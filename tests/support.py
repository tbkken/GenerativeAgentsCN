"""Shared builders for the post-reset, explicit-user-map test baseline."""

from __future__ import annotations

import copy
from uuid import uuid4

from generative_agents.config import ExperimentDefinition
from generative_agents.services.maps import WorldMapService


def publish_user_map(database, *, world=None, name: str = "Test user map") -> dict:
    """Create and publish an isolated user map for a service-level test."""
    service = WorldMapService(database)
    suffix = uuid4().hex[:12]
    world_payload = None
    width, height, tile_size = 4, 4, 16
    if world is not None:
        world_payload = copy.deepcopy(
            world.model_dump(mode="json", exclude_none=False)
            if hasattr(world, "model_dump")
            else dict(world)
        )
        definition = world_payload["definition"]
        size = definition.get("size") or [height, width]
        tiles = definition.get("tiles") or ()
        coords = [tile.get("coord") for tile in tiles if isinstance(tile, dict)]
        valid_coords = [
            coord
            for coord in coords
            if isinstance(coord, list) and len(coord) == 2
        ]
        height = (
            max(int(coord[1]) for coord in valid_coords) + 1
            if valid_coords
            else int(size[0])
        )
        width = (
            max(int(coord[0]) for coord in valid_coords) + 1
            if valid_coords
            else int(size[1])
        )
        definition["size"] = [height, width]
        tile_size = int(definition.get("tile_size") or tile_size)
        root = str(definition.get("world") or world_payload.get("world_name") or name)
        definition["world"] = root
        definition["tile_address_keys"] = [
            "world",
            "sector",
            "arena",
            "game_object",
        ]
        for tile in tiles:
            address = list(tile.get("address") or ())
            if not address:
                tile["address"] = [root]
            elif address[0] != root:
                tile["address"] = ([root, *address] if len(address) < 4 else [root, *address[1:]])[:4]
        world_payload["world_name"] = root
    created = service.create_map(
        name=name,
        map_key=f"test-map-{suffix}",
        width=max(4, width),
        height=max(4, height),
        tile_size=tile_size,
    )
    draft = service.get_draft(created["id"])
    if world_payload is not None:
        draft = service.update_draft(
            created["id"],
            expected_lock_version=draft["lock_version"],
            world=world_payload,
        )
    return service.publish_draft(
        created["id"],
        draft_revision_id=draft["id"],
        expected_lock_version=draft["lock_version"],
    )


def publish_user_map_via_api(client, *, name: str = "Test user map") -> dict:
    """Create and publish an isolated user map through the public API."""
    suffix = uuid4().hex[:12]
    created_response = client.post(
        "/api/v1/maps",
        json={"name": name, "map_key": f"test-map-{suffix}"},
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    draft = client.get(f"/api/v1/maps/{created['id']}/draft").json()
    world = copy.deepcopy(draft["world"])
    root = str(world["definition"]["world"])
    for tile in world["definition"]["tiles"]:
        x, y = tile["coord"]
        tile["address"] = [root, "test-sector", "test-arena", f"tile-{x}-{y}"]
    updated_response = client.put(
        f"/api/v1/maps/{created['id']}/draft",
        json={"lock_version": draft["lock_version"], "world": world},
    )
    assert updated_response.status_code == 200, updated_response.text
    draft = updated_response.json()
    published = client.post(
        f"/api/v1/maps/{created['id']}/draft/publish",
        json={
            "draft_revision_id": draft["id"],
            "lock_version": draft["lock_version"],
        },
    )
    assert published.status_code == 200, published.text
    return published.json()


def first_builtin_crowd_revision_id(client) -> str:
    """Return one seeded crowd revision for tests that need enabled Agents."""
    crowds = client.get("/api/v1/crowds?page_size=100").json()["items"]
    crowd = next(item for item in crowds if item["is_builtin"])
    return crowd["current_published"]["id"]


def bind_definition_to_selected_map(
    definition: ExperimentDefinition,
    draft: dict,
    *,
    experiment_key: str,
) -> ExperimentDefinition:
    """Keep test-specific Agent/model data while preserving selected map identity."""
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["experiment"]["key"] = experiment_key
    payload["world"] = draft["definition"]["world"]
    return ExperimentDefinition.model_validate(payload)
