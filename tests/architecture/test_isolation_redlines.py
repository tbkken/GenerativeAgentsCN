"""Release-blocking architecture redlines for experiment isolation.

Every assertion describes a target invariant from the approved technical
design.  Failures are intentional on the legacy baseline and reference a DEF
entry in ``docs/defect-log.md``.  Product changes should make these pass; do
not weaken a redline merely to obtain a green test run.
"""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_ROOT = REPO_ROOT / "generative_agents"


def _source(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _module_scope_calls(relative_path: str, call_name: str) -> list[int]:
    tree = ast.parse(_source(relative_path), filename=relative_path)
    lines: list[int] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value.func
        if isinstance(call, ast.Attribute) and call.attr == call_name:
            lines.append(node.lineno)
        elif isinstance(call, ast.Name) and call.id == call_name:
            lines.append(node.lineno)
    return lines


def test_def_001_runtime_has_no_process_global_game_or_timer_registry() -> None:
    offenders: list[str] = []
    for relative_path in (
        "generative_agents/modules/game.py",
        "generative_agents/modules/utils/timer.py",
        "generative_agents/modules/utils/log.py",
        "generative_agents/modules/agent.py",
    ):
        source = _source(relative_path)
        if "GenerativeAgentsMap" in source or "get_timer()" in source:
            offenders.append(relative_path)
    assert not offenders, f"DEF-001 process-global run state remains in: {offenders}"


def test_def_002_vector_indexes_do_not_write_llama_global_settings() -> None:
    source = _source("generative_agents/modules/storage/index.py")
    forbidden = (
        "from llama_index.core import Settings",
        "Settings.embed_model",
        "Settings.node_parser",
        "Settings.num_output",
        "Settings.context_window",
    )
    found = [token for token in forbidden if token in source]
    assert not found, f"DEF-002 LlamaIndex global state remains: {found}"


def test_def_003_run_paths_are_not_derived_from_user_visible_names() -> None:
    game_source = _source("generative_agents/modules/game.py")
    start_source = _source("generative_agents/start.py")
    replay_source = _source("generative_agents/replay.py")
    offenders = []
    if 'f"results/checkpoints/{name}"' in game_source:
        offenders.append("modules/game.py")
    if 'f"{checkpoints_path}/{name}"' in start_source:
        offenders.append("start.py")
    if 'f"results/compressed/{name}"' in replay_source:
        offenders.append("replay.py")
    assert not offenders, f"DEF-003 name-derived run directories remain in: {offenders}"


def test_def_004_checkpoint_identity_is_monotonic_step_number() -> None:
    source = _source("generative_agents/start.py")
    assert "simulate-{sim_time.replace(':', '')}.json" not in source, (
        "DEF-004 checkpoint filename still uses virtual minute and can overwrite another step"
    )
    assert "step-{" in source or (PRODUCT_ROOT / "runtime" / "checkpoint.py").exists(), (
        "DEF-004 no step-numbered checkpoint writer is present"
    )


def test_def_005_snapshot_serialization_has_no_hidden_index_persist() -> None:
    source = _source("generative_agents/modules/memory/associate.py")
    tree = ast.parse(source)
    to_dict = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "to_dict"
    )
    function_source = ast.get_source_segment(source, to_dict) or ""
    assert "save(" not in function_source and "persist(" not in function_source, (
        "DEF-005 Associate.to_dict still writes storage before the checkpoint bundle commit"
    )


def test_def_006_runtime_does_not_read_shared_bootstrap_configuration() -> None:
    checked = {
        "generative_agents/start.py": ("data/config.json", "frontend/static"),
        "generative_agents/modules/prompt/scratch.py": ("data/prompts",),
        "generative_agents/compress.py": ("frontend/static/assets/village",),
    }
    found: list[str] = []
    for relative_path, tokens in checked.items():
        source = _source(relative_path)
        found.extend(f"{relative_path}:{token}" for token in tokens if token in source)
    assert not found, f"DEF-006 runtime/bootstrap coupling remains: {found}"


def test_def_007_importing_product_modules_does_not_parse_process_arguments() -> None:
    offenders = {
        path: _module_scope_calls(path, "parse_args")
        for path in ("generative_agents/start.py", "generative_agents/compress.py")
    }
    offenders = {path: lines for path, lines in offenders.items() if lines}
    assert not offenders, f"DEF-007 import-time argparse side effects remain: {offenders}"


def test_def_008_simulation_loop_commits_complete_step_results() -> None:
    start_source = _source("generative_agents/start.py")
    required_files = (
        PRODUCT_ROOT / "runtime" / "result_types.py",
        PRODUCT_ROOT / "runtime" / "result_collector.py",
        PRODUCT_ROOT / "runtime" / "result_projector.py",
    )
    missing = [str(path.relative_to(REPO_ROOT)) for path in required_files if not path.exists()]
    assert '["plan"]' not in start_source, "DEF-008 simulation loop still discards non-plan agent facts"
    assert not missing, f"DEF-008 complete StepResult pipeline is absent: {missing}"


def test_def_012_published_world_and_action_inputs_are_not_mutated() -> None:
    maze_source = _source("generative_agents/modules/maze.py")
    action_source = _source("generative_agents/modules/memory/action.py")
    agent_source = _source("generative_agents/modules/agent.py")
    offenders = []
    if 'tile.pop("coord")' in maze_source:
        offenders.append("Maze.__init__ tile.pop")
    if 'config["event"] =' in action_source or 'config["start"] =' in action_source:
        offenders.append("Action.from_dict config mutation")
    if 'config["coord"] =' in agent_source:
        offenders.append("Agent.__init__ config mutation")
    assert not offenders, f"DEF-012 immutable revision inputs are mutated: {offenders}"


def test_def_013_simulation_randomness_comes_from_run_context() -> None:
    offenders: list[str] = []
    for relative_path in (
        "generative_agents/modules/agent.py",
        "generative_agents/modules/maze.py",
        "generative_agents/modules/prompt/scratch.py",
    ):
        source = _source(relative_path)
        if "import random" in source or "random.choice" in source or "random.sample" in source:
            offenders.append(relative_path)
    assert not offenders, f"DEF-013 module-global random source remains in: {offenders}"
