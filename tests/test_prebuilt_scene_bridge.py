"""Online map sessions render GA facts while SimWorld owns the scene and actors."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from generative_agents.adapters.web.routes import scene_sessions, simworld
from generative_agents.ga_protocol.packages.io import atomic_write_json, read_json
from generative_agents.ga_runtime.lifecycle.service import RunService
from tests.committed_frames import write_frame
from tests.runtime.test_ground_scene_runtime import experiment


def test_scene_and_character_routes_share_configured_proxy_prefix_and_auth(tmp_path, monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, self.headers.get("Authorization")))
            payload = json.dumps({"maps": []} if self.path.endswith("/maps") else {"avatar_generation": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as upstream:
        config = tmp_path / "services.toml"
        config.write_text(f'[simworld]\nurl="http://127.0.0.1:{upstream.server_port}/edge"\n', encoding="utf-8")
        monkeypatch.setenv("GA_SERVICES_CONFIG", str(config))
        monkeypatch.setenv("GA_SIMWORLD_TOKEN", "test-secret")
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        monkeypatch.delenv("GA_SIMWORLD_URL", raising=False)
        monkeypatch.delenv("GA_SIMWORLD_PORT", raising=False)
        ctx = SimpleNamespace(database=None, asset_service=SimpleNamespace(store=None), workspaces=None)
        app = FastAPI()
        app.include_router(simworld.create_simworld_router())
        app.include_router(scene_sessions.create_scene_session_router(ctx))
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        try:
            with TestClient(app) as client:
                assert client.get("/simworld/capabilities").json() == {"avatar_generation": True}
                assert client.get("/simworld/scenes").json() == {"scenes": []}
        finally:
            upstream.shutdown()
            thread.join()
    assert received == [
        ("/edge/v1/capabilities", "Bearer test-secret"),
        ("/edge/v1/maps", "Bearer test-secret"),
    ]




CAMERA = {"position":[12.5,8.25,2.0],"yaw_deg":135,"pitch_deg":0,"fov_deg":90}


@pytest.fixture
def bridge(monkeypatch):
    calls = []
    ctx = SimpleNamespace()
    grant = {"replay":True}
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\nimage\r\n"
    def serve(request):
        method, path = request.method, request.url.path
        body = json.loads(request.content) if request.content else None
        calls.append((method,path,body))
        if (method,path)==("GET","/v1/maps"):
            return httpx.Response(200,json={"maps":[
                {"id":"map-15","name":"Tokyo","availability":"snapshot_ready","map_uri":"/Game/Tokyo"},
                {"id":"map-2","name":"Classroom","availability":"unverified"},
                {"id":"map-3","name":"Missing","availability":"missing"}]})
        if (method,path)==("POST","/v1/maps/map-15/participant-views"):
            assert body=={"actors":[{"agent_key":"walker","spawn_anchor":"auto"}]}
            return httpx.Response(200,json={"agent_views":[{"agent_key":"walker","pose":CAMERA}]})
        if (method,path)==("POST","/v1/sessions"):
            result={"session_id":"owned","state":"starting"}
            if "replay" in body and grant["replay"]:
                result["replay"]={"run_id":body["replay"]["run_id"]}
            return httpx.Response(201,json=result)
        if (method,path)==("GET","/v1/sessions/owned/state"):
            return httpx.Response(200,json={"state":"ready"})
        if (method,path)==("GET","/v1/sessions/owned/stream"):
            return httpx.Response(200,stream=Stream(),headers={"Content-Type":"multipart/x-mixed-replace; boundary=frame"})
        if (method,path)==("GET","/v1/sessions/owned/frame"):
            if request.url.params.get("after_frame_seq")=="19":
                return httpx.Response(204)
            return httpx.Response(200,content=b"image",headers={"Content-Type":"image/jpeg","X-Replay-Seq":"3",
                "X-Replay-Step":"2","X-Frame-Seq":"19","X-Replay-Settled":"1"})
        if method=="POST" and path in {"/v1/sessions/owned/pose","/v1/sessions/owned/preset","/v1/sessions/owned/replay"}:
            return httpx.Response(200,json={"state":"ready"})
        if (method,path)==("DELETE","/v1/sessions/owned"):
            return httpx.Response(204)
        raise AssertionError(f"unexpected upstream request: {method} {path}")
    monkeypatch.setattr(scene_sessions,"_connection",lambda:{
        "base_url":"http://ue/v1/","transport":httpx.MockTransport(serve)})
    app=FastAPI()
    app.include_router(scene_sessions.create_scene_session_router(ctx))
    with TestClient(app) as client:
        yield client, calls, ctx, grant


def test_online_catalog_only_returns_names_and_preview_availability(bridge):
    client,calls,*_=bridge
    rows=client.get("/simworld/scenes").json()["scenes"]
    assert [(row["id"],row["previewable"]) for row in rows]==[("map-15",True),("map-2",True),("map-3",False)]
    assert all(set(row)=={"id","name","availability","previewable"} for row in rows)
    assert client.post("/simworld/scenes/map-15/import").status_code==404
    assert client.get("/simworld/scenes/map-15/thumbnail").status_code==404
    assert calls==[("GET","/v1/maps",None)]


@pytest.mark.parametrize(("kind","owner"),[("scenes","map-2"),("experiments","draft")])
def test_public_and_experiment_cameras_are_bound_to_their_remote_scene_owner(bridge,kind,owner):
    client,calls,ctx,_=bridge
    ctx.workspaces=SimpleNamespace(simworld_scene_selection=lambda exp:
        {"draft": ("map-15", [{"agent_key": "walker", "spawn_anchor": "auto"}])}[exp])
    base=f"/simworld/{kind}/{owner}/sessions"
    result=client.post(base,json={})
    assert result.status_code==201
    scene_id="map-15" if kind=="experiments" else owner
    assert result.json()["scene_id"]==scene_id
    assert calls[-1]==("POST","/v1/sessions",{"map_id":scene_id})
    assert client.get(base+"/owned/state").json()=={"state":"ready"}
    assert b"image" in client.get(base+"/owned/stream").content
    assert client.post(base+"/owned/pose",json=CAMERA).status_code==200
    assert client.post(base+"/owned/preset",json={"id":"start"}).status_code==200
    if kind=="experiments":
        chosen=client.post(base+"/owned/agent-view",json={"agent_key":"walker"})
        assert chosen.json()["agent_view"]=={"agent_key":"walker","pose":CAMERA}
        assert client.post(base+"/owned/agent-view",json={"agent_key":"absent"}).status_code==404
    before=len(calls)
    for other in (base.replace(owner,"different"),f"/simworld/runs/{owner}/sessions"):
        assert client.get(other+"/owned/state").status_code==404
        assert client.post(other+"/owned/close").status_code==404
        assert client.get(other+"/owned/snapshot.jpg").status_code==404
    assert len(calls)==before
    assert client.post(base+"/owned/close").is_success
    assert calls[-1]==("DELETE","/v1/sessions/owned",None)
    assert client.get(base+"/owned/state").status_code==404


@pytest.fixture
def committed_run(tmp_path):
    root=RunService().create(experiment(tmp_path,3),tmp_path/"run")
    run_id=read_json(root/"run.json")["run_id"]
    attempt=str(uuid4())
    for n in (1,2,3):
        write_frame(root,{"run_id":run_id,"attempt_id":attempt,"step_no":n,"virtual_time":f"2026-11-04T13:0{n}:00+08:00",
            "agents":[{"agent_key":"walker","from_coord":[n-1,0,0],"to_coord":[n,0,0],
                       "from_yaw_deg":0,"to_yaw_deg":90,"path":[[n,0,0]],"path_source":"OBSERVED"}],
            "domain_events":[{"event_type":"GAME_OBJECT_STATE_CHANGED","payload":{"structured_payload":{
                "object_key":"lamp","after":{"state":"on" if n==2 else "off"}}}}],"effects":[],"conversations":[]})
    status=read_json(root/"status.json")
    atomic_write_json(root/"status.json",{**status,"committed_step":2})
    return root,run_id,attempt


def test_run_seek_reduces_only_checked_commits_and_forwards_facilities_at_the_selected_step(bridge,committed_run):
    client,calls,ctx,_=bridge
    root,run_id,attempt=committed_run
    ctx.run_location=lambda owner: {run_id:root}[owner]
    base=f"/simworld/runs/{run_id}/sessions"
    size={"width":640,"height":360}
    result=client.post(base,json={"capture_size":size})
    assert result.status_code==201, result.text
    assert calls[-1]==("POST","/v1/sessions",{"map_id":"map-15","replay":{"run_id":run_id,
        "participants":{"walker":None},"capture_size":size}})
    endpoint=base+"/owned/replay"
    before=len(calls)
    assert client.post(endpoint,json={"step_no":3}).status_code==422
    assert client.post(endpoint,json={"step_no":1,"object_states":{"lamp":{"state":"forged"}}}).status_code==422
    assert client.post(endpoint.replace(run_id,"someone-else"),json={"step_no":1}).status_code==404
    assert len(calls)==before
    for n in (2,1,2):
        assert client.post(endpoint,json={"step_no":n,"playback_ms":800}).is_success
        assert calls[-1]==("POST","/v1/sessions/owned/replay",{"run_id":run_id,"step_no":n,"attempt_id":attempt,
            "playback_ms":800,"actors":[{"id":"walker","from_position":[n-1,0,0],"position":[n,0,0],
                "from_yaw_deg":0,"yaw_deg":90,"path":[[n,0,0]],"clip":"walk"}],
            "object_states":{"lamp":{"state":"on" if n==2 else "off"}}})
    snapshot=base+"/owned/snapshot.jpg?replay_seq=3"
    assert client.get(snapshot).headers["X-Replay-Step"]=="2"
    assert client.get(snapshot+"&after_frame_seq=19").status_code==204
    assert client.get(base+"/owned/snapshot.jpg?replay_seq=1").status_code==502
    # A corrupt published frame cannot drive a later seek.
    (root/"frames/step-000001.json.gz").write_bytes(b"modified")
    before=len(calls)
    assert client.post(endpoint,json={"step_no":2}).status_code==422
    assert len(calls)==before
    assert client.post(base+"/owned/close").is_success


def test_run_session_not_confirmed_by_simworld_is_released(bridge,committed_run):
    client,calls,ctx,grant=bridge
    root,run_id,_=committed_run
    grant["replay"]=False
    ctx.run_location=lambda owner: {run_id:root}[owner]
    result=client.post(f"/simworld/runs/{run_id}/sessions")
    assert result.status_code==502
    assert calls[-1]==("DELETE","/v1/sessions/owned",None)


def test_replay_animation_uses_the_committed_clip_or_observed_motion():
    fact = {"agent_key":"rider","from_coord":[0,0,0],"to_coord":[1,0,0],"path":[[1,0,0]],
            "from_yaw_deg":0,"to_yaw_deg":0,"action":{"description":"骑车"}}
    assert scene_sessions._actor_pose(fact)["clip"] == "walk"
    fact["action"]["presentation_clip"] = "cycle"
    assert scene_sessions._actor_pose(fact)["clip"] == "cycle"
    fact.update(from_coord=[1,0,0],path=[],action={"description":"查看手机"})
    assert scene_sessions._actor_pose(fact)["clip"] == "idle"
    fact["action"]["presentation_clip"] = "phone"
    assert scene_sessions._actor_pose(fact)["clip"] == "phone"
