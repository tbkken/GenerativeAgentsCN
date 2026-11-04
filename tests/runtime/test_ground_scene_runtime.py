"""The runtime consumes the online service, and its Run contains only GA-owned facts."""
import json
import math
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest

from generative_agents.ga_protocol.packages.io import seal_directory
from generative_agents.ga_replay.reader import ReplayReader
from generative_agents.ga_runtime.lifecycle.executor import RuntimeModelRegistry, execute_run_directory
from generative_agents.ga_runtime.lifecycle.service import RunService
from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder, SkillSource
from tests.runtime.test_object_skill_runtime import ToolModel
from tests.test_portable_package_protocol import _definition


@pytest.fixture
def provider(monkeypatch):
    state = {"online": True, "drop_move_reply": True, "receipts": {}, "calls": [], "move_count": 0,
             "known_runs": {}, "scene_revision": 1}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            _, version, kind, run, operation = self.path.split("/")
            assert (version, kind) == ("v1", "runtime")
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append((operation, data))
            if operation == "open" and "open_started" in state:
                state["open_started"].set()
                state["release_open"].wait(5)
            status = 200
            if not state["online"]:
                status, result = 503, {}
            elif operation == "open" and (
                (data["require_existing"] and run not in state["known_runs"]) or
                (data.get("origin_run_id") and state["known_runs"].get(data["origin_run_id"]) != state["scene_revision"])
            ):
                status, result = 409, {}
            elif operation == "open":
                state["known_runs"][run] = state["scene_revision"]
                state["position"] = data["committed_actors"].get("walker", {}).get("position", [0,0,0])
                result = {"run_id":run, "scene_id":"map-15", "actors":{"walker":{
                    "position":state["position"], "yaw_deg":0, "spawn_anchor":"pavement"}}}
            elif operation == "observe":
                result = {"actor_id":"walker", "position":state["position"], "nearby":[
                    {"id":"bench","name":"长椅","kind":"facility","position":state.get("bench_position",[4,0,0]),"interactable":True},
                    {"id":"outside","name":"不可见设备","kind":"facility","position":[1000,0,0],"interactable":True}],
                    "unrelated_private_asset":"opaque-provider-map-contents"}
            elif operation == "navigate":
                result = {"reachable":True,"target":[4,0,0],"path":[[4,0,0]]}
            elif operation == "act":
                key = run, data["step_no"], data["actor_id"]
                action = data["action"]
                if action["kind"] == "INTERACT" and (state.get("reject_interact")
                        or math.dist(state["position"],state.get("bench_position",[4,0,0])) > 0.6):
                    status, result = 422, {}
                elif key in state["receipts"]:
                    original, result = state["receipts"][key]
                    assert original == action
                elif action["kind"] == "MOVE":
                    result = {"position":[4,0,0], "from_position":[0,0,0],
                              "path":[[0,0,0],[2,0.5,0],[4,0,0]], "from_yaw_deg":0, "yaw_deg":0}
                    state["move_count"] += 1
                else:
                    assert action["kind"] == "INTERACT"
                    result = {"target_id":action["target_id"], "request_id":action["request_id"],
                              "accepted":True,"interacted":True}
                    if state.get("visibility_receipt"):
                        result["state_change"] = {"before":{"visible":True}, "after":{"visible":False}}
                state["receipts"][key] = (action, result)
                state["position"] = result.get("position", state["position"])
                if action["kind"] == "MOVE" and state.pop("drop_move_reply", False):
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
            else:
                pytest.fail(f"unsupported provider request {operation}")
            result["unrelated_private_asset"] = "opaque-provider-map-contents"
            payload = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Length",str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except ConnectionError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("GA_SIMWORLD_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def experiment(tmp_path, steps):
    definition = _definition()
    definition["world"]["definition"] = {"environment_type":"spatial_3d","simworld_scene_id":"map-15"}
    definition["agents"] = [{"agent_key":"walker","name":"Walker","scratch":{"age":26},
        "spawn_anchor":"auto","yaw_deg":0,"perception":{"vision_radius":12}}]
    definition["simulation"].update(max_steps=steps, stride_minutes=1)
    skill = tmp_path/"SKILL.md"
    skill.write_text("---\nname: test-brain\ndescription: 观察、移动、交互\n---\n用 @world-perceive 感知、@world-navigate 寻路、@world-act 行动。",
                     encoding="utf8")
    directory = ExperimentPackageBuilder().build_directory(tmp_path/"experiment", definition=definition,
        skills=[SkillSource("test-brain","brain",skill)],brain_skill="test-brain")
    return seal_directory(directory,tmp_path/"scene.gaexp")


