"""Prepare portable town character materials; does not access Studio or Runtime data.

Run from the repository using .venv/Scripts/python.exe.  Import the resulting ZIP
through the public resource UI, then merge placements through the experiment UI.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from generative_agents.ga_protocol.packages.io import seal_directory, verify_integrity
from generative_agents.ga_protocol.packages.resources import (
    ResourceRecord,
    ResourceRef,
    ResourceSet,
    read_resource_set,
    validate_resource_set,
    write_config_package,
)
from generative_agents.ga_protocol.schemas.resources import AgentCore
from generative_agents.ga_protocol.schemas.spatial import validate_agent_spatial


HERE = Path(__file__).resolve().parent
CASE_ROOT = HERE.parent
CONFIG_ROOT = HERE / "config"
ARCHIVE = HERE / "stanford-town-original-agents-config.zip"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def same_resources(left: ResourceSet, right: ResourceSet) -> bool:
    return (left.by_ref() == right.by_ref() and left.files == right.files
            and {ref.identity for ref in left.roots} == {ref.identity for ref in right.roots})


def build() -> None:
    roster = read_json(HERE / "roster-reference.json")
    assert roster["source_count"] == len(roster["agents"]) == 25
    map_resources = read_resource_set(CASE_ROOT / "map" / "stanford-town-original-map-config.zip")
    map_record = next(item for item in map_resources.resources if item.kind == "map")
    world = map_record.definition["definition"]
    tiles = world["tiles"]
    tile_by_coord = {tuple(tile["coord"]): tile for tile in tiles}
    resources = ResourceSet()
    placements = []
    spatial_checks = []
    for person in roster["agents"]:
        key = person["proposed_stable_key"]
        profile = person["profile"]
        params = person["source_behavior_parameters"]
        assets = {item["role"]: item for item in person["assets"]}
        logical_paths = {}
        for role, filename in (("portrait", "portrait.png"), ("walking_texture", "texture.png")):
            source = assets[role]
            content = (CASE_ROOT / source["copy"]).read_bytes()
            assert hashlib.sha256(content).hexdigest() == source["sha256"], key
            logical = f"assets/agents/{key}/{filename}"
            logical_paths[role] = logical
            resources.files[logical] = content
        definition = AgentCore(
            scratch={field: profile[field] for field in ("age", "innate", "learned", "lifestyle", "daily_plan")},
            currently=profile["currently"],
            portrait_asset=logical_paths["portrait"],
            sprite_asset=logical_paths["walking_texture"],
            sprite_layout="4x3",
            perception={"mode": params["perception_mode"], "vision_radius": params["vision_radius"],
                        "attention_bandwidth": params["attention_bandwidth"]},
        ).model_dump(mode="json", exclude_none=True)
        # Only document explicitly sourced values and required protocol defaults.
        for field in ("tags", "goals"):
            definition.pop(field, None)
        record = ResourceRecord(
            kind="agent", key=key, name=person["display_name"],
            description=f"斯坦福小镇中文原项目角色：{person['display_name']}（{person['documented_english_name']}）。",
            definition=definition, attachments=list(logical_paths.values()),
        )
        resources.resources.append(record)
        resources.roots.append(ResourceRef(kind="agent", key=key))

        source_ref = read_json(HERE / key / "source-reference.json")
        spatial = copy.deepcopy(source_ref["classic_known_spatial"])
        spatial["address"]["initial_location"] = person["initial_address"]
        placement = {"agent_key": key, "coord": person["initial_coord"], "spatial": spatial}
        placements.append(placement)
        tile = tile_by_coord[tuple(person["initial_coord"])]
        assert tile["address"] == person["initial_address"], (key, tile["address"], person["initial_address"])
        assert tile.get("collision") is not True, (key, "original placement is blocked")
        issues = validate_agent_spatial(spatial["address"], spatial["tree"], world_roots=[world["world"]], world_tiles=tiles)
        spatial_checks.append({"agent_key": key, "coord": person["initial_coord"],
                               "initial_address": person["initial_address"],
                               "issues": [item.__dict__ for item in issues]})
        assert not issues, (key, issues)

        # Mirror the existing UI's merge operation; omitted character fields survive.
        before = dict(definition, agent_key=key, name=person["display_name"], coord=[0, 0], spatial={})
        after = {**before, **placement, "scratch": {**before["scratch"], **placement.get("scratch", {})},
                 "spatial": {**before["spatial"], **placement["spatial"]}}
        assert {k: v for k, v in before.items() if k not in {"coord", "spatial"}} == {
            k: v for k, v in after.items() if k not in {"coord", "spatial"}}

    assert len({record.key for record in resources.resources}) == 25
    validate_resource_set(resources)
    if CONFIG_ROOT.exists():
        prior = read_resource_set(CONFIG_ROOT)
        assert same_resources(prior, resources), "Existing prepared content differs; do not overwrite it silently."
    else:
        write_config_package(CONFIG_ROOT, resources, name="斯坦福小镇原样中文25人",
                             config_id="29a98a27-a1a0-4825-91fa-f622f4ba7090",
                             created_at=datetime(2026, 10, 15, tzinfo=UTC))
    verify_integrity(CONFIG_ROOT)
    if not ARCHIVE.exists():
        seal_directory(CONFIG_ROOT, ARCHIVE)
    reread = read_resource_set(ARCHIVE)
    assert same_resources(reread, resources)
    save_json(HERE / "experiment-placement-merge.json", {"agents": placements})
    report = {
        "status": "offline-materials-verified-not-imported",
        "package_kind": "config", "schema_version": 2,
        "agent_count": len(resources.resources), "attachment_count": len(resources.files),
        "archive": str(ARCHIVE), "archive_bytes": ARCHIVE.stat().st_size,
        "archive_sha256": hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
        "roster_sha256": hashlib.sha256((HERE / "roster-reference.json").read_bytes()).hexdigest(),
        "public_agents_contain_coord_or_spatial": False,
        "all_assets_match_source_sha256": True,
        "placement_ui_merge_preserves_unprovided_profile_and_assets": True,
        "placement_validation": spatial_checks,
        "parameter_mapping": {
            "perception": "box, radius 8, bandwidth 8 from the original source config",
            "sprite_layout": "4x3 from original 96x128 texture dimensions and four direction rows",
            "sprite_display_tiles": "omitted; current product default, not a claimed original value",
            "move_speed": "not supplied: original Agent config has no runtime speed field",
            "enabled": "true: ordinary imported resource enabled for selection",
            "omitted_legacy_parameters": ["chat_iterations", "schedule_max_try", "schedule_diversity", "think_interval", "poignancy_max", "memory_retention"],
        },
    }
    save_json(HERE / "offline-verification.json", report)
    print(json.dumps({k: report[k] for k in ("status", "agent_count", "attachment_count", "archive", "archive_sha256")}, ensure_ascii=False))


if __name__ == "__main__":
    build()
