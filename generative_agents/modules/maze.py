"""generative_agents.maze"""

import copy
from collections.abc import Mapping
from itertools import product

from generative_agents.modules import utils
from generative_agents.modules.memory.event import Event


class MazeAddressNotFoundError(RuntimeError):
    """Raised instead of silently sending an Agent to an unrelated map tile."""

    code = "AGENT_SPATIAL_MAP_ADDRESS_INVALID"


_ADDRESS_LEVEL_ALIASES = {
    # The map workspace persists its deepest hierarchy level as ``object``.
    # The legacy simulation runtime calls the same level ``game_object``.
    # Keep both public contracts valid at the Tile boundary so editor-built
    # maps do not need to rewrite their immutable published definitions.
    "game_object": "object",
    "object": "game_object",
}


class Tile:
    """仿真地图中的一个网格，保存碰撞、语义地址和当前事件。"""

    def __init__(
        self,
        coord,
        world,
        address_keys,
        address=None,
        collision=False,
        spatial_semantics=None,
    ):
        # in order: world, sector, arena, game_object
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            coord: 地图坐标，按 `(行, 列)` 或项目约定的二维顺序表示。
            world: 当前运行使用的世界配置或运行时世界对象。
            address_keys: 本次操作涉及的空间地址键集合。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。 默认值：`None`。
            collision: 路径或移动过程中检测到的碰撞信息。 默认值：`False`。

        返回:
            无返回值。
        """
        self.coord = coord
        self.address = [world]
        if address:
            address = list(address)
            # Editor-v2 stores a complete hierarchy path, including the world,
            # while legacy runtime maps store only the levels below it.  Strip
            # the duplicated root at this adapter boundary before building the
            # runtime address indexes.
            if address and address[0] == world:
                address = address[1:]
            self.address += address
        self.address_keys = address_keys
        self.address_map = dict(zip(address_keys[: len(self.address)], self.address))
        self.collision = collision
        self.spatial_semantics = tuple(spatial_semantics or ())
        self.event_cnt = 0
        self._events = {}
        if len(self.address) == 4:
            self.add_event(Event(self.address[-1], address=self.address))

    def abstract(self):
        """执行 `Tile` 的`abstract`操作。

        返回:
            返回函数计算得到的结果。
        """
        address = ":".join(self.address)
        if self.collision:
            address += "(collision)"
        return {
            "coord[{},{}]".format(self.coord[0], self.coord[1]): address,
            "spatial_semantics": [dict(item) for item in self.spatial_semantics],
            "events": {k: str(v) for k, v in self.events.items()},
        }

    def __str__(self):
        """执行`str`的内部处理，供当前模块或类复用。

        返回:
            返回函数计算得到的结果。
        """
        return utils.dump_dict(self.abstract())

    def __eq__(self, other):
        """执行`eq`的内部处理，供当前模块或类复用。

        参数:
            other: 当前操作使用的`other`。

        返回:
            返回函数计算得到的结果。
        """
        if isinstance(other, Tile):
            return hash(self.coord) == hash(other.coord)
        return False

    def get_events(self):
        """获取`events`。

        返回:
            返回函数计算得到的结果。
        """
        return self.events.values()

    def add_event(self, event):
        """执行 `Tile` 的`add`事件操作。

        参数:
            event: 当前感知、处理或写入结果账本的领域事件。

        返回:
            返回函数计算得到的结果。
        """
        if isinstance(event, (tuple, list)):
            event = Event.from_list(event)
        if all(e != event for e in self._events.values()):
            self._events["e_" + str(self.event_cnt)] = event
            self.event_cnt += 1
        return event

    def remove_events(self, subject=None, event=None):
        """移除`events`。

        参数:
            subject: 事件三元组中的主体，通常是智能体或世界对象标识。 默认值：`None`。
            event: 当前感知、处理或写入结果账本的领域事件。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        r_events = {}
        for tag, eve in self._events.items():
            if subject and eve.subject == subject:
                r_events[tag] = eve
            if event and eve == event:
                r_events[tag] = eve
        for r_eve in r_events:
            self._events.pop(r_eve)
        return r_events

    def update_events(self, event, match="subject"):
        """更新`events`。

        参数:
            event: 当前感知、处理或写入结果账本的领域事件。
            match: 传入当前算法的`match`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`'subject'`。

        返回:
            返回函数计算得到的结果。
        """
        u_events = {}
        for tag, eve in self._events.items():
            if match == "subject" and eve.subject == event.subject:
                self._events[tag] = event
                u_events[tag] = event
        return u_events

    def has_address(self, key):
        """判断是否存在`address`。

        参数:
            key: 用于定位目标记录、配置项或技能的稳定键。

        返回:
            返回函数计算得到的结果。
        """
        if key in self.address_map:
            return True
        return _ADDRESS_LEVEL_ALIASES.get(key) in self.address_map

    def get_address(self, level=None, as_list=True):
        """获取`address`。

        参数:
            level: 日志级别、树层级或重要性等级。 默认值：`None`。
            as_list: 是否以列表形式返回结果；否则返回调用方要求的结构。 默认值：`True`。

        返回:
            返回函数计算得到的结果。
        """
        level = level or self.address_keys[-1]
        requested_level = level
        if level not in self.address_keys:
            level = _ADDRESS_LEVEL_ALIASES.get(level, level)
        assert level in self.address_keys, "Can not find {} from {}".format(
            requested_level, self.address_keys
        )
        pos = self.address_keys.index(level) + 1
        if as_list:
            return self.address[:pos]
        return ":".join(self.address[:pos])

    def get_addresses(self):
        """获取`addresses`。

        返回:
            返回函数计算得到的结果。
        """
        addresses = []
        if len(self.address) > 1:
            addresses = [
                ":".join(self.address[:i]) for i in range(2, len(self.address) + 1)
            ]
        return addresses

    @property
    def events(self):
        """执行 `Tile` 的`events`操作。

        返回:
            返回函数计算得到的结果。
        """
        return self._events

    @property
    def is_empty(self):
        """判断是否`empty`。

        返回:
            返回函数计算得到的结果。
        """
        return len(self.address) == 1 and not self._events


