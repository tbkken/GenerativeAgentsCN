"""File-only collision composition and deterministic four-neighbour navigation.

Images and semantic regions never imply collisions. Author masks are sampled in
the same rotated footprint as their renderings; map overrides are applied last.
"""

from collections import deque
from math import floor


def grid_path(width, height, blocked, source, target, *, allowed=None):
    """Return an inclusive shortest path; invalid/blocked endpoints return []."""
    source, target = tuple(source), tuple(target)

    def traversable(p):
        x, y = p
        return (0 <= x < width and 0 <= y < height and not blocked(x, y)
                and (allowed is None or allowed(x, y)))

    if not traversable(source) or not traversable(target):
        return []
    frontier, previous = deque([source]), {source: None}
    while frontier:
        current = frontier.popleft()
        if current == target:
            path = []
            while current is not None:
                path.append(current)
                current = previous[current]
            return list(reversed(path))
        x, y = current
        for candidate in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if candidate not in previous and traversable(candidate):
                previous[candidate] = current
                frontier.append(candidate)
    return []


def collision_preview(definition, *, slice_id=None):
    """Compile a map or an unrotated material mask into a boolean grid."""
    editor = definition.get("editor_v2") or {}
    slices = {s["id"]: s for s in editor.get("material_slices", [])}
    canvases = {c["slice_id"]: c for c in editor.get("material_canvases", [])}
    masks, inherited_masks = {}, {}

    def sample(sid, u, v, rotation=None, raw=0):
        s = slices[sid]
        rotation = s.get("rotation_degrees", 0) if rotation is None else rotation
        # Inverse of Canvas rotate -> diagonal flip -> H/V flip.
        if rotation == 90:
            u, v = v, 1 - u
        elif rotation == 180:
            u, v = 1 - u, 1 - v
        elif rotation == 270:
            u, v = 1 - v, u
        if raw & 0x20000000:
            u, v = v, u
        if raw & 0x80000000:
            u = 1 - u
        if raw & 0x40000000:
            v = 1 - v
        w, h, mask = material(sid)
        x, y = min(w - 1, max(0, floor(u * w))), min(h - 1, max(0, floor(v * h)))
        return mask[y * w + x]

    def cell_layers(layers):
        for layer in layers:
            sid, part = layer["slice_id"], layer.get("part")
            if part:
                hit = sample(sid, (part["column"] + .5) / part["columns"],
                             (part["row"] + .5) / part["rows"], part["rotation_degrees"])
            else:
                # One-cell rendering compresses the whole slice. Any occupied
                # subcell must still block the destination cell.
                hit = any(material(sid)[2])
            if hit:
                return True
        return False

    def material(sid):
        if sid in masks:
            return masks[sid]
        s = slices[sid]
        w, h = s["grid_rect"]["width"], s["grid_rect"]["height"]
        values = [False] * (w * h)
        canvas = canvases.get(sid)
        if canvas:
            for index, layers in canvas.get("cells", {}).items():
                values[int(index)] = cell_layers(layers)
        inherited_masks[sid] = values.copy()
        for index, value in (s.get("collision_cells") or {}).items():
            values[int(index)] = value
        masks[sid] = w, h, values
        return masks[sid]

    if slice_id is not None:
        if slice_id not in slices:
            raise ValueError("Unknown material slice")
        w, h, values = material(slice_id)
        return {"width": w, "height": h, "blocked": [i for i, b in enumerate(values) if b],
                "inherited_blocked": [i for i, b in enumerate(inherited_masks[slice_id]) if b]}

    height, width = definition["size"]
    if not 0 < width <= 10_000 or not 0 < height <= 10_000 or width * height > 1_000_000:
        raise ValueError("navigation grid size is invalid or exceeds one million cells")
    navigation = editor.get("navigation")
    if navigation is None:
        # Capture imported collision facts once; never derive this baseline from
        # an already compiled map after editing has started.
        baseline = [t["coord"][1] * width + t["coord"][0]
                    for t in definition.get("tiles", []) if t.get("collision") is True]
        navigation = {"base_blocked": sorted(set(baseline)), "overrides": {}}
        editor["navigation"] = navigation
    if any(not 0 <= int(i) < width * height for i in
           [*navigation["base_blocked"], *navigation["overrides"]]):
        raise ValueError("navigation cell is outside world.definition.size")
    blocked = set(navigation["base_blocked"])
    gids = {s.get("indexed_gid"): s["id"] for s in slices.values() if s.get("indexed_gid")}
    for layer in editor.get("visual_layers", []):
        # Visibility and drawing order do not change physical properties.
        for i, raw in enumerate(layer.get("raw_gids", [])):
            sid = gids.get(raw & 0x1FFFFFFF)
            x, y = i % layer["width"], i // layer["width"]
            if sid and x < width and y < height and any(material(sid)[2]):
                blocked.add(y * width + x)
    for i, layers in editor.get("tile_override_layers", {}).items():
        if cell_layers(layers):
            blocked.add(int(i))
    for i, sid in editor.get("tile_overrides", {}).items():
        if str(i) not in editor.get("tile_override_layers", {}):
            part = editor.get("tile_override_parts", {}).get(str(i))
            if cell_layers([{"slice_id": sid, "part": part}]):
                blocked.add(int(i))
    for node in editor.get("hierarchy_nodes", []):
        sid = node.get("material_slice_id")
        if not sid:
            continue
        s = slices[sid]
        w, h = s["grid_rect"]["width"], s["grid_rect"]["height"]
        if s.get("rotation_degrees", 0) in (90, 270):
            w, h = h, w
        bx, by = node["bounds"]["x"], node["bounds"]["y"]
        for y in range(min(h, height - by)):
            for x in range(min(w, width - bx)):
                if sample(sid, (x + .5) / w, (y + .5) / h):
                    blocked.add((by + y) * width + bx + x)
    inherited = sorted(blocked)
    for index, value in navigation["overrides"].items():
        if value:
            blocked.add(int(index))
        else:
            blocked.discard(int(index))
    return {"width": width, "height": height, "blocked": sorted(blocked),
            "inherited_blocked": inherited}


def compile_collision(definition):
    """Update package Tile facts from validated authoring data, in place."""
    if not definition.get("editor_v2"):
        return
    preview = collision_preview(definition)
    blocked, width = set(preview["blocked"]), preview["width"]
    for tile in definition.get("tiles", []):
        x, y = tile["coord"]
        tile["collision"] = y * width + x in blocked
