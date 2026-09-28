"""Semantic validation for runnable experiment and replayable Run packages."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from generative_agents.ga_protocol.packages.constants import EXPERIMENT_MANIFEST
from generative_agents.ga_protocol.packages.constants import RUN_MANIFEST
from generative_agents.ga_protocol.packages.io import checked_package_path
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.io import sha256_file
from generative_agents.ga_protocol.packages.io import verify_integrity
from generative_agents.ga_protocol.schemas.manifests import ExperimentManifest
from generative_agents.ga_protocol.schemas.manifests import RunManifest
from generative_agents.ga_protocol.schemas.manifests import SkillPackageRegistry
from generative_agents.ga_protocol.schemas.manifests import validate_package_path


_FORBIDDEN_LIVE_REFERENCE_KEYS = {
    "map_id",
    "map_snapshot_hash",
    "revision_id",
    "brain_revision_id",
    "brain_revision_hash",
    "skill_revision_id",
    "source_revision_id",
    "secret_ref",
}


def _validate_no_live_references(value: object, location: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _FORBIDDEN_LIVE_REFERENCE_KEYS or key.endswith("_revision_id"):
                raise PackageError(f"live author-resource reference is forbidden at {location}.{key}")
            _validate_no_live_references(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_no_live_references(child, f"{location}[{index}]")


def _load_model(path: Path, model_type):
    document = read_json(path)
    try:
        return model_type.model_validate(document)
    except ValidationError as exc:
        raise PackageError(f"protocol validation failed for {path}: {exc}") from exc


def _tree_contains(tree: object, path: list[str]) -> bool:
    node = tree
    for index, segment in enumerate(path):
        if isinstance(node, dict):
            if segment not in node:
                return False
            node = node[segment]
        elif isinstance(node, list):
            return index == len(path) - 1 and segment in node
        else:
            return False
    return bool(path)


def _validate_world_and_agents(
    world_document: object,
    agents_document: object,
    semantic_document: object,
) -> None:
    if not isinstance(world_document, dict):
        raise PackageError("world entrypoint must be an object")
    definition = world_document.get("definition")
    if not isinstance(definition, dict):
        raise PackageError("world.definition must be an object")
    size = definition.get("size")
    if (
        not isinstance(size, list)
        or len(size) != 2
        or any(not isinstance(value, int) or value < 1 for value in size)
    ):
        raise PackageError("world.definition.size must be [height, width] in Tiles")
    height, width = size
    address_keys = definition.get("tile_address_keys")
    expected_keys = ["world", "sector", "arena", "game_object"]
    if address_keys != expected_keys:
        raise PackageError(
            "world.definition.tile_address_keys must be exactly "
            "world/sector/arena/game_object"
        )
    world_name = str(definition.get("world") or world_document.get("world_name") or "").strip()
    if not world_name:
        raise PackageError("world semantic root name is required")
    tiles = definition.get("tiles")
    if not isinstance(tiles, list):
        raise PackageError("world.definition.tiles must be an array")
    tile_by_coord: dict[tuple[int, int], dict] = {}
    complete_paths: set[tuple[str, str, str, str]] = set()
    for index, tile in enumerate(tiles):
        if not isinstance(tile, dict):
            raise PackageError(f"world Tile {index} must be an object")
        coord = tile.get("coord")
        if (
            not isinstance(coord, list)
            or len(coord) != 2
            or any(not isinstance(value, int) for value in coord)
        ):
            raise PackageError(f"world Tile {index} has an invalid coord")
        key = (coord[0], coord[1])
        if key in tile_by_coord:
            raise PackageError(f"world Tile coordinate is duplicated: {coord}")
        if not (0 <= key[0] < width and 0 <= key[1] < height):
            raise PackageError(f"world Tile coordinate is out of bounds: {coord}")
        if not isinstance(tile.get("collision"), bool):
            raise PackageError(f"world Tile {coord} must declare collision")
        address = tile.get("address")
        if address is not None:
            if (
                not isinstance(address, list)
                or len(address) > 4
                or any(not isinstance(value, str) or not value.strip() for value in address)
            ):
                raise PackageError(f"world Tile {coord} has an invalid semantic address")
            if address and address[0] != world_name:
                raise PackageError(f"world Tile {coord} belongs to a different World")
            if len(address) == 4:
                complete_paths.add(tuple(address))
        tile_by_coord[key] = tile
    if len(tile_by_coord) != height * width:
        raise PackageError(
            f"world Tile grid is incomplete: expected {height * width}, found {len(tile_by_coord)}"
        )
    if not complete_paths:
        raise PackageError("world must contain at least one complete four-level semantic path")

    if not isinstance(semantic_document, dict) or semantic_document.get("schema_version") != 1:
        raise PackageError("semantic index must be a schema-version 1 object")
    levels = ["WORLD", "SECTOR", "ARENA", "GAME_OBJECT"]
    if semantic_document.get("levels") != levels:
        raise PackageError("semantic index levels must be World/Sector/Arena/Game Object")
    semantic_nodes = semantic_document.get("nodes")
    if not isinstance(semantic_nodes, list):
        raise PackageError("semantic index nodes must be an array")
    semantic_by_id = {
        str(node.get("id")): node
        for node in semantic_nodes
        if isinstance(node, dict) and str(node.get("id") or "").strip()
    }
    if len(semantic_by_id) != len(semantic_nodes):
        raise PackageError("semantic index node IDs must be present and unique")
    semantic_path_to_id: dict[tuple[str, ...], str] = {}
    for node_id, node in semantic_by_id.items():
        address = node.get("address")
        kind = str(node.get("kind") or "")
        if (
            kind not in levels
            or not isinstance(address, list)
            or len(address) != levels.index(kind) + 1
            or any(not isinstance(part, str) or not part.strip() for part in address)
        ):
            raise PackageError(f"semantic node {node_id} has an invalid kind/address")
        path = tuple(address)
        if path in semantic_path_to_id:
            raise PackageError(f"semantic path is duplicated: {address}")
        semantic_path_to_id[path] = node_id
        parent_id = node.get("parent_id")
        if kind == "WORLD":
            if parent_id is not None:
                raise PackageError("WORLD semantic nodes cannot have a parent")
        else:
            parent = semantic_by_id.get(str(parent_id or ""))
            if parent is None or parent.get("kind") != levels[levels.index(kind) - 1]:
                raise PackageError(f"semantic node {node_id} has an invalid parent")
            if parent.get("address") != address[:-1]:
                raise PackageError(f"semantic node {node_id} parent address is inconsistent")
    coordinate_paths = semantic_document.get("coordinate_paths")
    if not isinstance(coordinate_paths, dict):
        raise PackageError("semantic index coordinate_paths must be an object")
    for coord, tile in tile_by_coord.items():
        key = f"{coord[0]},{coord[1]}"
        node_ids = coordinate_paths.get(key)
        address = tile.get("address") or []
        if not isinstance(node_ids, list) or any(node_id not in semantic_by_id for node_id in node_ids):
            raise PackageError(f"semantic index path is missing or invalid at Tile {list(coord)}")
        indexed_address = semantic_by_id[node_ids[-1]]["address"] if node_ids else []
        if indexed_address != address:
            raise PackageError(f"semantic index disagrees with Tile address at {list(coord)}")
    embedded_index = definition.get("semantic_index")
    if embedded_index != semantic_document:
        raise PackageError("world definition and semantic index entrypoint disagree")

    editor = definition.get("editor_v2")
    if isinstance(editor, dict):
        from generative_agents.ga_protocol.schemas.world import WorldDocument
        try:
            WorldDocument.model_validate(editor)
        except ValueError as exc:
            raise PackageError(f"world editor configuration is invalid: {exc}") from exc
        nodes = editor.get("hierarchy_nodes")
        if not isinstance(nodes, list):
            raise PackageError("world editor_v2.hierarchy_nodes must be an array")
        by_id = {
            str(node.get("id")): node
            for node in nodes
            if isinstance(node, dict) and str(node.get("id") or "").strip()
        }
        if len(by_id) != len(nodes):
            raise PackageError("world hierarchy node IDs must be present and unique")
        worlds = [node for node in nodes if node.get("kind") == "WORLD"]
        if len(worlds) != 1 or worlds[0].get("parent_id") is not None:
            raise PackageError("world hierarchy must contain exactly one root WORLD")
        parent_kind = {
            "SECTOR": "WORLD",
            "ARENA": "SECTOR",
            "GAME_OBJECT": "ARENA",
        }
        for node in nodes:
            kind = node.get("kind")
            if kind == "WORLD":
                continue
            expected_parent = parent_kind.get(kind)
            parent = by_id.get(str(node.get("parent_id")))
            if expected_parent is None or parent is None or parent.get("kind") != expected_parent:
                raise PackageError(
                    f"world hierarchy node {node.get('id')} violates World/Sector/Arena/Game Object nesting"
                )

    if not isinstance(agents_document, dict) or not isinstance(agents_document.get("agents"), list):
        raise PackageError("agents entrypoint must contain an agents array")
    seen_agent_keys: set[str] = set()
    for index, agent in enumerate(agents_document["agents"]):
        if not isinstance(agent, dict):
            raise PackageError(f"Agent {index} must be an object")
        agent_key = str(agent.get("agent_key") or "").strip()
        if not agent_key or agent_key in seen_agent_keys:
            raise PackageError(f"Agent keys must be non-empty and unique: {agent_key!r}")
        seen_agent_keys.add(agent_key)
        coord = agent.get("coord")
        if not isinstance(coord, list) or len(coord) != 2 or any(not isinstance(value, int) for value in coord):
            raise PackageError(f"Agent {agent_key} has an invalid initial coord")
        tile = tile_by_coord.get((coord[0], coord[1]))
        if tile is None or tile.get("collision") is True:
            raise PackageError(f"Agent {agent_key} initial coord is not walkable")
        spatial = agent.get("spatial")
        if not isinstance(spatial, dict):
            raise PackageError(f"Agent {agent_key} spatial placement is required")
        addresses = spatial.get("address")
        tree = spatial.get("tree")
        initial = addresses.get("initial_location") if isinstance(addresses, dict) else None
        # An Arena's walkable floor need not be covered by a Game Object.
        # Keep the exact Tile address below; an Arena prefix is not a substitute
        # when the spawn actually belongs to a four-level object address.
        if not isinstance(initial, list) or len(initial) not in (3, 4):
            raise PackageError(f"Agent {agent_key} needs one Arena or Game Object initial_location")
        if initial != tile.get("address"):
            raise PackageError(f"Agent {agent_key} initial_location does not match its Tile")
        if not _tree_contains(tree, initial):
            raise PackageError(f"Agent {agent_key} initial_location is absent from its spatial tree")


def validate_experiment_integrity(root: Path, *, verify_hashes: bool = True) -> ExperimentManifest:
    """Check package identity, safe entrypoints and exact physical contents.

    Read-only views can inspect a sealed experiment without requiring its Skill
    or author configuration to satisfy today's execution contracts.
    """
    root = checked_package_path(root)
    if verify_hashes:
        verify_integrity(root)
    manifest = _load_model(root / EXPERIMENT_MANIFEST, ExperimentManifest)
    for name, relative in manifest.entrypoints.model_dump(exclude_none=True).items():
        path = root / relative
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise PackageError(f"experiment entrypoint escapes package: {name}={relative}") from exc
        if not path.is_file():
            raise PackageError(f"experiment entrypoint is missing: {name}={relative}")
    return manifest


def validate_experiment_directory(root: Path, *, verify_hashes: bool = True) -> ExperimentManifest:
    from generative_agents.ga_protocol.packages.resources import read_resource_index, validate_resource_set
    from generative_agents.ga_protocol.packages.definition import assemble_experiment_definition
    from generative_agents.ga_protocol.schemas.resources import ExperimentAssembly
    from generative_agents.ga_protocol.schemas.experiment import ExperimentDefinition

    root = checked_package_path(root)
    manifest = validate_experiment_integrity(root, verify_hashes=verify_hashes)
    resources = read_resource_index(root, manifest.entrypoints.resources)
    validate_resource_set(resources)
    try:
        assembly = ExperimentAssembly.model_validate(read_json(root / manifest.entrypoints.assembly))
        if assembly.map.kind != "map" or assembly.brain.kind != "skill":
            raise PackageError("experiment map and Brain selections have invalid kinds")
        brain = resources.get(assembly.brain)
        if brain.definition.get("skill_kind") != "brain":
            raise PackageError("experiment Brain selection must identify a Brain Skill")
        brains = [record.key for record in resources.resources if record.kind == "skill" and record.definition.get("skill_kind") == "brain"]
        if brains != [assembly.brain.key]:
            raise PackageError("an experiment must contain exactly one configured Brain Skill")
        if set(assembly.models) != {"chat", "embedding"}:
            raise PackageError("experiment must select chat and embedding model configurations")
        placement_keys = [placement.agent.identity for placement in assembly.placements]
        if len(placement_keys) != len(set(placement_keys)):
            raise PackageError("each Agent must have exactly one experiment placement")
        definition = assemble_experiment_definition(manifest, resources, assembly)
        runtime_identity = {key: value for key, value in definition["experiment"].items() if key != "experiment_id"}
        ExperimentDefinition.model_validate({key: (runtime_identity if key == "experiment" else value)
                                             for key, value in definition.items() if key not in {"crowds", "evaluation"}})
        world = definition["world"]
        semantic = world.get("definition", {}).get("semantic_index")
        _validate_world_and_agents(world, {"agents": definition["agents"]}, semantic)
        agent_keys = {agent["agent_key"] for agent in definition["agents"]}
        for crowd in definition["crowds"]:
            if set(crowd["agent_keys"]) - agent_keys:
                raise PackageError(f"Crowd {crowd['crowd_key']} contains an Agent without an experiment placement")
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PackageError):
            raise
        raise PackageError(f"invalid experiment assembly: {exc}") from exc
    return manifest


def validate_run_integrity(root: Path, *, sealed: bool = False) -> RunManifest:
    """Validate immutable Run identity and contents without loading execution contracts.

    Replay and navigation consume recorded facts. Skill declarations and author
    configuration stay opaque here; their exact bytes are still covered by the
    embedded experiment hash. Execution additionally uses validate_run_directory.
    """
    root = checked_package_path(root)
    if sealed:
        verify_integrity(root)
    manifest = _load_model(root / RUN_MANIFEST, RunManifest)
    experiment_root = root / manifest.experiment.path
    try:
        experiment_root.resolve().relative_to(root)
    except ValueError as exc:
        raise PackageError("embedded experiment path escapes Run package") from exc
    if experiment_root.is_symlink():
        raise PackageError("embedded experiment cannot be a symbolic link")
    integrity = verify_integrity(experiment_root)
    experiment_manifest = validate_experiment_integrity(experiment_root, verify_hashes=False)
    if experiment_manifest.experiment.experiment_id != manifest.experiment.experiment_id:
        raise PackageError("embedded experiment_id does not match Run manifest")
    if integrity["root_sha256"] != manifest.experiment.root_sha256:
        raise PackageError("embedded experiment hash does not match Run manifest")
    return manifest


def validate_run_directory(root: Path, *, sealed: bool = False) -> RunManifest:
    """Validate a Run for execution, including the current experiment contracts."""
    root = checked_package_path(root)
    manifest = validate_run_integrity(root, sealed=sealed)
    validate_experiment_directory(root / manifest.experiment.path, verify_hashes=False)
    from generative_agents.ga_protocol.schemas.manifests import RunStatus
    from generative_agents.ga_protocol.facts.commits import validate_committed_frames
    from generative_agents.ga_protocol.facts.recovery import boundary_snapshot
    status = RunStatus.model_validate(read_json(root / "status.json"))
    if status.run_id != manifest.run_id or status.total_steps != manifest.requested_steps:
        raise PackageError("Run status identity or step budget disagrees with its manifest")
    validate_committed_frames(root, manifest.run_id, status.committed_step)
    if status.committed_step:
        boundary_snapshot(root, manifest.run_id, status.committed_step)
    return manifest