def test_remote_action_lost_reply_seal_resume_and_rerun(tmp_path, provider, monkeypatch):
    service = RunService()
    root = service.create(experiment(tmp_path, 2), tmp_path/"run")
    model_steps = []
    def choose(messages, options):
        step = options["step_no"]
        model_steps.append(step)
        if step == 1:
            return "world-act", {"action_type":"MOVE","target_node_id":"bench"}
        return "world-act", {"action_type":"INTERACT","selection_key":"bench/interact"}

    monkeypatch.setattr(RuntimeModelRegistry,"get",lambda *_: ToolModel(choose))
    interrupted = execute_run_directory(root)
    assert interrupted.status.value == "PAUSED" and interrupted.committed_step == 0
    assert provider["move_count"] == 1 and not list((root/"frames").glob("step-*.json.gz"))
    previous_revision = provider["known_runs"].pop(interrupted.run_id)
    missing = service.resume(root)
    assert missing.status.value == "PAUSED" and missing.active_attempt_id == interrupted.active_attempt_id
    assert "核对" in missing.reason and provider["move_count"] == 1
    provider["known_runs"][interrupted.run_id] = previous_revision
    archive = service.seal(root, tmp_path/"paused.garun")
    resumed = tmp_path/"resumed"
    final = service.resume(archive, extracted_destination=resumed)
    assert final.status.value == "COMPLETED" and final.run_id == interrupted.run_id
    assert final.active_attempt_id != interrupted.active_attempt_id
    assert [b["require_existing"] for op,b in provider["calls"] if op=="open"] == [False,True,True]
    assert provider["move_count"] == 1 and model_steps == [1,2]
    assert not (resumed/"simworld-pending-step.json").exists()
    with ReplayReader(resumed) as replay:
        first, second = replay.iter_steps()
        assert replay.state_at(2)["object_states"] == {}
    assert first["agents"][0]["path"] == [[0,0,0],[2,0.5,0],[4,0,0]]
    interactions = [event for event in second["domain_events"] if event["event_type"]=="GAME_OBJECT_INTERACTED"]
    assert len(interactions)==1
    fact = interactions[0]["payload"]["structured_payload"]["observation"]
    assert fact["object_key"]=="bench" and fact["interacted"] and fact["request_id"]
    assert "opaque-provider-map-contents" not in json.dumps([first,second])
    assert all(b"opaque-provider-map-contents" not in p.read_bytes() for p in resumed.rglob("*") if p.is_file())
    rerun = service.rerun(resumed, tmp_path/"new", requested_steps=1)
    assert rerun.run_id != final.run_id and provider["move_count"] == 2
    assert [body for op,body in provider["calls"] if op=="open"][-1]["origin_run_id"] == final.run_id
    provider["scene_revision"] = 2
    changed = service.rerun(resumed, tmp_path/"changed", requested_steps=1)
    assert changed.status.value == "PAUSED" and changed.active_attempt_id is None
    assert provider["move_count"] == 2


def test_service_unavailable_before_start_does_not_create_empty_attempts(tmp_path, provider, monkeypatch):
    service = RunService()
    root = service.create(experiment(tmp_path,1),tmp_path/"run")
    provider["online"] = False
    for _ in range(2):
        result = service.resume(root)
        assert result.status.value == "PAUSED" and result.committed_step == 0
        assert not list((root/"attempts").glob("*/attempt.json"))
    provider["online"] = True
    monkeypatch.setattr(RuntimeModelRegistry,"get",lambda *_: ToolModel(lambda *_:
        ("world-act",{"action_type":"WAIT","wait_reason":"正在等候"})))
    assert service.resume(root).status.value == "COMPLETED"


