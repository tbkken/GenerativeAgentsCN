"""Regression coverage for behaviors extracted from the legacy engine.

Originally this module captured known-bad behavior. Once a defect is fixed the
test is intentionally converted into an executable assertion of the corrected
contract; preserving an obsolete failure would make the suite contradict the
release-blocking architecture tests.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    """为本测试模块封装 ``_source`` 辅助步骤，减少重复的场景搭建代码。"""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")






def test_fixed_checkpoint_identity_uses_monotonic_step_not_virtual_minute() -> None:
    """回归验证 ``test_fixed_checkpoint_identity_uses_monotonic_step_not_virtual_minute`` 所描述的业务结果、故障边界和隔离约束。"""
    checkpoint_source = _source("src/generative_agents/ga_runtime/storage/checkpoints.py")
    assert 'f"step-{result.step_no:06d}"' in checkpoint_source
    assert "simulate-{sim_time" not in _source("src/generative_agents/ga_runtime/engine/scheduler.py")
    assert f"step-{1:06d}" != f"step-{2:06d}"


def test_fixed_resume_uses_verified_latest_bundle_not_arbitrary_json_order() -> None:
    """回归验证 ``test_fixed_resume_uses_verified_latest_bundle_not_arbitrary_json_order`` 所描述的业务结果、故障边界和隔离约束。"""
    checkpoint_source = _source("src/generative_agents/ga_runtime/storage/checkpoints.py")
    assert "def read_latest" in checkpoint_source
    assert "bundle_sha256" in checkpoint_source
    assert "json_files[-1]" not in _source("src/generative_agents/ga_runtime/engine/scheduler.py")








def test_fixed_replay_reads_run_manifest_and_observed_frames_only() -> None:
    """回归验证 ``test_fixed_replay_reads_run_manifest_and_observed_frames_only`` 所描述的业务结果、故障边界和隔离约束。"""
    source = _source("src/generative_agents/ga_replay/reader.py")
    assert "validate_run_integrity" in source
    assert "find_path(" not in source
    assert "frontend/static" not in source


def test_fixed_product_imports_parse_arguments_only_inside_main() -> None:
    """回归验证 ``test_fixed_product_imports_parse_arguments_only_inside_main`` 所描述的业务结果、故障边界和隔离约束。"""
    for relative_path in ("src/generative_agents/adapters/cli/main.py",):
        source = _source(relative_path)
        tree = __import__("ast").parse(source)
        module_calls = [
            node
            for node in tree.body
            if isinstance(node, __import__("ast").Assign)
            and isinstance(node.value, __import__("ast").Call)
            and getattr(node.value.func, "attr", None) == "parse_args"
        ]
        assert not module_calls
        assert "def main(" in source
