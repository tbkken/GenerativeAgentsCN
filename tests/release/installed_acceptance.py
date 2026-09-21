"""Installed-wheel acceptance with disposable Studio and a deterministic HTTP model.

Executed by tools/verify_wheel.py, outside the repository. The model substitutes
only the external provider; CLI, Studio, MCP, checkpoints and Replay are real.
"""

from __future__ import annotations

import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import threading

from fastapi.testclient import TestClient

import generative_agents
from generative_agents.ga_protocol import read_json
from generative_agents.ga_studio.web import create_studio_app


def main(installed: Path, root: Path) -> None:
    assert Path(generative_agents.__file__).resolve().is_relative_to(installed)
    root.mkdir()
    executables = list(installed.rglob("ga.exe" if sys.platform == "win32" else "ga"))
    assert len(executables) == 1, executables
    executable = executables[0]
    commands = []

    def cli(*args):
        completed = subprocess.run([str(executable), *map(str, args)], cwd=root,
                                   capture_output=True, text=True, encoding="utf-8", timeout=90)
        assert completed.returncode == 0, (args, completed.stdout, completed.stderr)
        commands.append(" ".join(map(str, args[:2])))
        return json.loads(completed.stdout)

    contexts = []
    failures = []
    pause_once = threading.Event()
    run = root / "first-run"

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.endswith("/embeddings"):
                    inputs = body["input"]
                    inputs = inputs if isinstance(inputs, list) else [inputs]
                    response = {"data": [{"index": i, "embedding": [1.0, 0.0, 0.0]}
                                         for i, _ in enumerate(inputs)]}
                else:
                    text = body["messages"][1]["content"]
                    context = json.loads(text[text.index("{"):])["IterationContext"]
                    assert any(tool["function"]["name"] == "world-act" for tool in body.get("tools", []))
                    contexts.append(context)
                    step = context["step"]["number"]
                    if "game_object" in context:
                        arguments = {"action_type": "SET_OBJECT_STATE", "state_patch": {"state": f"S{step}"},
                                     "idempotency_key": f"switch:{step}",
                                     "responses": [{"request_id": request["request_id"], "message": "已收到请求"}
                                                   for request in context["variables"]["interaction_requests"]]}
                        if not pause_once.is_set():
                            pause_once.set()
                            cli("run", "pause", run)
                    elif step == 1:
                        arguments = {"action_type": "INTERACT", "selection_key": "desk/interact", "request": "请确认"}
                    else:
                        arguments = {"action_type": "WAIT"}
                    response = {"id": f"acceptance-{len(contexts)}", "object": "chat.completion",
                                "created": 1, "model": "acceptance-model", "choices": [{"index": 0,
                                "finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                                "tool_calls": [{"id": f"call-{len(contexts)}", "type": "function",
                                "function": {"name": "world-act", "arguments": json.dumps(arguments)}}]}}],
                                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
                payload = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:
                failures.append(repr(exc))
                self.send_error(500, "acceptance fixture failed")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1"
        var = root / "studio"
        app = create_studio_app(database_url=f"sqlite:///{(var / 'studio.db').as_posix()}", var_dir=var)
        with TestClient(app) as client:
            def api(method, path, **kwargs):
                response = client.request(method, "/api/studio" + path, **kwargs)
                assert response.is_success, (path, response.status_code, response.text)
                return response.json() if response.content else None

            assert api("GET", "/health")["runtime_truth"] == "files"
            assert client.get("/").status_code == 200
            for retired in ("/static/console/commute-demo.html", "/static/console/map-configuration-demo.html", "/api/v1/experiments"):
                assert client.get(retired).status_code == 404
            skills = {}
            for name, kind in (("acceptance-brain", "brain"), ("acceptance-object", "atomic")):
                item = api("POST", "/resources/skills", json={"name": name, "kind": kind, "description": "验收技能"})
                item = api("PUT", f"/resources/skills/{name}", json={"markdown": item["markdown"] + "\n调用 `world-act` 执行本轮动作。\n"})
                skills[name] = item
            public_map = api("POST", "/resources/maps", json={"name": "验收地图", "width": 2, "height": 1})
            nodes = []
            parent = None
            for key, kind in (("world", "WORLD"), ("sector", "SECTOR"), ("room", "ARENA"), ("desk", "GAME_OBJECT")):
                node = {"id": key, "name": key, "kind": kind, "parent_id": parent,
                        "bounds": {"x": 0, "y": 0, "width": 2 if kind != "GAME_OBJECT" else 1, "height": 1}}
                if kind == "GAME_OBJECT":
                    node.update(skill_bindings=[{"skill_name": "acceptance-object", "vision_radius": 2}],
                                initial_state={"state": "S0"})
                nodes.append(node)
                parent = key
            world = {"world_key": "acceptance-world", "world_name": "验收地图", "assets": [], "definition": {
                "world": "world", "size": [1, 2], "size_unit": "TILE", "tile_size": 32,
                "tile_address_keys": ["world", "sector", "arena", "game_object"],
                "editor_v2": {"root_node_id": "world", "hierarchy_nodes": nodes},
                "tiles": [{"coord": [x, 0], "collision": False,
                           "address": ["world", "sector", "room"] + (["desk"] if x == 0 else [])} for x in range(2)]}}
            api("PUT", f"/resources/maps/{public_map['id']}", json={"row_version": public_map["row_version"], "world": world})
            agent = api("POST", "/resources/agents", json={"definition": {
                "agent_key": "acceptance-agent", "name": "验收人物", "scratch": {"age": 30}}})
            definition = copy.deepcopy(agent["definition"])
            definition["name"] = "已编辑人物"
            agent = api("PUT", f"/resources/agents/{agent['id']}", json={"definition": definition, "row_version": agent["row_version"]})
            models = api("POST", "/resources/model-presets", json={"name": "HTTP 验收替身", "config": {
                "chat": {"provider": "vllm", "model": "acceptance-model", "base_url": endpoint},
                "embedding": {"provider": "openai_compatible", "model": "acceptance-embedding", "base_url": endpoint}}})
            created = api("POST", "/experiments", json={"name": "wheel 验收", "map_id": public_map["id"],
                "brain_skill_id": skills["acceptance-brain"]["resource_id"], "model_preset_id": models["id"],
                "agent_ids": [agent["id"]], "placements": [{"agent_id": agent["id"], "coord": [1, 0]}],
                "simulation": {"start_time": "2026-09-21T08:00:00+08:00", "stride_minutes": 1,
                               "max_steps": 3, "checkpoint_interval_steps": 1, "random_seed": 42}})
            experiment_id = created["experiment_id"]
            package = Path(created["location"])
            imported_hash = read_json(package / "integrity/sha256.json")["root_sha256"]
            api("PUT", "/resources/skills/acceptance-brain", json={"markdown": skills["acceptance-brain"]["markdown"] + "\n作者后续修改\n"})
            assert read_json(package / "integrity/sha256.json")["root_sha256"] == imported_hash
            cli("experiment", "seal", package, root / "cli-sealed.gaexp")
            sealed = api("POST", f"/experiments/{experiment_id}/seal")
            exchange = root / "renamed.gaexp"
            shutil.copyfile(sealed["location"], exchange)
            assert client.put(f"/api/studio/experiments/{experiment_id}/entrypoints/simulation",
                              json={"document": {}}).status_code >= 400
        # Removing the disposable author workspace proves Runtime needs only the package.
        shutil.rmtree(var)
        assert cli("experiment", "validate", exchange)["experiment_id"] == experiment_id
        paused = cli("run", "start", exchange, run, "--steps", 3)
        assert not failures, failures
        assert (paused["status"], paused["committed_step"]) == ("PAUSED", 1), paused
        frames_before = {path.name: path.read_bytes() for path in (run / "frames").glob("*.json.gz")}
        paused_archive = root / "renamed-paused.garun"
        cli("run", "seal", run, paused_archive)
        resumed = root / "resumed-elsewhere"
        completed = cli("run", "resume", paused_archive, "--destination", resumed)
        assert (completed["status"], completed["committed_step"]) == ("COMPLETED", 3), completed
        assert completed["run_id"] == paused["run_id"]
        assert completed["active_attempt_id"] != paused["active_attempt_id"]
        assert all((resumed / "frames" / name).read_bytes() == data for name, data in frames_before.items())
        agent_contexts = [c for c in contexts if "agent" in c]
        assert [c["step"]["number"] for c in agent_contexts] == [1, 2, 3], agent_contexts
        observations = [c["variables"]["external_observations"] for c in agent_contexts]
        assert list(map(len, observations)) == [0, 1, 0], {
            "observations": observations,
            "objects": [c["variables"] for c in contexts if "game_object" in c],
        }
        object_contexts = [c for c in contexts if "game_object" in c]
        assert [c["step"]["number"] for c in object_contexts] == [1, 2, 3]
        assert object_contexts[1]["variables"]["object_state"]["state"] == "S1"
        assert cli("run", "status", resumed)["run_id"] == paused["run_id"]
        archive = root / "final.garun"
        cli("run", "seal", resumed, archive)
        assert cli("replay", "summary", archive)["run_id"] == paused["run_id"]
        timeline = cli("replay", "timeline", archive)
        assert [f["step_no"] for f in timeline] == [1, 2, 3]
        events = [event for frame in timeline for event in frame["domain_events"]]
        assert sum(e["event_type"] == "GAME_OBJECT_SKILL_RESPONDED" for e in events) == 1
        assert sum(e["event_type"] == "GAME_OBJECT_STATE_CHANGED" for e in events) == 3
        for step in (1, 2, 3):
            assert cli("replay", "state", archive, step)["object_states"]["desk"]["state"] == f"S{step}"
        rerun = cli("run", "rerun", archive, root / "rerun", "--steps", 1)
        assert rerun["run_id"] != paused["run_id"] and rerun["status"] == "COMPLETED"
        created_run = root / "created-only"
        cli("run", "create", exchange, created_run, "--steps", 1)
        assert cli("run", "cancel", created_run)["cancel_requested"]
        assert read_json(created_run / "control.json")["cancel_requested"]
        assert not failures, failures
        print(json.dumps({"installed_acceptance": "passed", "commands": sorted(set(commands)),
                          "author_edit": True, "physical_import_and_seal": True,
                          "pause_resume_committed_steps": [1, 3], "object_reply_deliveries": [0, 1, 0],
                          "replay_steps": [1, 2, 3], "model": "deterministic local HTTP stub"}), flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
