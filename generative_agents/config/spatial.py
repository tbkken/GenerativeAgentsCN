"""Shared validation for Agent spatial definitions.

Public Agent templates are map-independent, while experiment Agents must also be
compatible with the selected map.  Keeping the structural rules here prevents
the two publication paths from drifting apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class AgentSpatialIssue:
    code: str
    message: str
    purpose: str | None = None
    path: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


def spatial_path_exists(tree: Mapping[str, Any], path: Sequence[str]) -> bool:
    """Return whether ``path`` resolves to a node or leaf in a spatial tree."""

    node: Any = tree
    for index, segment in enumerate(path):
        if isinstance(node, Mapping):
            if segment not in node:
                return False
            node = node[segment]
        elif isinstance(node, list):
            return index == len(path) - 1 and segment in node
        else:
            return False
    return bool(path)


def _valid_path(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(segment, str) and bool(segment.strip()) for segment in value)
    )


def build_map_address_index(
    *,
    world_roots: Iterable[str],
    tiles: Iterable[Mapping[str, Any]],
) -> set[tuple[str, ...]]:
    """Index every accessible map-address prefix once for fast Agent checks."""

    roots = {root for root in world_roots if root}
    index: set[tuple[str, ...]] = set()
    for tile in tiles:
        if not isinstance(tile, Mapping) or tile.get("collision", False):
            continue
        tile_address = tile.get("address")
        if not isinstance(tile_address, list):
            continue
        normalized = list(tile_address)
        if normalized and normalized[0] in roots:
            normalized = normalized[1:]
        for length in range(1, len(normalized) + 1):
            index.add(tuple(normalized[:length]))
    return index


def _map_contains_path(
    path: Sequence[str],
    *,
    world_roots: set[str],
    address_index: set[tuple[str, ...]],
) -> bool:
    candidate = list(path)
    if candidate and candidate[0] in world_roots:
        candidate = candidate[1:]
    if not candidate:
        return False
    return tuple(candidate) in address_index


def _iter_leaf_paths(tree: Mapping[str, Any], prefix: tuple[str, ...] = ()):
    for segment, value in tree.items():
        path = (*prefix, segment)
        if isinstance(value, Mapping):
            yield from _iter_leaf_paths(value, path)
        elif isinstance(value, list):
            for leaf in value:
                if isinstance(leaf, str) and leaf.strip():
                    yield (*path, leaf)


def validate_agent_spatial(
    address: Mapping[str, Any],
    tree: Mapping[str, Any],
    *,
    world_roots: Iterable[str] | None = None,
    world_tiles: Iterable[Mapping[str, Any]] | None = None,
    world_address_index: set[tuple[str, ...]] | None = None,
) -> list[AgentSpatialIssue]:
    """Validate structural rules and, when supplied, selected-map compatibility."""

    issues: list[AgentSpatialIssue] = []
    living = address.get("living_area")
    sleeping = address.get("sleeping") or address.get("睡觉")
    if not living and not sleeping:
        return [
            AgentSpatialIssue(
                code="AGENT_SPATIAL_ADDRESS_REQUIRED",
                message="Agent 必须配置居住地或睡觉地址",
            )
        ]
    if not tree:
        return [
            AgentSpatialIssue(
                code="AGENT_SPATIAL_TREE_REQUIRED",
                message="Agent 必须配置至少一个可用空间",
            )
        ]

    valid_paths: list[tuple[str, list[str]]] = []
    for purpose, raw_path in address.items():
        if not _valid_path(raw_path) or not spatial_path_exists(tree, raw_path):
            issues.append(
                AgentSpatialIssue(
                    code="AGENT_SPATIAL_ADDRESS_INVALID",
                    message=f"Agent 常用地址“{purpose}”不在可用空间中",
                    purpose=purpose,
                    path=tuple(raw_path) if isinstance(raw_path, list) else (),
                    details={"purpose": purpose, "path": raw_path},
                )
            )
        else:
            valid_paths.append((purpose, raw_path))

    sleeping_path: Any = sleeping
    if sleeping_path is None and _valid_path(living):
        sleeping_path = [*living, "床"]
    if not _valid_path(sleeping_path) or not spatial_path_exists(tree, sleeping_path):
        issues.append(
            AgentSpatialIssue(
                code="AGENT_SLEEPING_ADDRESS_INVALID",
                message=(
                    "Agent 睡觉地址必须指向可用空间中的床；可填写睡觉地址，"
                    "或在居住地中添加“床”"
                ),
                purpose="sleeping",
                path=tuple(sleeping_path) if isinstance(sleeping_path, list) else (),
                details={"path": sleeping_path},
            )
        )

    if world_tiles is not None or world_address_index is not None:
        roots = {root for root in (world_roots or ()) if root}
        address_index = world_address_index
        if address_index is None:
            address_index = build_map_address_index(
                world_roots=roots,
                tiles=world_tiles or (),
            )
        checked: set[tuple[str, ...]] = set()
        map_paths = [*valid_paths]
        if _valid_path(sleeping_path) and spatial_path_exists(tree, sleeping_path):
            map_paths.append(("sleeping", sleeping_path))
        map_paths.extend(("空间树", list(path)) for path in _iter_leaf_paths(tree))
        for purpose, path in map_paths:
            path_key = tuple(path)
            if path_key in checked:
                continue
            checked.add(path_key)
            if not _map_contains_path(
                path,
                world_roots=roots,
                address_index=address_index,
            ):
                is_tree_path = purpose == "空间树"
                display_path = " > ".join(path)
                issues.append(
                    AgentSpatialIssue(
                        code="AGENT_SPATIAL_MAP_ADDRESS_INVALID",
                        message=(
                            f"Agent 空间“{display_path}”在当前地图中不存在或不可到达"
                            if is_tree_path
                            else f"Agent 地址“{purpose}”（{display_path}）在当前地图中不存在或不可到达"
                        ),
                        purpose=None if is_tree_path else purpose,
                        path=path_key,
                        details={"purpose": purpose, "path": path},
                    )
                )
                # One concrete incompatible address is sufficient to block
                # publication without flooding the report for a wrong map.
                break

    return issues


__all__ = [
    "AgentSpatialIssue",
    "build_map_address_index",
    "spatial_path_exists",
    "validate_agent_spatial",
]
