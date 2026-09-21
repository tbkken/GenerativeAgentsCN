"""Regressions for current shared kernel and Studio surfaces."""

from __future__ import annotations
import random
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from fastapi.testclient import TestClient
from generative_agents.config import ExperimentDefinition
from generative_agents.config.schema import make_blank_definition
from generative_agents.modules import memory as memory_module
from generative_agents.modules.config_adapter import ConfigAdapter
from generative_agents.modules.game import Game
from generative_agents.runtime.algorithm import get_algorithm_profile
from generative_agents.runtime.context import RunControl, RunPaths, SimulationClock
from tests.studio_support import create_test_studio

ROOT = Path(__file__).resolve().parents[2]


CONSOLE_HTML = ROOT / "generative_agents" / "web" / "static" / "experiment-console.html"


CONSOLE_JS = ROOT / "generative_agents" / "web" / "static" / "console-api.js"


def _definition(key: str) -> ExperimentDefinition:
    """为本测试模块封装 ``_definition`` 辅助步骤，减少重复的场景搭建代码。"""
    definition = make_blank_definition(key=key, name=f"Experiment {key}")
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["models"]["chat"]["resolved_model"] = "Qwen/test-chat"
    payload["models"]["embedding"]["resolved_model"] = "test-embedding"
    payload["world"]["definition"] = {
        "world": "test",
        "tile_size": 16,
        "size": [1, 2],
        "map": [[0, 0]],
        "camera": [0, 0],
        "tile_address_keys": ["world", "sector", "arena", "game_object"],
        "tiles": [
            {"coord": [0, 0], "collision": False, "address": ["home", "bedroom", "bed"]},
            {"coord": [1, 0], "collision": False, "address": ["home", "bedroom", "bed"]},
        ],
    }
    payload["agents"] = [
        {
            "agent_key": "test-agent",
            "enabled": True,
            "name": "Test Agent",
            "portrait_asset": None,
            "coord": [0, 0],
            "currently": "testing",
            "scratch": {
                "age": 30,
                "innate": "careful",
                "learned": "tests systems",
                "lifestyle": "repeatable",
                "daily_plan": "",
            },
            "spatial": {
                "address": {
                    "living_area": ["test", "home", "bedroom"],
                    "sleeping": ["test", "home", "bedroom", "bed"],
                },
                "tree": {"test": {"home": {"bedroom": ["bed"]}}},
            },
        }
    ]
    return ExperimentDefinition.model_validate(payload)


