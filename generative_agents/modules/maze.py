"""generative_agents.maze"""

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
    def __init__(
        self,
        coord,
        world,
        address_keys,
        address=None,
        collision=False,
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
                for key in ("address", "collision")
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

        self.logger = logger
        self._rng = random_source

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
