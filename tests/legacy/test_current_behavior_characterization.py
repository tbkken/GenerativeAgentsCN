"""Regression coverage for behaviors extracted from the legacy engine.

Originally this module captured known-bad behavior. Once a defect is fixed the
test is intentionally converted into an executable assertion of the corrected
contract; preserving an obsolete failure would make the suite contradict the
release-blocking architecture tests.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "generative_agents"


def _source(relative_path: str) -> str:
    """为本测试模块封装 ``_source`` 辅助步骤，减少重复的场景搭建代码。"""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _load_arguments_module():
    """为本测试模块封装 ``_load_arguments_module`` 辅助步骤，减少重复的场景搭建代码。"""
    path = SOURCE_ROOT / "modules" / "utils" / "arguments.py"
    spec = importlib.util.spec_from_file_location("legacy_arguments", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_soft_update_treats_falsy_existing_values_as_missing() -> None:
    """A zero/False/empty value is overwritten even for a soft update."""

    arguments = _load_arguments_module()
    original = {"zero": 0, "false": False, "empty": "", "present": 7}

    result = arguments.update_dict(
        original,
        {"zero": 2, "false": True, "empty": "filled", "present": 9},
        soft_update=True,
    )

    assert result == {"zero": 2, "false": True, "empty": "filled", "present": 7}


def test_fixed_checkpoint_identity_uses_monotonic_step_not_virtual_minute() -> None:
    """回归验证 ``test_fixed_checkpoint_identity_uses_monotonic_step_not_virtual_minute`` 所描述的业务结果、故障边界和隔离约束。"""
    checkpoint_source = _source("generative_agents/runtime/checkpoint.py")
    assert 'f"step-{result.step_no:06d}"' in checkpoint_source
    assert "simulate-{sim_time" not in _source("generative_agents/start.py")
    assert f"step-{1:06d}" != f"step-{2:06d}"


def test_fixed_resume_uses_verified_latest_bundle_not_arbitrary_json_order() -> None:
    """回归验证 ``test_fixed_resume_uses_verified_latest_bundle_not_arbitrary_json_order`` 所描述的业务结果、故障边界和隔离约束。"""
    checkpoint_source = _source("generative_agents/runtime/checkpoint.py")
    assert "def read_latest" in checkpoint_source
    assert "bundle_sha256" in checkpoint_source
    assert "json_files[-1]" not in _source("generative_agents/start.py")


def test_fixed_memory_limit_keeps_exact_configured_capacity() -> None:
    """回归验证 ``test_fixed_memory_limit_keeps_exact_configured_capacity`` 所描述的业务结果、故障边界和隔离约束。"""
    from generative_agents.modules.memory.associate import enforce_memory_limit

    for limit in (1, 2, 8):
        kept, evicted = enforce_memory_limit(list(range(20)), limit)
        assert len(kept) == limit
        assert kept + evicted == list(range(20))


def test_fixed_short_chat_duration_is_at_least_one_minute() -> None:
    """回归验证 ``test_fixed_short_chat_duration_is_at_least_one_minute`` 所描述的业务结果、故障边界和隔离约束。"""
    from generative_agents.modules.agent import estimate_chat_duration

    assert estimate_chat_duration([("a", "")]) == 1
    assert estimate_chat_duration([("a", "x")]) == 1
    assert estimate_chat_duration([("a", "x" * 239)]) == 1
    assert estimate_chat_duration([("a", "x" * 240)]) == 1
    assert estimate_chat_duration([("a", "x" * 241)]) == 2


def test_fixed_agent_serialization_has_no_vector_index_io_side_effect() -> None:
    """回归验证 ``test_fixed_agent_serialization_has_no_vector_index_io_side_effect`` 所描述的业务结果、故障边界和隔离约束。"""
    associate_source = _source("generative_agents/modules/memory/associate.py")
    to_dict_source = associate_source.split("def to_dict(self):", 1)[1].split(
        "def export_storage", 1
    )[0]
    assert ".save(" not in to_dict_source
    assert "def export_storage" in associate_source
    assert "storage_exporters" in _source("generative_agents/modules/game.py")


def test_fixed_replay_reads_run_manifest_and_observed_frames_only() -> None:
    """回归验证 ``test_fixed_replay_reads_run_manifest_and_observed_frames_only`` 所描述的业务结果、故障边界和隔离约束。"""
    compress_source = _source("generative_agents/compress.py")
    replay_v2_source = _source("generative_agents/runtime/replay_v2.py")
    assert "RunManifestStore" in compress_source
    assert "build_replay_v2" in compress_source
    assert "StepResult.from_dict" in compress_source
    assert '"path_source": agent.path_source' in replay_v2_source
    assert "find_path(" not in compress_source
    assert "frontend/static" not in compress_source


def test_fixed_product_imports_parse_arguments_only_inside_main() -> None:
    """回归验证 ``test_fixed_product_imports_parse_arguments_only_inside_main`` 所描述的业务结果、故障边界和隔离约束。"""
    for relative_path in ("generative_agents/start.py", "generative_agents/compress.py"):
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
        assert "def main(argv=None)" in source
