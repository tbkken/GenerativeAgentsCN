"""Identity-scoped use of the simulation provider; return only GA observations."""
from __future__ import annotations

import math


def describe_ground_interfaces(descriptors):
    for item in descriptors:
        name, schema = item["name"], item["parameters"]
        props = schema["properties"]
        if name == "world-perceive":
            props.pop("radius_tiles", None)
            props["radius_m"] = {"type": "number", "minimum": 0}
            item["description"] = "Observe ground-level spatial semantics, visible actors and facilities within radius_m, in metres."
        elif name in {"world-act", "world-navigate"} and "target_coord" in props:
            props["target_coord"] = {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"},
                                    "description": "Ground XYZ position in metres, Z-up."}
            props["target_node_id"] = {"type": "string", "description": "Perceived anchor or instance ID."}
            props.pop("target_address", None)
            if name == "world-navigate":
                schema["oneOf"] = [{"required": ["target_coord"]}, {"required": ["target_node_id"]}]
                item["description"] = "Query one visible XYZ coordinate or observed anchor/instance ID. Returns distance in metres and next waypoint; use the same destination for MOVE."
    return descriptors


def perceive(api, arguments):
    from generative_agents.ga_runtime.capabilities.simworld import SceneUnavailable, point

    scene, observer = api.game.ground, api._observer()
    center = tuple(observer.coord)
    limit = observer.percept_config["vision_r"]
    requested = arguments.get("radius_m", limit)
    if type(requested) not in {int, float} or not math.isfinite(requested) or requested < 0:
        raise ValueError("radius_m must be a finite nonnegative number")
    radius = min(requested, limit)
    bandwidth = observer.percept_config["att_bandwidth"]
    remote = scene.call("observe", {"actor_id": api.memory_owner_key, "vision_radius_m": radius,
                                     "attention_bandwidth": bandwidth})
    if remote.get("actor_id") != api.memory_owner_key or math.dist(point(remote.get("position")), center) > 0.01:
        raise SceneUnavailable("SimWorld 感知的身份或位置与本轮不一致")
    nearby = remote.get("nearby")
    if not isinstance(nearby, list):
        raise SceneUnavailable("SimWorld 感知没有返回可见实体")
    items = {}
    for item in nearby:
        key = item.get("id")
        if not isinstance(key, str) or not key or key == api.memory_owner_key:
            continue
        coord = point(item.get("position"))
        distance = math.dist(center, coord)
        if distance > radius:
            continue
        kind = item.get("kind")
        value = {"id": key, "name": str(item.get("name") or key), "coord": list(coord),
                 "distance_m": round(distance, 4)}
        if kind == "agent":
            group = "nearby_agents"
            value["agent_key"] = key
        else:
            group = "game_objects"
            value.update(object_key=key, interactions=[{"selection_key": f"{key}/interact"}]
                         if item.get("interactable") is True else [])
        items[(group,key)] = (distance, group, key, value)
    # A previous utterance is visible only when its participant is visible and authorized.
    visible = {key for (group,key) in items if group == "nearby_agents"}
    for event in api.game.ground_events.values():
        if event["agent_key"] not in visible or (event["participants"] and api.memory_owner_key not in event["participants"]):
            continue
        distance = math.dist(center, event["coord"])
        if distance <= radius:
            items[("events",event["id"])] = (distance, "events", event["id"],
                                               {k:v for k,v in event.items() if k != "participants"})
    result = {"environment_type": "spatial_3d", "units": "m", "current": {"coord": list(center),
              "address": list(api.iteration.address), "semantics": list(api.iteration.spatial_semantics)},
              "radius_m": radius, "requested_radius_m": requested,
              "vision_radius_m": limit, "attention": {"bandwidth": bandwidth, "candidates": len(items),
              "truncated": len(items)>bandwidth}, "nearby_agents": [], "spatial_nodes": [], "game_objects": [], "events": []}
    for _, group, _, value in sorted(items.values(), key=lambda item: item[:3])[:bandwidth]:
        result[group].append(value)
    return result


def route(api, arguments):
    from generative_agents.ga_runtime.capabilities.simworld import SceneUnavailable, point

    scene, actor = api.game.ground, api._observer()
    start = tuple(actor.coord)
    raw, key = arguments.get("target_coord"), arguments.get("target_node_id")
    if (raw is None) == (key is None):
        raise ValueError("3D navigation requires one target_coord or target_node_id")
    request = {"actor_id": api.memory_owner_key, "vision_radius_m": actor.percept_config["vision_r"]}
    if raw is not None:
        target = point(raw)
        known_destination = actor.path and tuple(actor.path[-1]) == target
        if not known_destination and math.dist(start, target) > actor.percept_config["vision_r"]:
            raise ValueError("target_coord is outside this Agent's visible space")
        request["target"] = list(target)
    else:
        observed = perceive(api, {})
        if key not in {item["id"] for group in ("game_objects", "nearby_agents") for item in observed[group]}:
            raise ValueError("3D navigation target is not perceived by this Agent")
        request["target_id"] = key
    reply = scene.call("navigate", request)
    if reply.get("reachable") is not True:
        return None
    destination = point(reply.get("target"))
    if raw is not None and destination != target:
        raise SceneUnavailable("SimWorld 导航返回了其他目标")
    path = tuple(point(p) for p in reply.get("path", []))
    if path and path[0] == start:
        path = path[1:]
    if path and path[-1] != destination and math.dist(path[-1],destination) <= 0.22:
        # navigate is a disposable capsule probe, not the measured movement of the Run actor.
        path += (destination,)
    if (not path and destination != start) or (path and path[-1] != destination):
        raise SceneUnavailable("SimWorld 路径与导航终点不一致")
    return path


def path_length(start, path):
    return sum(math.dist(a, b) for a,b in zip((start, *path), path))
