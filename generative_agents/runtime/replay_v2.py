"""Single validated Replay Bundle V2 contract used by every producer."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Literal, Mapping
from urllib.parse import quote
from uuid import UUID

from pydantic import Field

from generative_agents.config import ExperimentDefinition
from generative_agents.config.schema import StrictModel

from .results import StepResult
from .artifact_contract import REPLAY_GENERATOR_VERSION


GENERATOR_VERSION = REPLAY_GENERATOR_VERSION
LEGACY_ADAPTER_VERSION = "ga-replay-legacy-adapter-v1"
_VILLAGE_URL = "/generative_agents/frontend/static/assets/village"
_VILLAGE_ROOT = Path(__file__).resolve().parents[1] / "frontend" / "static" / "assets" / "village"
_NORMALIZED_TILEMAP_URL = "/static/console/replay-assets/tilemap.json"
_NORMALIZED_TILEMAP_SHA256 = (
    "53477dc3e5eed02798967fbe032774bf73abe96316a7aeb93397b932e1d3259b"
)
_LEGACY_TILEMAP_SHA256 = (
    "8c15aa6f46ebaf43aec6cf3244860e8161e9d8f7541d1765f907a496686a9bfc"
)
_NORMALIZED_INTERIORS_PT3_URL = "/static/console/replay-assets/interiors_pt3.png"
_NORMALIZED_INTERIORS_PT3_SHA256 = (
    "2d7eab019f428df91dfe8a5861575b7fe15196c1832f2921872de0cd7cc17952"
)


class ReplayAgentDefinition(StrictModel):
    agent_key: str
    display_name: str
    initial_coord: tuple[int, int]
    sprite_asset: dict[str, Any]


class ReplayAgentStep(StrictModel):
    agent_key: str
    from_coord: tuple[int, int]
    coord: tuple[int, int]
    path: list[tuple[int, int]]
    path_source: str
    action: dict[str, Any]
    address: list[str]
    currently: str | None = None
    schedule_item_id: str | None = None


class ReplayStep(StrictModel):
    step_no: int = Field(ge=1)
    virtual_time: str
    attempt_id: str
    attempt_boundary: bool
    checkpoint: bool
    agents: list[ReplayAgentStep]
    conversations: list[dict[str, Any]]
    memory_deltas: list[dict[str, Any]]
    schedule_revisions: list[dict[str, Any]]
    domain_events: list[dict[str, Any]]


class ReplayBundleV2(StrictModel):
    schema_version: Literal[2] = 2
    generator_version: str
    source_kind: Literal["RUN_FRAMES", "RUN_PROJECTION", "LEGACY_ADAPTER"]
    run_id: str
    revision_id: str
    definition_hash: str
    world: dict[str, Any]
    source_step: int = Field(ge=0)
    available_step: int = Field(ge=0)
    stride_minutes: int = Field(ge=1)
    start_time: str
    agents: list[ReplayAgentDefinition]
    partial: bool
    steps: list[ReplayStep]


def _builtin_sprite_map() -> dict[str, str]:
    root = _VILLAGE_ROOT / "agents"
    if not root.is_dir():
        return {}
    directories = sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name)
    return {
        f"resident-{index:03d}": directory.name
        for index, directory in enumerate(directories, start=1)
    }


def _world_descriptor(definition: ExperimentDefinition) -> dict[str, Any]:
    world = definition.world.model_dump(mode="json", exclude_none=False)
    if definition.world.world_key == "the-ville":
        world["render_asset"] = {
            "status": "READY",
            "source": "BUILTIN_PACKAGE",
            "tilemap_url": _NORMALIZED_TILEMAP_URL,
            "tilemap_asset": {
                "url": _NORMALIZED_TILEMAP_URL,
                "sha256": _NORMALIZED_TILEMAP_SHA256,
                "source_sha256": _LEGACY_TILEMAP_SHA256,
                "normalization": "INTERIORS_PT3_IMAGEHEIGHT_10016",
            },
            "base_url": f"{_VILLAGE_URL}/tilemap",
            "tile_size": 32,
            # The legacy source has a 16 px non-tile footer.  Replay owns a
            # package-local, pixel-identical crop of the complete 313 tile
            # rows so Phaser never receives a malformed tilesheet.
            "texture_overrides": {
                "interiors_pt3": {
                    "url": _NORMALIZED_INTERIORS_PT3_URL,
                    "sha256": _NORMALIZED_INTERIORS_PT3_SHA256,
                    "width": 512,
                    "height": 10016,
                    "normalization": "CROP_BOTTOM_NON_TILE_PIXELS",
                }
            },
        }
    else:
        world["render_asset"] = {
            "status": "MISSING",
            "source": "REVISION_ASSETS",
            "error_code": "WORLD_RENDER_ASSET_UNRESOLVED",
        }
    return world


def _agents(definition: ExperimentDefinition) -> list[dict[str, Any]]:
    builtin = _builtin_sprite_map() if definition.world.world_key == "the-ville" else {}
    output = []
    for agent in definition.agents:
        if not agent.enabled:
            continue
        directory = builtin.get(agent.agent_key)
        if directory:
            sprite = {
                "status": "READY",
                "source": "BUILTIN_PACKAGE",
                "texture_url": f"{_VILLAGE_URL}/agents/{quote(directory)}/texture.png",
                "atlas_url": f"{_VILLAGE_URL}/agents/sprite.json",
            }
        elif agent.portrait_asset:
            sprite = {
                "status": "MISSING",
                "source": "REVISION_ASSET",
                "logical_reference": agent.portrait_asset,
                "error_code": "AGENT_SPRITE_ASSET_UNRESOLVED",
            }
        else:
            sprite = {
                "status": "MISSING",
                "source": "NONE",
                "error_code": "AGENT_SPRITE_MAPPING_MISSING",
            }
        output.append(
            {
                "agent_key": agent.agent_key,
                "display_name": agent.name,
                "initial_coord": agent.coord,
                "sprite_asset": sprite,
            }
        )
    return output


def _step_document(
    result: StepResult,
    *,
    checkpoint: bool,
    attempt_boundary: bool,
) -> dict[str, Any]:
    wire = result.to_dict()
    return {
        "step_no": result.step_no,
        "virtual_time": result.virtual_time.isoformat(),
        "attempt_id": str(result.attempt_id),
        "attempt_boundary": attempt_boundary,
        "checkpoint": checkpoint,
        "agents": [
            {
                "agent_key": agent.agent_key,
                "from_coord": agent.from_coord,
                "coord": agent.to_coord,
                "path": list(agent.path),
                "path_source": agent.path_source,
                "action": {
                    "description": agent.action.description,
                    "emoji": agent.action.emoji,
                    "object_description": agent.action.object_description,
                },
                "address": list(agent.location),
                "currently": agent.currently,
                "schedule_item_id": agent.schedule_item_id,
            }
            for agent in result.agents
        ],
        "conversations": wire["conversations"],
        "memory_deltas": wire["memory_deltas"],
        "schedule_revisions": [
            {
                "revision_id": item["revision_id"],
                "sequence": item["sequence"],
                "agent_key": item["agent_key"],
                "reason": item["reason"],
                "source_event_id": item.get("source_event_id"),
                "content_hash": item["content_hash"],
                "item_count": len(item.get("schedule", [])),
                # A replay is an inspector DTO, not a storage dump.  Keep a
                # bounded schedule preview and exclude vector/secret-shaped
                # fields while preserving the committed order.
                "schedule": [
                    {
                        str(key): value
                        for key, value in schedule_item.items()
                        if not any(
                            token in str(key).casefold()
                            for token in ("embedding", "vector", "secret", "token", "api_key")
                        )
                    }
                    for schedule_item in item.get("schedule", [])[:50]
                    if isinstance(schedule_item, Mapping)
                ],
                "truncated": len(item.get("schedule", [])) > 50,
            }
            for item in wire["schedule_revisions"]
        ],
        "domain_events": wire["domain_events"],
    }


def build_replay_v2(
    *,
    run_id: str,
    revision_id: str,
    definition_hash: str,
    definition: ExperimentDefinition,
    source_step: int,
    partial: bool,
    results: Iterable[StepResult],
    checkpoint_steps: Iterable[int] = (),
    source_kind: Literal["RUN_FRAMES", "RUN_PROJECTION", "LEGACY_ADAPTER"] = "RUN_FRAMES",
    generator_version: str = GENERATOR_VERSION,
    previous_attempt_id: str | UUID | None = None,
) -> dict[str, Any]:
    checkpoints = set(checkpoint_steps)
    ordered = sorted((result for result in results if result.step_no <= source_step), key=lambda item: item.step_no)
    previous_attempt = previous_attempt_id
    steps = []
    for result in ordered:
        boundary = previous_attempt is None or str(previous_attempt) != str(result.attempt_id)
        steps.append(
            _step_document(
                result,
                checkpoint=result.step_no in checkpoints,
                attempt_boundary=boundary,
            )
        )
        previous_attempt = result.attempt_id
    document = ReplayBundleV2.model_validate(
        {
            "schema_version": 2,
            "generator_version": generator_version,
            "source_kind": source_kind,
            "run_id": run_id,
            "revision_id": revision_id,
            "definition_hash": definition_hash,
            "world": _world_descriptor(definition),
            "source_step": source_step,
            "available_step": source_step,
            "stride_minutes": definition.simulation.stride_minutes,
            "start_time": definition.simulation.start_time.isoformat(),
            "agents": _agents(definition),
            "partial": partial,
            "steps": steps,
        }
    )
    return document.model_dump(mode="json", exclude_none=False)


def validate_replay_v2(document: Mapping[str, Any]) -> dict[str, Any]:
    return ReplayBundleV2.model_validate(document).model_dump(mode="json", exclude_none=False)


def adapt_legacy_movement(
    document: Mapping[str, Any], *, definition: ExperimentDefinition
) -> dict[str, Any]:
    """Adapt a legacy movement document without ever relabelling it as native V2."""

    steps = []
    previous: dict[str, tuple[int, int]] = {}
    for raw_step in document.get("steps", []):
        agent_items = []
        for raw in raw_step.get("agents", []):
            key = str(raw.get("agent_key") or raw.get("name") or "unknown")
            samples = raw.get("samples") or raw.get("path") or []
            coord = tuple(raw.get("coord") or (samples[-1] if samples else previous.get(key, (0, 0))))
            start = previous.get(key, coord)
            previous[key] = coord
            agent_items.append(
                {
                    "agent_key": key,
                    "from_coord": start,
                    "coord": coord,
                    "path": samples or [coord],
                    "path_source": "RECONSTRUCTED",
                    "action": {"description": raw.get("action") or "", "emoji": raw.get("emoji")},
                    "address": (
                        raw.get("address")
                        if isinstance(raw.get("address"), list)
                        else raw.get("location")
                        if isinstance(raw.get("location"), list)
                        else [raw.get("address") or ""]
                    ),
                }
            )
        steps.append(
            {
                "step_no": int(raw_step.get("step_no", len(steps) + 1)),
                "virtual_time": raw_step.get("virtual_time") or definition.simulation.start_time.isoformat(),
                "attempt_id": "legacy",
                "attempt_boundary": not steps,
                "checkpoint": False,
                "agents": agent_items,
                "conversations": list(raw_step.get("conversations", [])),
                "memory_deltas": list(raw_step.get("memory_deltas", [])),
                "schedule_revisions": list(raw_step.get("schedule_revisions", [])),
                "domain_events": list(raw_step.get("domain_events", [])),
            }
        )
    source_step = int(document.get("source_step", len(steps)))
    adapted = {
        "schema_version": 2,
        "generator_version": LEGACY_ADAPTER_VERSION,
        "source_kind": "LEGACY_ADAPTER",
        "run_id": str(document.get("run_id", "legacy")),
        "revision_id": str(document.get("revision_id", "legacy")),
        "definition_hash": str(document.get("definition_hash", "legacy")),
        "world": _world_descriptor(definition),
        "source_step": source_step,
        "available_step": source_step,
        "stride_minutes": definition.simulation.stride_minutes,
        "start_time": definition.simulation.start_time.isoformat(),
        "agents": _agents(definition),
        "partial": bool(document.get("partial", False)),
        "steps": steps,
    }
    return validate_replay_v2(adapted)