def test_def_031_runtime_thread_lock_is_not_deepcopied_into_agent(monkeypatch, tmp_path):
    """RunControl contains locks; construction must retain, not deepcopy, it."""

    captured = []

    class FakeAssociate:
        """测试替身 ``FakeAssociate``：记录调用并返回当前场景可控的结果。"""
        def __init__(self, _path, *_args, **kwargs):
            """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
            captured.append(kwargs)
            self.last_evicted = ()

        def to_dict(self):
            """为本测试模块封装 ``to_dict`` 辅助步骤，减少重复的场景搭建代码。"""
            return {"memory": {"event": [], "thought": [], "chat": []}}

    class Logger:
        """为 ``Logger`` 相关场景组织共享测试状态、输入或断言。"""
        def info(self, *_args, **_kwargs):
            """为本测试模块封装 ``info`` 辅助步骤，减少重复的场景搭建代码。"""
            pass

        debug = info
        warning = info

    monkeypatch.setattr(memory_module, "Associate", FakeAssociate)
    definition = _definition("thread-lock")
    config = ConfigAdapter().game_config(definition)
    config["storage_root"] = str(tmp_path / "attempt-storage")
    control = RunControl()
    run_id, attempt_id = uuid4(), uuid4()
    context = SimpleNamespace(
        run_id=run_id,
        attempt_id=attempt_id,
        clock=SimulationClock(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        random=random.Random(7),
        paths=RunPaths.under(tmp_path, run_id),
        skills={},
        models=None,
        metadata={},
        logger=Logger(),
        control=control,
        algorithm=get_algorithm_profile("ga-cn-v1"),
    )

    game = Game(config, {}, context=context)

    assert game.get_agent("test-agent").associate is not None
    assert captured[0]["embedding"]["_control"] is control


def test_def_036_agent_modal_has_scrollable_body_and_reachable_footer():
    """回归验证 ``test_def_036_agent_modal_has_scrollable_body_and_reachable_footer`` 所描述的业务结果、故障边界和隔离约束。"""
    html = CONSOLE_HTML.read_text(encoding="utf-8")
    assert re.search(r"\.modal\s*\{[^}]*max-height:\s*calc\(100vh\s*-\s*40px\)", html)
    assert re.search(r"\.modal-body\s*\{[^}]*overflow-y:\s*auto", html)
    modal = html[html.index('id="agentEditorModal"'):]
    assert modal.index('class="modal-body"') < modal.index('class="modal-foot"')


def test_def_036_agent_modal_traps_focus_and_restores_the_trigger():
    """回归验证 ``test_def_036_agent_modal_traps_focus_and_restores_the_trigger`` 所描述的业务结果、故障边界和隔离约束。"""
    html = (
        ROOT / "generative_agents" / "web" / "static" / "experiment-console.html"
    ).read_text(encoding="utf-8")
    source = CONSOLE_JS.read_text(encoding="utf-8")
    modal_start = html.index('id="agentEditorModal"')
    modal_opening = html[modal_start : html.index(">", modal_start) + 1]
    assert 'role="dialog"' in modal_opening and 'aria-modal="true"' in modal_opening

    open_editor = source[
        source.index("function openAgentEditor") : source.index(
            "async function saveAgentEditor"
        )
    ]
    assert "openModal('agentEditorModal'" in open_editor
    open_modal = source[
        source.index("function openModal") : source.index("function closeModal")
    ]
    close_modal = source[
        source.index("function closeModal") : source.index("function handleModalKeydown")
    ]
    keydown = source[
        source.index("function handleModalKeydown") : source.index(
            "function openPublishModal"
        )
    ]
    assert "document.activeElement" in open_modal
    assert "state.modalReturnFocus" in open_modal
    assert "setBackgroundInert(true)" in open_modal and ".focus(" in open_modal
    assert "setBackgroundInert(false)" in close_modal
    assert "returnFocus.focus(" in close_modal
    assert ".inert" in source, "background remains keyboard-reachable while modal is open"
    assert "$('agentEditorModal').addEventListener('keydown'" in source
    assert "event.key === 'Tab'" in keydown and "event.shiftKey" in keydown
    assert "event.key === 'Escape'" in keydown


def test_def_036_modal_focus_explicitly_advances_middle_items():
    """Focus movement must not depend on a browser's native Tab default action."""

    node = shutil.which("node")
    assert node, "Node.js is required for the executable modal focus contract"
    focus_module = (
        ROOT / "generative_agents" / "web" / "static" / "modal-focus.js"
    )
    program = r"""
const { tabTarget } = require(process.argv[1]);
const first = { id: 'first' };
const middle = { id: 'middle' };
const last = { id: 'last' };
const focusables = [first, middle, last];
const actual = {
  firstForward: tabTarget(focusables, first, false)?.id,
  middleForward: tabTarget(focusables, middle, false)?.id,
  lastForward: tabTarget(focusables, last, false)?.id,
  lastBackward: tabTarget(focusables, last, true)?.id,
  middleBackward: tabTarget(focusables, middle, true)?.id,
  firstBackward: tabTarget(focusables, first, true)?.id,
  outsideForward: tabTarget(focusables, {}, false)?.id,
  outsideBackward: tabTarget(focusables, {}, true)?.id,
};
const expected = {
  firstForward: 'middle', middleForward: 'last', lastForward: 'first',
  lastBackward: 'middle', middleBackward: 'first', firstBackward: 'last',
  outsideForward: 'first', outsideBackward: 'last',
};
if (JSON.stringify(actual) !== JSON.stringify(expected)) {
  console.error(JSON.stringify({ actual, expected }));
  process.exit(1);
}
"""
    result = subprocess.run(
        [node, "-e", program, str(focus_module)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_def_037_invalid_asset_upload_is_a_422_error_envelope(tmp_path):
    """回归验证 ``test_def_037_invalid_asset_upload_is_a_422_error_envelope`` 所描述的业务结果、故障边界和隔离约束。"""
    database_url = "sqlite:///" + (tmp_path / "asset-http.db").as_posix()
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        response = client.post(
            "/api/studio/resources/assets",
            files={"file": ("malformed.json", b"not-json-or-image", "application/json")},
        )
    assert response.status_code == 422
    assert response.json()["detail"]


def test_def_038_production_result_shell_contains_no_real_looking_demo_facts(tmp_path):
    """回归验证 ``test_def_038_production_result_shell_contains_no_real_looking_demo_facts`` 所描述的业务结果、故障边界和隔离约束。"""
    database_url = "sqlite:///" + (tmp_path / "shell.db").as_posix()
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        html = client.get("/").text
    forbidden = (
        "run_0109",
        "cfg_8f3a2c1",
        "共 3,842 条",
        "情人节社会传播实验",
        "Isabella Rodriguez",
    )
    assert not [value for value in forbidden if value in html]


def test_def_039_production_experiment_badges_are_not_hardcoded(tmp_path):
    """回归验证 ``test_def_039_production_experiment_badges_are_not_hardcoded`` 所描述的业务结果、故障边界和隔离约束。"""
    database_url = "sqlite:///" + (tmp_path / "badges.db").as_posix()
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        html = client.get("/").text
    assert '<span class="nav-count" id="navRunCount">3</span>' not in html
    assert 'class="experiment-card" data-status=' not in html


def test_def_040_production_shell_does_not_ship_prototype_event_listeners(tmp_path):
    """回归验证 ``test_def_040_production_shell_does_not_ship_prototype_event_listeners`` 所描述的业务结果、故障边界和隔离约束。"""
    database_url = "sqlite:///" + (tmp_path / "listeners.db").as_posix()
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        html = client.get("/").text
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, flags=re.DOTALL)
    assert not [script for script in scripts if script.strip()], (
        "inline prototype state/listeners execute beside console-api.js"
    )
    assert html.count('/static/console/console-api.js') == 1


def test_def_045_console_api_owns_every_required_ui_global():
    """The production bundle must not depend on globals from the removed prototype."""

    source = CONSOLE_JS.read_text(encoding="utf-8")
    state_replacements = {
        "currentExperiment": "state.currentExperimentName",
        "currentExperimentStatus": "state.currentExperimentStatus",
        "currentWorkspaceReadonly": "state.workspaceReadonly",
    }
    required_functions = {
        "showToast",
        "markDirty",
        "clearDirty",
        "applyStatusPill",
        "setWorkspaceMode",
        "goToPage",
    }
    missing = []
    for legacy_name, replacement in sorted(state_replacements.items()):
        if replacement not in source:
            missing.append(f"{legacy_name} -> {replacement}")
        bare_lines = [
            line
            for line in source.splitlines()
            if re.search(rf"(?<!state\.)\b{re.escape(legacy_name)}\b", line)
            and not re.search(rf"^\s*{re.escape(legacy_name)}\s*:", line)
        ]
        if bare_lines:
            missing.append(legacy_name)
    for name in sorted(required_functions):
        declared = re.search(
            rf"\b(?:async\s+function|function|const|let|var)\s+{re.escape(name)}\b",
            source,
        )
        if not declared:
            missing.append(name)
    assert missing == [], (
        "console-api.js still relies on globals supplied only by the removed inline "
        f"prototype: {missing}"
    )


def test_def_045_console_api_owns_foundational_ui_interactions():
    """Removing the prototype must not silently remove the only event listeners."""

    source = CONSOLE_JS.read_text(encoding="utf-8")
    required_wiring = {
        "new experiment modal": "$('createExperimentBtn').addEventListener",
        "wizard back": "$('wizardBack').addEventListener",
        "close create modal": "$('closeCreateModal').addEventListener",
        "close publish modal": "$('closeModal').addEventListener",
        "back to experiment list": "$('backToHub').addEventListener",
    }
    missing = [label for label, marker in required_wiring.items() if marker not in source]
    nav_owned = source.count("document.querySelectorAll('.nav-item[data-page]')") >= 2 or (
        "closest('.nav-item[data-page]')" in source
    )
    result_tabs_owned = source.count("document.querySelectorAll('[data-result-tab]')") >= 2 or (
        "closest('[data-result-tab]')" in source
    )
    if not nav_owned:
        missing.append("left navigation")
    if not result_tabs_owned:
        missing.append("result tabs")
    assert missing == [], (
        "foundational interactions still existed only in the removed prototype: "
        f"{missing}"
    )


def test_def_045_every_static_id_selector_exists_in_the_neutral_shell():
    """A renderer cannot dereference children erased by shell neutralization."""

    shell = (
        ROOT / "generative_agents" / "web" / "static" / "experiment-console.html"
    ).read_text(encoding="utf-8")
    source = CONSOLE_JS.read_text(encoding="utf-8")
    shell_ids = set(re.findall(r'\bid=["\']([^"\']+)["\']', shell))
    selected_ids = set(re.findall(r"\$\([\"']([^\"']+)[\"']\)", source))
    missing = sorted(selected_ids - shell_ids)
    assert missing == [], (
        "console-api.js dereferences IDs removed from the neutral shell before a "
        f"renderer can recreate them: {missing}"
    )
