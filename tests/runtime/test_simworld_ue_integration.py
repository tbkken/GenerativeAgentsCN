"""Opt-in physical gate: set GA_TEST_UE_EXE and GA_TEST_SIMWORLD_PAK_DIR on a GPU host."""
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

import pytest

from generative_agents.ga_replay.reader import ReplayReader
from generative_agents.ga_runtime.lifecycle.executor import RuntimeModelRegistry, execute_run_directory
from generative_agents.ga_runtime.lifecycle.service import RunService
from tests.runtime.test_ground_scene_runtime import experiment
from tests.runtime.test_object_skill_runtime import ToolModel


@contextmanager
def isolated_provider(directory):
    from ga_scene.builds import AvatarBuilds
    from ga_scene.host import UEHost
    from ga_scene.preview import Preview
    from ga_scene.server import make_server

    host = UEHost(os.environ["GA_TEST_UE_EXE"])
    preview = Preview(managed=host, builds=AvatarBuilds(None, directory/"cache"))
    server = make_server("127.0.0.1",0,preview, map_dirs=[Path(os.environ["GA_TEST_SIMWORLD_PAK_DIR"])],
                         world_root=directory/"provider-owned-state")
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
        preview.disconnect()
        host.stop()


@pytest.mark.skipif(not (os.environ.get("GA_TEST_UE_EXE") and os.environ.get("GA_TEST_SIMWORLD_PAK_DIR")),
                    reason="requires the installed 3D service, GPU, SimWorld Base and Tokyo PAK")
def test_live_tokyo_walk_then_restore_a_sealed_run_after_disconnect(tmp_path, monkeypatch):
    service = RunService()
    root = service.create(experiment(tmp_path,2), tmp_path/"run")

    def choose(messages, options):
        if options["step_no"] == 2:
            return "world-act", {"action_type":"WAIT","wait_reason":"核对已恢复的实际位置"}
        results = [m for m in messages if m.get("role")=="tool"]
        if not results:
            return "world-perceive", {"radius_m":3}
        position = json.loads(results[-1]["content"])["current"]["coord"]
        # Tokyo's short verified sidewalk runs north/south. Choose a point from the actual spawn.
        x,y,z = position
        service.request_pause(root)
        return "world-act", {"action_type":"MOVE","target_coord":[x, y+(1.2 if y < -5 else -1.2), z]}

    monkeypatch.setattr(RuntimeModelRegistry,"get",lambda *_: ToolModel(choose))
    with isolated_provider(tmp_path) as url:
        monkeypatch.setenv("GA_SIMWORLD_URL",url)
        first = execute_run_directory(root)
    assert first.status.value == "PAUSED" and first.committed_step == 1
    offline = service.resume(root)
    assert offline.status.value == "PAUSED" and offline.active_attempt_id == first.active_attempt_id
    assert offline.committed_step == 1 and "SimWorld" in offline.reason
    archive = service.seal(root,tmp_path/"paused.garun")
    with isolated_provider(tmp_path) as restored_url:
        monkeypatch.setenv("GA_SIMWORLD_URL",restored_url)
        final = service.resume(archive,extracted_destination=tmp_path/"resumed")
    assert final.status.value == "COMPLETED" and (final.run_id, final.committed_step) == (first.run_id, 2)
    assert final.active_attempt_id != first.active_attempt_id
    with ReplayReader(tmp_path/"resumed") as replay:
        start, finish = replay.iter_steps()
    actor = start["agents"][0]
    assert math.dist(actor["from_coord"][:2],actor["to_coord"][:2]) > 0.25
    assert len(actor["path"]) > 1 and actor["path"][-1] == actor["to_coord"]
    assert actor["to_coord"] == finish["agents"][0]["from_coord"]
