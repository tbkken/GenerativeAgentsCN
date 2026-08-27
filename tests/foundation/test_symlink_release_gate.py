"""基础能力回归测试：覆盖 ``test_symlink_release_gate`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "tools" / "run_symlink_release_gate.py"
WORKFLOW = ROOT / ".github" / "workflows" / "native-symlink-release-gate.yml"


def _gate_module():
    """为本测试模块封装 ``_gate_module`` 辅助步骤，减少重复的场景搭建代码。"""
    spec = importlib.util.spec_from_file_location("symlink_release_gate", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_gate_reuses_all_seven_native_symlink_redlines(tmp_path):
    """回归验证 ``test_release_gate_reuses_all_seven_native_symlink_redlines`` 所描述的业务结果、故障边界和隔离约束。"""
    gate = _gate_module()
    nodes = gate.SYMLINK_TEST_NODES

    assert len(nodes) == len(set(nodes)) == 7
    assert sum("test_def_047" in node for node in nodes) == 1
    assert sum("test_def_061" in node for node in nodes) == 3
    assert sum("test_def_063" in node for node in nodes) == 3
    assert sum("[final_symlink]" in node for node in nodes) == 2
    assert sum("[intermediate_symlink]" in node for node in nodes) == 2
    assert sum("cross_run_native" in node for node in nodes) == 2

    report = tmp_path / "report.xml"
    report.write_text(
        '<testsuites><testsuite tests="7" failures="0" errors="0" skipped="1" /></testsuites>',
        encoding="utf-8",
    )
    assert gate.junit_counts(report) == (7, 0, 0, 1)


def test_release_workflow_is_strict_on_linux_and_windows():
    """回归验证 ``test_release_workflow_is_strict_on_linux_and_windows`` 所描述的业务结果、故障边界和隔离约束。"""
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "ubuntu-latest" in workflow
    assert "windows-latest" in workflow
    assert "AllowDevelopmentWithoutDevLicense" in workflow
    assert "python tools/run_symlink_release_gate.py" in workflow
    assert "--allow-unavailable" not in workflow
    assert "fail-fast: false" in workflow
    assert 'GA_REQUIRE_NATIVE_SYMLINK_TESTS: "1"' in workflow
    assert 'environment["GA_REQUIRE_NATIVE_SYMLINK_TESTS"] = "1"' in SCRIPT.read_text(
        encoding="utf-8"
    )
    assert '"SYMLINK_RELEASE_GATE_FAILED allow-unavailable is forbidden in CI"' in SCRIPT.read_text(
        encoding="utf-8"
    )
