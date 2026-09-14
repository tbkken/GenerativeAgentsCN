"""Shared builders for the post-reset, explicit-user-map test baseline."""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from uuid import uuid4

from generative_agents.config import ExperimentDefinition
from generative_agents.services.maps import WorldMapService
from generative_agents.skills import DatabaseSkillRegistry


def brain_selection_for_database(database) -> dict[str, str]:
    """Seed and return the exact built-in Brain Revision for service tests."""
    registry = DatabaseSkillRegistry(
        database,
        cache_root=Path(tempfile.gettempdir()) / "ga-cn-test-skill-cache",
    )
    registry.ensure_builtin_skills()
    brain = registry.get("stanford-town-brain")
    return {
        "brain_skill": brain.name,
        "brain_revision_id": brain.revision_id,
        "brain_revision_hash": brain.revision,
    }


def publish_user_map(database, *, world=None, name: str = "Test user map") -> dict:
    """Create an isolated mutable user map for a service-level test.

    The historical helper name is kept so unrelated tests do not need a noisy rename.
    Experiments now freeze this current map only when the experiment is published.
    """
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
    current = service.get_map(created["id"])
    if world_payload is not None:
        current = service.update_map(
            created["id"],
            expected_lock_version=current["lock_version"],
            world=world_payload,
        )
    return current


def publish_user_map_via_api(client, *, name: str = "Test user map") -> dict:
    """Create a valid mutable user map through the public API."""
    suffix = uuid4().hex[:12]
    created_response = client.post(
        "/api/v1/maps",
        json={"name": name, "map_key": f"test-map-{suffix}"},
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    current = client.get(f"/api/v1/maps/{created['id']}").json()
    world = copy.deepcopy(current["world"])
    root = str(world["definition"]["world"])
    for tile in world["definition"]["tiles"]:
        x, y = tile["coord"]
        tile["address"] = [root, "test-sector", "test-arena", f"tile-{x}-{y}"]
    updated_response = client.put(
        f"/api/v1/maps/{created['id']}",
        json={"lock_version": current["lock_version"], "world": world},
    )
    assert updated_response.status_code == 200, updated_response.text
    current = updated_response.json()
    validated = client.post(
        f"/api/v1/maps/{created['id']}/validate",
        json={"lock_version": current["lock_version"]},
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["validation"]["valid"], validated.text
    return validated.json()


def first_user_crowd_revision_id(client) -> str:
    """Create one user-owned crowd revision for legacy API integration tests."""
    suffix = uuid4().hex[:10]
    definition = {
        "agent_key": f"test-agent-{suffix}",
        "enabled": True,
        "name": f"Test Agent {suffix}",
        "portrait_asset": None,
        "sprite_asset": None,
        "model_override": None,
        "tags": ["test"],
        "goals": ["complete the test"],
        "coord": [0, 0],
        "currently": "testing",
        "scratch": {
            "age": 30,
            "innate": "careful",
            "learned": "test fixtures",
            "lifestyle": "regular",
            "daily_plan": "run tests",
        },
        "spatial": {
            "address": {"living_area": ["test", "sector", "arena"]},
            "tree": {"test": {"sector": {"arena": ["object"]}}},
        },
    }
    agent = client.post(
        "/api/v1/agent-templates",
        json={"definition": definition, "description": "user-owned test Agent"},
    )
    assert agent.status_code == 201, agent.text
    agent_id = agent.json()["id"]
    agent_draft = client.get(f"/api/v1/agent-templates/{agent_id}/draft").json()
    agent_revision = client.post(
        f"/api/v1/agent-templates/{agent_id}/draft/publish",
        json={
            "draft_revision_id": agent_draft["id"],
            "lock_version": agent_draft["lock_version"],
        },
    )
    assert agent_revision.status_code == 200, agent_revision.text

    crowd = client.post(
        "/api/v1/crowds",
        json={
            "name": f"Test Crowd {suffix}",
            "crowd_key": f"test-crowd-{suffix}",
            "description": "user-owned test crowd",
            "agent_revision_ids": [agent_revision.json()["id"]],
        },
    )
    assert crowd.status_code == 201, crowd.text
    crowd_id = crowd.json()["id"]
    crowd_draft = client.get(f"/api/v1/crowds/{crowd_id}/draft").json()
    crowd_revision = client.post(
        f"/api/v1/crowds/{crowd_id}/draft/publish",
        json={
            "draft_revision_id": crowd_draft["id"],
            "lock_version": crowd_draft["lock_version"],
        },
    )
    assert crowd_revision.status_code == 200, crowd_revision.text
    return crowd_revision.json()["id"]


def brain_revision_via_api(client, name: str = "stanford-town-brain") -> dict:
    """Return the exact immutable Brain Revision selected by API tests."""
    response = client.get(f"/api/v1/skills/{name}")
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["kind"] == "brain"
    assert document["revision_id"]
    return document


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
