"""Run-owned participant state; behavior is exclusively supplied by Brain Skills."""
from __future__ import annotations
import copy
import json
from generative_agents.ga_runtime.memory.action import Action
from generative_agents.ga_runtime.memory.event import Event
from generative_agents.ga_runtime.memory.spatial import Spatial
from generative_agents.ga_runtime.engine.profile import ActorProfile

class ActorState:
    def __init__(self, config, maze, conversation, logger, *, clock, random_source):
        self.name = config['name']
        self.agent_key = config.get('agent_key', self.name)
        self.maze, self.conversation, self.logger = maze, conversation, logger
        self._clock, self._rng = clock, random_source
        self._current_step_no = None
        self._result_events = []
        self.percept_config = copy.deepcopy(config['percept'])
        self.spatial = Spatial(**copy.deepcopy(config['spatial']), random_source=random_source)
        self.profile = ActorProfile(copy.deepcopy(config['scratch']), config.get('currently', ''))
        self.status = copy.deepcopy(config.get('status', {}))
        self.plan = copy.deepcopy(config.get('plan', {}))
        self.chats = copy.deepcopy(config.get('chats', []))
        self.coord, self.path = None, []
        initial_coord = tuple(config['coord'])
        if 'action' in config:
            self.action = Action.from_dict(config['action'], clock=clock)
        else:
            address = maze.tile_at(initial_coord).get_address('game_object', as_list=True)
            self.action = Action(Event(self.name, address=address),
                                 Event(address[-1], address=address), clock=clock)
        self.move(initial_coord, copy.deepcopy(config.get('path', [])))

    def abstract(self):
        return {'name': self.name, 'currently': self.profile.currently,
                'tile': self.get_tile().abstract(), 'status': self.status,
                'chats': self.chats, 'action': self.action.abstract()}

    def __str__(self):
        return json.dumps(self.abstract(), ensure_ascii=False, default=str)

    def begin_step(self, step_no):
        if not isinstance(step_no, int) or isinstance(step_no, bool) or step_no < 1:
            raise ValueError('step_no must be a positive integer')
        self._current_step_no = step_no

    def drain_result_events(self):
        events, self._result_events = tuple(self._result_events), []
        return events

    def get_tile(self):
        return self.maze.tile_at(self.coord)

    def get_event(self, as_act=True):
        return self.action.event if as_act else self.action.obj_event

    def to_dict(self, with_action=True):
        value = {'status': copy.deepcopy(self.status), 'chats': copy.deepcopy(self.chats),
                 'currently': self.profile.currently}
        if with_action:
            value['action'] = self.action.to_dict()
        return value

    def move(self, coord, path=None):
        events = {}

        def _update_tile(coord):
            """更新`tile`。

                参数:
                    coord: 地图坐标，按 `(行, 列)` 或项目约定的二维顺序表示。

                返回:
                    返回函数计算得到的结果。
                """
            tile = self.maze.tile_at(coord)
            if not self.action:
                return {}
            if not tile.update_events(self.get_event()):
                tile.add_event(self.get_event())
            obj_event = self.get_event(False)
            if obj_event:
                self.maze.update_obj(coord, obj_event)
            return {e: coord for e in tile.get_events()}
        if self.coord and self.coord != coord:
            tile = self.get_tile()
            tile.remove_events(subject=self.name)
            if tile.has_address('game_object'):
                addr = tile.get_address('game_object')
                self.maze.update_obj(self.coord, Event(addr[-1], address=addr))
            events.update({e: self.coord for e in tile.get_events()})
        events.update(_update_tile(coord))
        self.coord = coord
        self.path = path or []
        return events