class Maze:
    """运行时地图索引，负责坐标查询、寻路、邻域和语义地址解析。"""

    def __init__(self, config, logger, random_source):
        # define tiles
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。
            logger: 记录运行诊断信息的日志器。
            random_source: 运行私有的伪随机数生成器，用于保证快照恢复后的确定性。

        返回:
            无返回值。
        """
        self.maze_height, self.maze_width = config["size"]
        self.tile_size = config["tile_size"]
        address_keys = config["tile_address_keys"]
        self.tiles = [
            [
                Tile((x, y), config["world"], address_keys)
                for x in range(self.maze_width)
            ]
            for y in range(self.maze_height)
        ]
        for tile_definition in config["tiles"]:
            x, y = tile_definition["coord"]
            # Public-map editors may persist presentation metadata such as the
            # palette ``tile`` key beside runtime fields.  Keep the Maze
            # boundary explicit so editor extensions cannot leak unexpected
            # keyword arguments into the runtime Tile contract.
            tile_attributes = {
                key: tile_definition[key]
                for key in ("address", "collision", "spatial_semantics")
                if key in tile_definition
            }
            self.tiles[y][x] = Tile(
                (x, y), config["world"], address_keys, **tile_attributes
            )

        # define address
        self.address_tiles = dict()
        for i in range(self.maze_height):
            for j in range(self.maze_width):
                for add in self.tile_at([j, i]).get_addresses():
                    self.address_tiles.setdefault(add, set()).add((j, i))

        # Spatial semantics are authored as hierarchy nodes, while Tiles are a
        # rendering, collision and path-finding representation.  Build a
        # run-private semantic index once so Agent perception can query unique
        # nodes without serialising the same World/Sector/Arena description for
        # every visible Tile.
        self._semantic_bucket_size = 16
        self._semantic_nodes = self._build_semantic_nodes(config)
        self._semantic_buckets: dict[tuple[int, int], set[str]] = {}
        self._broad_semantic_node_ids: set[str] = set()
        self._index_semantic_nodes()

        self.logger = logger
        self._rng = random_source

    def _build_semantic_nodes(self, config) -> dict[str, dict]:
        """Materialize one immutable runtime record for each semantic node."""

        editor = config.get("editor_v2")
        raw_nodes = (
            editor.get("hierarchy_nodes") if isinstance(editor, Mapping) else None
        )
        if isinstance(raw_nodes, list) and raw_nodes:
            nodes = {
                str(item.get("id")): item
                for item in raw_nodes
                if isinstance(item, Mapping) and str(item.get("id") or "").strip()
            }

            def address_for(node):
                parts = []
                current = node
                seen = set()
                while isinstance(current, Mapping):
                    node_id = str(current.get("id") or "")
                    if not node_id or node_id in seen:
                        break
                    seen.add(node_id)
                    parts.append(str(current.get("name") or node_id))
                    parent_id = str(current.get("parent_id") or "")
                    current = nodes.get(parent_id) if parent_id else None
                return list(reversed(parts))

            result = {}
            for node_id, node in nodes.items():
                bounds = node.get("bounds") or {}
                x = int(bounds.get("x", 0))
                y = int(bounds.get("y", 0))
                width = max(1, int(bounds.get("width", 1)))
                height = max(1, int(bounds.get("height", 1)))
                result[node_id] = {
                    "id": node_id,
                    "kind": str(node.get("kind") or "").upper(),
                    "name": str(node.get("name") or node_id),
                    "semantic": str(node.get("semantic") or ""),
                    "parent_id": str(node.get("parent_id") or "") or None,
                    "address": address_for(node),
                    "bounds": {
                        "x": x,
                        "y": y,
                        "width": width,
                        "height": height,
                    },
                }
            return result

        # Legacy maps have no hierarchy document.  Derive the same index once
        # at Maze construction time; perception remains semantic-first after
        # this compatibility boundary.
        derived: dict[str, dict] = {}
        extents: dict[str, list[int]] = {}
        for row in self.tiles:
            for tile in row:
                parent_id = None
                for level, item in enumerate(tile.spatial_semantics):
                    if not isinstance(item, Mapping):
                        continue
                    address = list(tile.address[: level + 1])
                    kind = str(item.get("kind") or "").upper()
                    node_id = str(item.get("id") or "").strip()
                    if not node_id:
                        node_id = "legacy:{}:{}".format(kind, ":".join(address))
                    if node_id not in derived:
                        derived[node_id] = {
                            "id": node_id,
                            "kind": kind,
                            "name": str(item.get("name") or address[-1]),
                            "semantic": str(item.get("semantic") or ""),
                            "parent_id": parent_id,
                            "address": address,
                        }
                        extents[node_id] = [
                            int(tile.coord[0]),
                            int(tile.coord[1]),
                            int(tile.coord[0]),
                            int(tile.coord[1]),
                        ]
                    else:
                        extent = extents[node_id]
                        extent[0] = min(extent[0], int(tile.coord[0]))
                        extent[1] = min(extent[1], int(tile.coord[1]))
                        extent[2] = max(extent[2], int(tile.coord[0]))
                        extent[3] = max(extent[3], int(tile.coord[1]))
                    parent_id = node_id
        for node_id, node in derived.items():
            x_min, y_min, x_max, y_max = extents[node_id]
            node["bounds"] = {
                "x": x_min,
                "y": y_min,
                "width": x_max - x_min + 1,
                "height": y_max - y_min + 1,
            }
        return derived

    def _index_semantic_nodes(self) -> None:
        """Index hierarchy bounds into coarse buckets for bounded range queries."""

        bucket_size = self._semantic_bucket_size
        for node_id, node in self._semantic_nodes.items():
            bounds = node["bounds"]
            raw_x = int(bounds["x"])
            raw_y = int(bounds["y"])
            width = max(1, int(bounds["width"]))
            height = max(1, int(bounds["height"]))
            x_min = max(0, raw_x)
            y_min = max(0, raw_y)
            x_max = min(
                self.maze_width - 1,
                raw_x + width - 1,
            )
            y_max = min(
                self.maze_height - 1,
                raw_y + height - 1,
            )
            if x_max < x_min or y_max < y_min:
                continue
            x_buckets = range(x_min // bucket_size, x_max // bucket_size + 1)
            y_buckets = range(y_min // bucket_size, y_max // bucket_size + 1)
            # A World-sized node should not be copied into every bucket of a
            # very large map.  Broad nodes are checked once per query instead.
            if len(x_buckets) * len(y_buckets) > 1024:
                self._broad_semantic_node_ids.add(node_id)
                continue
            for bucket in product(x_buckets, y_buckets):
                self._semantic_buckets.setdefault(bucket, set()).add(node_id)

    @staticmethod
    def _distance_to_bounds(coord, bounds) -> float:
        """Return Chebyshev Tile distance from a coordinate to a node rectangle."""

        x = float(coord[0])
        y = float(coord[1])
        x_min = float(bounds["x"])
        y_min = float(bounds["y"])
        x_max = x_min + max(1.0, float(bounds["width"])) - 1.0
        y_max = y_min + max(1.0, float(bounds["height"])) - 1.0
        dx = max(x_min - x, 0.0, x - x_max)
        dy = max(y_min - y, 0.0, y - y_max)
        return max(dx, dy)

    def semantic_nodes_in_scope(self, coord, radius) -> list[dict]:
        """Return unique hierarchy nodes intersecting one box-shaped view."""

        center = (int(coord[0]), int(coord[1]))
        radius = max(0, int(radius))
        bucket_size = self._semantic_bucket_size
        x_min = max(0, center[0] - radius)
        x_max = min(self.maze_width - 1, center[0] + radius)
        y_min = max(0, center[1] - radius)
        y_max = min(self.maze_height - 1, center[1] + radius)
        candidate_ids = set(self._broad_semantic_node_ids)
        for bucket in product(
            range(x_min // bucket_size, x_max // bucket_size + 1),
            range(y_min // bucket_size, y_max // bucket_size + 1),
        ):
            candidate_ids.update(self._semantic_buckets.get(bucket, ()))

        current_ids = {
            str(item.get("id") or "")
            for item in self.tile_at(center).spatial_semantics
            if isinstance(item, Mapping)
        }
        kind_order = {"WORLD": 0, "SECTOR": 1, "ARENA": 2, "GAME_OBJECT": 3}
        observed = []
        for node_id in candidate_ids:
            node = self._semantic_nodes[node_id]
            distance = self._distance_to_bounds(center, node["bounds"])
            if distance > radius:
                continue
            item = copy.deepcopy(node)
            item["distance_tiles"] = round(distance, 3)
            item["relation"] = "CURRENT" if node_id in current_ids else "NEARBY"
            observed.append(item)
        return sorted(
            observed,
            key=lambda item: (
                item["relation"] != "CURRENT",
                item["distance_tiles"],
                kind_order.get(item["kind"], 99),
                item["id"],
            ),
        )

    def events_in_scope(self, coord, radius) -> list[dict]:
        """Return distinct world facts in range, retaining their nearest coordinate."""

        center = (int(coord[0]), int(coord[1]))
        radius = max(0, int(radius))
        observed = {}
        for tile in self.get_scope(center, {"vision_r": radius, "mode": "box"}):
            distance = max(
                abs(int(tile.coord[0]) - center[0]),
                abs(int(tile.coord[1]) - center[1]),
            )
            for event in tile.get_events():
                event_payload = event.to_dict()
                fingerprint = (
                    str(event_payload.get("subject") or ""),
                    str(event_payload.get("predicate") or ""),
                    str(event_payload.get("object") or ""),
                    str(event_payload.get("describe") or ""),
                    tuple(event_payload.get("address") or ()),
                )
                previous = observed.get(fingerprint)
                if previous is not None and previous[0] <= distance:
                    continue
                item = copy.deepcopy(event_payload)
                item["coord"] = [int(tile.coord[0]), int(tile.coord[1])]
                item["distance_tiles"] = float(distance)
                observed[fingerprint] = (distance, item)
        return [
            item
            for _, item in sorted(
                observed.values(),
                key=lambda pair: (
                    pair[0],
                    str(pair[1].get("subject") or ""),
                    str(pair[1].get("predicate") or ""),
                    str(pair[1].get("object") or ""),
                ),
            )
        ]

    def find_path(self, src_coord, dst_coord):
        """在地图可通行区域内搜索从起点到终点的移动路径。

        参数:
            src_coord: `src`对应的二维地图坐标。
            dst_coord: `dst`对应的二维地图坐标。

        返回:
            返回函数计算得到的结果。
        """
        map = [[0 for _ in range(self.maze_width)] for _ in range(self.maze_height)]
        frontier, visited = [src_coord], set()
        map[src_coord[1]][src_coord[0]] = 1
        while map[dst_coord[1]][dst_coord[0]] == 0:
            new_frontier = []
            for f in frontier:
                for c in self.get_around(f):
                    if (
                        0 < c[0] < self.maze_width - 1
                        and 0 < c[1] < self.maze_height - 1
                        and map[c[1]][c[0]] == 0
                        and c not in visited
                    ):
                        map[c[1]][c[0]] = map[f[1]][f[0]] + 1
                        new_frontier.append(c)
                        visited.add(c)
            if not new_frontier:
                return []
            frontier = new_frontier
        step = map[dst_coord[1]][dst_coord[0]]
        path = [dst_coord]
        while step > 1:
            for c in self.get_around(path[-1]):
                if map[c[1]][c[0]] == step - 1:
                    path.append(c)
                    break
            step -= 1
        return path[::-1]

    def tile_at(self, coord):
        """执行 `Maze` 的`tile``at`操作。

        参数:
            coord: 地图坐标，按 `(行, 列)` 或项目约定的二维顺序表示。

        返回:
            返回函数计算得到的结果。
        """
        return self.tiles[coord[1]][coord[0]]

    def update_obj(self, coord, obj_event):
        """更新`obj`。

        参数:
            coord: 地图坐标，按 `(行, 列)` 或项目约定的二维顺序表示。
            obj_event: 世界对象因当前行为产生的状态事件。

        返回:
            无返回值。
        """
        tile = self.tile_at(coord)
        if not tile.has_address("game_object"):
            return
        if obj_event.address != tile.get_address("game_object"):
            return
        addr = ":".join(obj_event.address)
        if addr not in self.address_tiles:
            return
        for c in self.address_tiles[addr]:
            self.tile_at(c).update_events(obj_event)

    def get_scope(self, coord, config):
        """获取`scope`。

        参数:
            coord: 地图坐标，按 `(行, 列)` 或项目约定的二维顺序表示。
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。

        返回:
            返回函数计算得到的结果。
        """
        coords = []
        vision_r = config["vision_r"]
        if config["mode"] == "box":
            x_range = [
                max(coord[0] - vision_r, 0),
                min(coord[0] + vision_r + 1, self.maze_width),
            ]
            y_range = [
                max(coord[1] - vision_r, 0),
                min(coord[1] + vision_r + 1, self.maze_height),
            ]
            coords = list(product(list(range(*x_range)), list(range(*y_range))))
        return [self.tile_at(c) for c in coords]

    def get_around(self, coord, no_collision=True):
        """获取`around`。

        参数:
            coord: 地图坐标，按 `(行, 列)` 或项目约定的二维顺序表示。
            no_collision: 是否要求路径或放置结果完全不存在碰撞。 默认值：`True`。

        返回:
            返回函数计算得到的结果。
        """
        coords = [
            (coord[0] - 1, coord[1]),
            (coord[0] + 1, coord[1]),
            (coord[0], coord[1] - 1),
            (coord[0], coord[1] + 1),
        ]
        if no_collision:
            coords = [c for c in coords if not self.tile_at(c).collision]
        return coords

    def get_address_tiles(self, address):
        """获取`address``tiles`。

        参数:
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。

        返回:
            返回函数计算得到的结果。

        异常:
            MazeAddressNotFoundError: 当底层操作报告该异常条件时抛出。
        """
        addr = ":".join(address)
        if addr in self.address_tiles:
            return self.address_tiles[addr]
        raise MazeAddressNotFoundError(f"当前地图中不存在可到达地址“{addr}”")