def test_remote_resume_from_checkpoint_and_refused_interaction(tmp_path, provider, monkeypatch):
    service = RunService()
    root = service.create(experiment(tmp_path,3),tmp_path/"run")
    calls = []
    def choose(messages, options):
        calls.append(options["step_no"])
        if options["step_no"] == 1:
            return "world-act", {"action_type":"WAIT","wait_reason":"等待来人"}
        if options["step_no"] == 2:
            return "world-act", {"action_type":"MOVE","target_node_id":"bench"}
        return "world-act", {"action_type":"INTERACT","selection_key":"bench/interact"}
    monkeypatch.setattr(RuntimeModelRegistry,"get",lambda *_: ToolModel(choose))
    first = execute_run_directory(root)
    assert first.status.value == "PAUSED" and first.committed_step == 1
    provider["reject_interact"] = True
    final = service.resume(root)
    assert final.status.value == "COMPLETED" and final.committed_step == 3
    resumed_request = [body for op,body in provider["calls"] if op=="open"][-1]
    assert resumed_request["committed_step"] == 1
    assert resumed_request["committed_actors"]["walker"]["position"] == [0,0,0]
    assert provider["move_count"] == 1 and calls == [1,2,3]
    with ReplayReader(root) as replay:
        step = replay.read_step(3)
    events = step["domain_events"]
    assert not any(e["event_type"] == "GAME_OBJECT_INTERACTED" for e in events)
    waited = next(e for e in events if e["event_type"]=="AGENT_WAITED")
    args = waited["payload"]["structured_payload"]["arguments"]
    assert args["rejected_action_type"] == "INTERACT" and "SimWorld" in args["wait_reason"]


def test_temporary_skill_trial_does_not_open_remote_world(tmp_path, provider, monkeypatch):
    from generative_agents.ga_protocol.packages.io import atomic_write_json, extract_archive, read_json
    from generative_agents.ga_protocol.schemas.trials import SkillTrialError
    from generative_agents.ga_runtime.skills.trial import execute_skill_trial
    from tests.runtime.test_skill_trial_scene import trial

    workspace = trial(tmp_path, scene=False, body="请调用 @world-perceive。")
    extract_archive(experiment(tmp_path,1), workspace/"experiment")
    request = read_json(workspace/"trial.json")
    atomic_write_json(workspace/"trial.json", {**request, "agent_key":"walker", "experiment_directory":"experiment"})
    monkeypatch.setattr("generative_agents.ga_runtime.skills.trial.create_llm_model",
                        lambda *a, **kw: pytest.fail("An online context must be checked before starting a model"))
    with pytest.raises(SkillTrialError) as exc:
        execute_skill_trial(workspace)
    assert exc.value.code == "SKILL_REQUIRES_LIVE_3D_RUN"
    assert "map-15" in str(exc.value) and "world-perceive" in str(exc.value)
    assert not provider["calls"]


def test_cancel_interrupts_cold_scene_open_before_creating_an_attempt(tmp_path, provider):
    from concurrent.futures import ThreadPoolExecutor

    service = RunService()
    root = service.create(experiment(tmp_path,1),tmp_path/"run")
    provider.update(open_started=Event(),release_open=Event())
    with ThreadPoolExecutor() as worker:
        future = worker.submit(execute_run_directory, root)
        try:
            assert provider["open_started"].wait(3)
            service.request_cancel(root)
            cancelled = future.result(timeout=2)
        finally:
            provider["release_open"].set()
    assert cancelled.status.value == "CANCELLED" and cancelled.committed_step == 0
    assert not list((root/"attempts").glob("*/attempt.json"))


def test_only_confirmed_facility_change_is_projected_and_restored(tmp_path, provider, monkeypatch):
    service = RunService()
    root = service.create(experiment(tmp_path,2),tmp_path/"run")
    provider["visibility_receipt"] = True
    provider["bench_position"] = [0.4,0,0]
    def choose(messages, options):
        if options["step_no"] == 1:
            service.request_pause(root)
            return "world-act", {"action_type":"INTERACT","selection_key":"bench/interact"}
        return "world-act", {"action_type":"WAIT","wait_reason":"查看设施"}

    monkeypatch.setattr(RuntimeModelRegistry,"get",lambda *_: ToolModel(choose))
    assert execute_run_directory(root).committed_step == 1
    with ReplayReader(root) as replay:
        assert replay.state_at(1)["object_states"] == {"bench":{"visible":False}}
        events = replay.read_step(1)["domain_events"]
    changed = next(e["payload"]["structured_payload"] for e in events if e["event_type"]=="GAME_OBJECT_STATE_CHANGED")
    contacted = next(e["payload"]["structured_payload"] for e in events if e["event_type"]=="GAME_OBJECT_INTERACTED")
    assert changed["request_id"] == contacted["observation"]["request_id"]
    assert changed["before"] == {"visible":True} and changed["after"] == {"visible":False}
    assert service.resume(root).status.value == "COMPLETED"
    assert [b for op,b in provider["calls"] if op=="open"][-1]["committed_objects"] == {"bench":{"visible":False}}
