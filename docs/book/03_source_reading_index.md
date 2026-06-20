# GenerativeAgentsCN 源码精读索引 v0.1

## 文档目标

本文件用于规划全书第三部分“源码深读”的阅读顺序和讲解边界。

用户已明确要求源码讲解达到核心源码逐行级别，因此本文件不是普通目录，而是后续写作的施工图：

- 每一章读哪些文件。
- 每一章读哪些类和函数。
- 每个函数为什么要读。
- 哪些代码需要逐行精读。
- 哪些代码只需要模块级说明。
- 每章应画哪些图。
- 每章要连接论文中的哪个概念。
- 每章结束时读者应掌握什么。

## 与前两个文件的关系

- `01_original_paper_outline.md`
  - 定义论文与思想源头。
- `02_paper_to_code_mapping.md`
  - 建立论文概念到项目源码的映射。
- `03_source_reading_index.md`
  - 将映射进一步转成源码精读路线。

写第三部分正文时，必须同时参考这三个文件，不能脱离论文概念单独讲代码。

## 源码精读原则

1. 先讲数据流，再讲函数细节。
   - 读者先知道数据从哪里来、到哪里去，再读函数内部逻辑。
2. 先讲 Agent 外部世界，再讲 Agent 内部大脑。
   - 世界、时间、地图、事件是前提。
3. `agent.py` 不要从第一行一路硬读。
   - 应按感知、日程、反应、反思、行动四条线拆开讲。
4. Prompt 文件必须当作源码讲。
   - GenerativeAgentsCN 的行为很大一部分由 `data/prompts/*.txt` 决定。
5. 示例数据必须进入讲解。
   - 角色 `agent.json`、checkpoint、`simulation.md`、`movement.json` 都是理解源码的证据。
6. 对差异要诚实。
   - 当前项目没有完整实现论文中的所有交互能力，不能写成已经具备。

## 精读深度分级

### L1：模块级阅读

只讲职责、输入输出、与其他模块关系。

适用对象：

- 静态资源文件。
- 前端展示细节。
- 工具函数中的简单包装。

### L2：函数级阅读

讲函数调用链、核心分支、关键数据结构。

适用对象：

- `start.py`
- `game.py`
- `compress.py`
- 部分前端回放逻辑。

### L3：逐行级精读

逐段拆解变量、分支、循环、对象状态变化和异常兜底。

适用对象：

- `Agent.think()`
- `Agent.make_schedule()`
- `Agent.percept()`
- `Agent._determine_action()`
- `Agent._reaction()`
- `Agent._chat_with()`
- `Agent.reflect()`
- `AssociateRetriever._retrieve()`
- `Associate.add_node()`
- `Schedule.current_plan()`
- `Scratch` 中关键 prompt 方法。
- `LLMModel.completion()` 与结构化解析。

---

## 第三部分章节结构

第三部分计划覆盖第 11-20 章：

11. 世界模型：地图、Tile、地址树、空间记忆
12. 智能体初始化：角色设定如何进入系统
13. 仿真循环：`start.py`、`Game`、`Agent.think()`
14. 感知：智能体如何看见附近事件
15. 记忆：事件、对话、想法如何存储和检索
16. 日程：从一天计划到具体动作
17. 社交：聊天、等待、关系传播
18. 反思：重要事件如何变成长期认知
19. 模型适配：Ollama、MiniMax、OpenAI、结构化输出
20. 回放系统：checkpoint、`movement.json`、`simulation.md`、Phaser 前端

---

## 第 11 章：世界模型：地图、Tile、地址树、空间记忆

### 对应论文概念

- Smallville
- Sandbox Environment
- Sandbox Grounding
- Spatial Memory

### 章节目标

让读者理解：智能体不是漂浮在文本中的角色，而是生活在一个由坐标、地点、房间、对象和事件组成的世界中。

### 阅读入口

优先阅读：

1. `generative_agents/frontend/static/assets/village/maze.json`
2. `generative_agents/modules/maze.py`
3. `generative_agents/modules/memory/spatial.py`
4. `generative_agents/frontend/static/assets/village/tilemap/tilemap.json`
5. `generative_agents/frontend/static/assets/village/agents/伊莎贝拉/agent.json`

### 核心源码锚点

`generative_agents/modules/maze.py`

- `Tile`
- `Tile.__init__()`
- `Tile.add_event()`
- `Tile.remove_events()`
- `Tile.update_events()`
- `Tile.get_address()`
- `Tile.get_addresses()`
- `Maze`
- `Maze.__init__()`
- `Maze.find_path()`
- `Maze.tile_at()`
- `Maze.update_obj()`
- `Maze.get_scope()`
- `Maze.get_around()`
- `Maze.get_address_tiles()`

`generative_agents/modules/memory/spatial.py`

- `Spatial`
- `Spatial.__init__()`
- `Spatial.add_leaf()`
- `Spatial.find_address()`
- `Spatial.get_leaves()`
- `Spatial.random_address()`

### 精读顺序

1. 先读 `maze.json` 的结构：
   - `size`
   - `tile_size`
   - `world`
   - `tile_address_keys`
   - `tiles`
2. 读 `Tile.__init__()`：
   - 坐标如何保存。
   - 地址如何拆成 `world / sector / arena / game_object`。
   - 初始对象事件如何创建。
3. 读 `Maze.__init__()`：
   - 如何创建全图空 tile。
   - 如何用配置覆盖具体 tile。
   - 如何构建 `address_tiles` 索引。
4. 读 `Maze.find_path()`：
   - 使用广度优先方式寻找路径。
   - 如何通过 `get_around()` 避开碰撞。
5. 读 `Maze.get_scope()`：
   - 感知范围如何生成。
   - 为什么 box 模式意味着智能体不是全知。
6. 读 `Spatial`：
   - 角色自己知道哪些地点。
   - 地点知识如何随着感知扩展。

### 需要逐行精读的函数

- `Tile.__init__()`
- `Maze.__init__()`
- `Maze.find_path()`
- `Maze.get_scope()`
- `Spatial.find_address()`
- `Spatial.get_leaves()`

### 只需模块级说明的内容

- `tilemap.json` 的完整图层细节。
- PNG tilemap 资源。

### 图表建议

- 地图双层结构图：
  - Python 后端 `maze.json`
  - Phaser 前端 `tilemap.json`
- 地址层级图：
  - world -> sector -> arena -> game_object
- Tile 数据结构图：
  - coord / address / collision / events
- 寻路流程图：
  - source -> frontier -> visited -> target -> reversed path

### 必讲数据样例

使用伊莎贝拉的 `agent.json` 展示：

- 她的初始坐标。
- 她的 living area。
- 她知道的 `spatial.tree`。
- 她如何从“睡觉”映射到“床”。

### 易错点

1. 不要把前端地图和后端地图混为一谈。
2. 不要把 `Spatial` 理解成全局地图。
   - 它是角色自己的空间记忆。
3. 不要以为所有地点一开始都被角色知道。
   - `Agent.percept()` 会把感知到的新对象写入 `Spatial`。

### 本章结束读者应掌握

- 小镇空间如何被 Python 表示。
- 地址树如何连接自然语言计划和具体地点。
- 智能体如何从坐标、tile、address 中定位自己。

---

## 第 12 章：智能体初始化：角色设定如何进入系统

### 对应论文概念

- Agent Persona
- Initial Memory
- Current Status
- Natural Language Agent Description

### 章节目标

让读者理解一个角色是如何从 `agent.json` 变成运行中的 `Agent` 对象。

### 阅读入口

1. `generative_agents/frontend/static/assets/village/agents/伊莎贝拉/agent.json`
2. `generative_agents/data/config.json`
3. `generative_agents/start.py`
4. `generative_agents/modules/game.py`
5. `generative_agents/modules/agent.py`
6. `generative_agents/modules/prompt/scratch.py`

### 核心源码锚点

`generative_agents/start.py`

- `personas`
- `get_config()`
- `get_config_from_log()`

`generative_agents/modules/game.py`

- `Game.__init__()`
- `Game.load_static()`
- `Game.reset_game()`

`generative_agents/modules/agent.py`

- `Agent.__init__()`
- `Agent.reset()`
- `Agent.abstract()`
- `Agent.to_dict()`

`generative_agents/modules/prompt/scratch.py`

- `Scratch.__init__()`
- `Scratch._base_desc()`
- `Scratch.build_prompt()`

### 精读顺序

1. 读 `data/config.json`：
   - 全局 agent base 配置。
   - percept / think / associate / schedule。
2. 读单个角色 `agent.json`：
   - `currently`
   - `scratch`
   - `spatial`
   - `coord`
3. 读 `start.py:get_config()`：
   - 如何把全局配置和角色配置组织成 simulation config。
4. 读 `Game.__init__()`：
   - 如何加载地图。
   - 如何合并 `agent_base` 和每个角色配置。
   - 如何创建每个 `Agent`。
5. 读 `Agent.__init__()`：
   - 名字、地图、对话引用。
   - 感知配置、思考配置、对话轮数。
   - 空间记忆、日程、关联记忆。
   - scratch prompt 状态。
   - 当前 action 和初始 tile 事件。
   - `move()` 如何把角色放进地图。
6. 读 `Scratch._base_desc()`：
   - 人设如何变成每次 prompt 的公共上下文。

### 需要逐行精读的函数

- `get_config()`
- `Game.__init__()`
- `Agent.__init__()`
- `Scratch._base_desc()`

### 图表建议

- 配置合并图：
  - `data/config.json` + `agent.json` -> `agent_config` -> `Agent`
- Agent 对象结构图：
  - spatial / schedule / associate / scratch / status / action
- Prompt 基础描述构造图：
  - scratch 字段 + currently + date -> base_desc

### 必讲数据样例

用伊莎贝拉展示：

- `currently` 中的情人节派对。
- `daily_plan` 中的咖啡馆经营。
- `spatial.address.living_area`。
- 这些信息如何进入 `base_desc.txt`。

### 易错点

1. `currently` 不是当前动作。
   - 它是角色当前背景状态。
2. `daily_plan` 不是当天生成的完整日程。
   - 它是人设中的生活习惯描述。
3. `agent_base` 和 `agent.json` 有覆盖关系。
   - 写作时要把合并顺序讲清楚。

### 本章结束读者应掌握

- 一个静态角色文件如何进入运行时对象。
- 人设如何影响后续所有 LLM 调用。
- 中文化为什么不只是翻译名字。

---

## 第 13 章：仿真循环：`start.py`、`Game`、`Agent.think()`

### 对应论文概念

- Agent Runtime Loop
- Time Step
- Simulation Environment
- Observation-Planning-Action Loop

### 章节目标

让读者看到整个项目如何一轮一轮推进，不陷入单个函数细节。

### 阅读入口

1. `generative_agents/start.py`
2. `generative_agents/modules/utils/timer.py`
3. `generative_agents/modules/game.py`
4. `generative_agents/modules/agent.py`

### 核心源码锚点

`start.py`

- `SimulateServer`
- `SimulateServer.__init__()`
- `SimulateServer.simulate()`
- `get_config()`
- `get_config_from_log()`

`timer.py`

- `Timer`
- `Timer.forward()`
- `Timer.get_date()`
- `Timer.daily_duration()`
- `Timer.daily_time()`
- `set_timer()`
- `get_timer()`

`game.py`

- `create_game()`
- `Game.agent_think()`
- `Game.reset_game()`

`agent.py`

- `Agent.think()`

### 精读顺序

1. 读命令行入口：
   - `--name`
   - `--start`
   - `--resume`
   - `--step`
   - `--stride`
   - `--agent-count`
   - `--agents`
2. 读 `SimulateServer.__init__()`：
   - checkpoint 目录。
   - conversation 加载。
   - logger。
   - `create_game()`。
   - `agent_status` 初始化。
3. 读 `create_game()`：
   - 设置全局 timer。
   - 创建全局 Game。
4. 读 `SimulateServer.simulate()`：
   - 外层 step 循环。
   - 内层 agent 循环。
   - 调用 `Game.agent_think()`。
   - 保存 agent 状态。
   - 写 checkpoint。
   - 写 conversation。
   - `timer.forward(stride)`。
5. 读 `Game.agent_think()`：
   - 调用 `Agent.think()`。
   - 提取 info。
   - 记录日志。
6. 读 `Agent.think()` 做高层浏览：
   - move。
   - make_schedule。
   - sleep 判断。
   - percept。
   - make_plan。
   - reflect。
   - find_path。

### 需要逐行精读的函数

- `SimulateServer.simulate()`
- `Game.agent_think()`
- `Agent.think()`
- `Timer.forward()`
- `Timer.daily_duration()`

### 图表建议

- 主循环图：
  - step -> each agent -> think -> save -> time forward
- 时间推进图：
  - real loop step vs simulated time stride
- checkpoint 写入图：
  - config -> simulate-*.json
  - conversation -> conversation.json

### 易错点

1. 一个 step 中会依次处理多个智能体，不是并行。
2. `status["coord"]` 与 `Agent.coord` 有交互。
3. `stride` 是小镇时间，不是程序 sleep 时间。
4. resume 不是继续某个 Python 进程，而是从 checkpoint 重建 config。

### 本章结束读者应掌握

- 项目如何从命令行启动。
- 每一步仿真到底发生什么。
- checkpoint 和断点恢复为什么可行。

---

## 第 14 章：感知：智能体如何看见附近事件

### 对应论文概念

- Observation
- Perception
- Memory Stream Entry
- Attention Bandwidth

### 章节目标

解释智能体如何从地图读取附近事件，并把新事件变成记忆。

### 阅读入口

1. `generative_agents/modules/agent.py`
2. `generative_agents/modules/maze.py`
3. `generative_agents/modules/memory/event.py`
4. `generative_agents/modules/memory/associate.py`

### 核心源码锚点

`agent.py`

- `Agent.move()`
- `Agent.percept()`
- `Agent._add_concept()`
- `Agent.get_tile()`
- `Agent.get_event()`

`maze.py`

- `Maze.get_scope()`
- `Tile.get_events()`
- `Tile.update_events()`
- `Tile.add_event()`
- `Tile.remove_events()`

`event.py`

- `Event`
- `Event.get_describe()`
- `Event.fit()`

### 精读顺序

1. 先回顾 `Agent.think()` 中感知发生的位置。
2. 读 `Agent.move()`：
   - 角色离开旧 tile 时移除旧事件。
   - 角色进入新 tile 时更新事件。
   - 对象事件如何同步。
3. 读 `Maze.get_scope()`：
   - 通过 `vision_r` 生成视野范围。
4. 读 `Agent.percept()`：
   - 视野范围内对象加入 `Spatial`。
   - 收集同一 arena 中的事件。
   - 根据距离排序。
   - 过滤最近记忆中已存在的事件。
   - 将有效事件写入 `Associate`。
   - 累加 poignancy。
5. 读 `Event.get_describe()`：
   - 事件如何变成自然语言。

### 需要逐行精读的函数

- `Agent.move()`
- `Agent.percept()`
- `Event.get_describe()`

### 图表建议

- 感知范围图：
  - 当前 coord + vision_r -> scope tiles
- 感知到记忆图：
  - tile events -> sorted events -> Concept -> Associate
- 事件去重图：
  - current event vs recent retrieve_events/retrieve_chats

### 易错点

1. 感知不是读取全图。
2. 感知只处理同一 arena 的事件。
3. idle / 空闲事件也可能进入 concepts，但重要性低。
4. `self.concepts` 是当前感知结果，不是长期记忆本身。

### 本章结束读者应掌握

- 观察如何进入 Agent。
- 事件如何变成概念。
- 感知为什么会影响后续 reaction 和 reflection。

---

## 第 15 章：记忆：事件、对话、想法如何存储和检索

### 对应论文概念

- Memory Stream
- Retrieval
- Recency
- Importance
- Relevance

### 章节目标

完整讲清 GenerativeAgentsCN 的长期记忆系统。

### 阅读入口

1. `generative_agents/modules/memory/event.py`
2. `generative_agents/modules/memory/associate.py`
3. `generative_agents/modules/storage/index.py`
4. `generative_agents/modules/agent.py`
5. `generative_agents/data/prompts/poignancy_event.txt`
6. `generative_agents/data/prompts/poignancy_chat.txt`

### 核心源码锚点

`associate.py`

- `Concept`
- `Concept.from_node()`
- `Concept.from_event()`
- `AssociateRetriever`
- `AssociateRetriever._retrieve()`
- `Associate`
- `Associate.cleanup_index()`
- `Associate.add_node()`
- `Associate._retrieve_nodes()`
- `Associate.retrieve_events()`
- `Associate.retrieve_thoughts()`
- `Associate.retrieve_chats()`
- `Associate.retrieve_focus()`
- `Associate.get_relation()`
- `Associate.to_dict()`

`storage/index.py`

- `OllamaHttpEmbedding`
- `LlamaIndex`
- `LlamaIndex.add_node()`
- `LlamaIndex.cleanup()`
- `LlamaIndex.retrieve()`
- `LlamaIndex.save()`

`agent.py`

- `Agent._add_concept()`

### 精读顺序

1. 读 `Event`：
   - 世界事件的最小表示。
2. 读 `Concept`：
   - 进入记忆后的事件节点。
   - `node_type`
   - `poignancy`
   - `create / expire / access`
3. 读 `LlamaIndex`：
   - embedding provider 如何创建。
   - 节点如何插入。
   - 索引如何保存。
4. 读 `Associate.add_node()`：
   - event 如何变成 TextNode。
   - metadata 如何保存。
   - memory 列表如何维护。
5. 读 `AssociateRetriever._retrieve()`：
   - 原始向量检索。
   - recency score。
   - relevance score。
   - importance score。
   - final score。
   - 更新 access 时间。
6. 读 `retrieve_focus()`：
   - 多个 focus 如何合并结果。
7. 回到 `Agent._add_concept()`：
   - poignancy 如何计算。
   - event / chat / thought 的写入差异。

### 需要逐行精读的函数

- `Concept.__init__()`
- `Associate.add_node()`
- `AssociateRetriever._retrieve()`
- `Associate.retrieve_focus()`
- `LlamaIndex.add_node()`
- `LlamaIndex.retrieve()`
- `Agent._add_concept()`

### 图表建议

- Event -> Concept -> TextNode -> Vector Index 图。
- 三类记忆图：
  - event / chat / thought
- 三因素检索图：
  - recency + relevance + importance -> final score
- 记忆生命周期图：
  - create -> access -> expire -> cleanup

### 易错点

1. `Associate.memory` 保存的是 node id 列表，不是完整文本。
2. 真正文本存在 LlamaIndex docstore 中。
3. `retrieve_chats(name)` 会把名字拼进检索文本。
4. `access` 时间会在检索后更新。
5. `poignancy` 不是情绪状态，而是记忆重要性。

### 本章结束读者应掌握

- 记忆如何写入。
- 记忆如何检索。
- 论文三因素检索如何在代码中实现。
- 为什么记忆系统是后续社交、计划和反思的基础。

---

## 第 16 章：日程：从一天计划到具体动作

### 对应论文概念

- Planning
- Recursive Decomposition
- Replanning
- Goal Grounding

### 章节目标

讲清角色如何从人设中的生活习惯，生成当天计划，再拆成具体行动并落到地点和对象。

### 阅读入口

1. `generative_agents/modules/memory/schedule.py`
2. `generative_agents/modules/agent.py`
3. `generative_agents/modules/memory/action.py`
4. `generative_agents/data/prompts/wake_up.txt`
5. `generative_agents/data/prompts/schedule_init.txt`
6. `generative_agents/data/prompts/schedule_daily.txt`
7. `generative_agents/data/prompts/schedule_decompose.txt`
8. `generative_agents/data/prompts/schedule_revise.txt`
9. `generative_agents/data/prompts/determine_sector.txt`
10. `generative_agents/data/prompts/determine_arena.txt`
11. `generative_agents/data/prompts/determine_object.txt`
12. `generative_agents/data/prompts/describe_object.txt`

### 核心源码锚点

`schedule.py`

- `Schedule`
- `Schedule.add_plan()`
- `Schedule.current_plan()`
- `Schedule.plan_stamps()`
- `Schedule.decompose()`
- `Schedule.scheduled()`
- `Schedule.to_dict()`

`action.py`

- `Action`
- `Action.finished()`
- `Action.to_dict()`
- `Action.from_dict()`

`agent.py`

- `Agent.make_schedule()`
- `Agent.revise_schedule()`
- `Agent.make_plan()`
- `Agent._determine_action()`
- `Agent.make_event()`
- `Agent.find_path()`

`scratch.py`

- `prompt_wake_up()`
- `prompt_schedule_init()`
- `prompt_schedule_daily()`
- `prompt_schedule_decompose()`
- `prompt_schedule_revise()`
- `prompt_determine_sector()`
- `prompt_determine_arena()`
- `prompt_determine_object()`
- `prompt_describe_object()`

### 精读顺序

1. 读 `Schedule`：
   - daily schedule 的结构。
   - start / duration。
   - decompose。
2. 读 `Schedule.current_plan()`：
   - 当前时间如何匹配计划。
3. 读 `Schedule.decompose()`：
   - 哪些计划需要拆解。
   - 睡眠计划如何处理。
4. 读 `Agent.make_schedule()`：
   - 首次生成当天计划。
   - 从旧记忆更新 currently。
   - wake up。
   - schedule init。
   - schedule daily。
   - 写入 thought。
   - 分解当前计划。
5. 读 `Agent._determine_action()`：
   - 当前 de_plan 如何转成行动。
   - 如何找地点。
   - 找不到地点时如何调用 LLM 选 sector / arena / object。
   - 如何生成角色事件和对象事件。
6. 读 `Agent.find_path()`：
   - 根据 action address 寻找目标 tile。
   - 如何处理 persona / waiting / 当前地点。
7. 读 `Agent.revise_schedule()`：
   - 对话和等待如何打断计划。

### 需要逐行精读的函数

- `Schedule.current_plan()`
- `Schedule.decompose()`
- `Agent.make_schedule()`
- `Agent._determine_action()`
- `Agent.find_path()`
- `Agent.revise_schedule()`

### 图表建议

- 三层计划图：
  - `daily_plan` -> daily schedule -> decomposed action
- 计划到地点图：
  - describe -> Spatial.find_address -> determine sector/arena/object -> address
- 行动生命周期图：
  - Action.start -> Action.end -> Action.finished()
- 计划修订图：
  - 原子计划 -> chat/wait -> revised schedule

### 易错点

1. `daily_plan` 是人设，不是运行时日程。
2. `schedule_daily` 生成 24 小时表。
3. `schedule_decompose` 只分解当前大计划。
4. `Action` 是当前正在执行的行为，不是完整日程。
5. `find_path()` 返回路径，不直接移动角色；移动在下一轮状态同步时体现。

### 本章结束读者应掌握

- 计划如何生成。
- 计划如何拆解。
- 计划如何落到地图对象。
- 行动如何被打断和修订。

---

## 第 17 章：社交：聊天、等待、关系传播

### 对应论文概念

- Reaction
- Dialogue
- Social Propagation
- Relationship Memory

### 章节目标

讲清智能体如何从感知到他人，发展出聊天、等待、信息传播和计划改变。

### 阅读入口

1. `generative_agents/modules/agent.py`
2. `generative_agents/modules/prompt/scratch.py`
3. `generative_agents/data/prompts/decide_chat.txt`
4. `generative_agents/data/prompts/decide_chat_terminate.txt`
5. `generative_agents/data/prompts/generate_chat.txt`
6. `generative_agents/data/prompts/generate_chat_check_repeat.txt`
7. `generative_agents/data/prompts/summarize_relation.txt`
8. `generative_agents/data/prompts/summarize_chats.txt`
9. `generative_agents/data/prompts/decide_wait.txt`
10. `generative_agents/data/prompts/decide_wait_example.txt`

### 核心源码锚点

`agent.py`

- `Agent.make_plan()`
- `Agent._reaction()`
- `Agent._skip_react()`
- `Agent._chat_with()`
- `Agent._wait_other()`
- `Agent.schedule_chat()`
- `Agent.revise_schedule()`

`scratch.py`

- `prompt_decide_chat()`
- `prompt_decide_chat_terminate()`
- `prompt_generate_chat()`
- `prompt_generate_chat_check_repeat()`
- `prompt_summarize_relation()`
- `prompt_summarize_chats()`
- `prompt_decide_wait()`

### 精读顺序

1. 从 `Agent.make_plan()` 进入：
   - 为什么先 reaction，再 determine action。
2. 读 `_reaction()`：
   - 当前 concepts 如何选 focus。
   - 为什么优先选择其他 Agent。
   - 如何获取与对方相关的 relation。
3. 读 `_skip_react()`：
   - 睡觉、深夜、待开始行动为什么跳过互动。
4. 读 `_chat_with()`：
   - 初始化状态检查。
   - 对方正在移动时不聊。
   - 双方正在对话时不聊。
   - 最近 60 分钟聊过则不聊。
   - `decide_chat` 判断。
   - 双方关系摘要。
   - 多轮对话生成。
   - 复读检查。
   - 终止判断。
   - 写 conversation。
   - 总结对话。
   - 双方 schedule_chat。
5. 读 `schedule_chat()`：
   - 对话如何变成 action。
   - 对话如何进入 `self.chats` 等待反思。
6. 读 `_wait_other()`：
   - 什么时候等待。
   - 等待如何改写日程。

### 需要逐行精读的函数

- `Agent._reaction()`
- `Agent._chat_with()`
- `Agent._wait_other()`
- `Agent.schedule_chat()`
- `Scratch.prompt_generate_chat()`
- `Scratch.prompt_decide_chat()`

### 图表建议

- 社交反应决策树：
  - concepts -> focus -> chat / wait / no reaction
- 对话生成流程图：
  - relation -> memory -> conversation -> utterance -> termination
- 信息传播图：
  - Isabella -> agent A -> agent B -> plan change
- 对话到记忆图：
  - chats -> summary -> action -> reflection -> thought

### 易错点

1. 聊天不是每次遇见都会发生。
2. `_chat_with()` 使用发起者和响应者双方的 completion。
3. `self.chats` 暂存的是反思材料，不等于 `conversation.json`。
4. 对话持续时间按文本长度估算，属于工程近似。
5. `decide_wait()` 是对象或地点冲突处理，不是普通社交等待。

### 本章结束读者应掌握

- 什么时候会聊天。
- 对话如何生成。
- 对话如何改变日程。
- 信息如何通过对话传播。

---

## 第 18 章：反思：重要事件如何变成长期认知

### 对应论文概念

- Reflection
- Reflection Tree
- High-level Insight
- Memory Consolidation

### 章节目标

讲清反思机制如何把碎片事件和对话总结成长期想法，并影响后续计划和社交。

### 阅读入口

1. `generative_agents/modules/agent.py`
2. `generative_agents/modules/memory/associate.py`
3. `generative_agents/modules/prompt/scratch.py`
4. `generative_agents/data/prompts/reflect_focus.txt`
5. `generative_agents/data/prompts/reflect_insights.txt`
6. `generative_agents/data/prompts/reflect_chat_planing.txt`
7. `generative_agents/data/prompts/reflect_chat_memory.txt`

### 核心源码锚点

`agent.py`

- `Agent.reflect()`
- `Agent._add_concept()`

`associate.py`

- `Associate.retrieve_events()`
- `Associate.retrieve_thoughts()`
- `Associate.retrieve_focus()`
- `Associate.retrieve_chats()`

`scratch.py`

- `prompt_reflect_focus()`
- `prompt_reflect_insights()`
- `prompt_reflect_chat_planing()`
- `prompt_reflect_chat_memory()`

### 精读顺序

1. 回顾 `percept()` 中 `status["poignancy"]` 的累加。
2. 读 `reflect()` 的 early return：
   - 重要性不够不反思。
   - 没有 nodes 不反思。
3. 读事件反思：
   - 取 events + thoughts。
   - 按 access 排序。
   - 限制 max_importance。
   - 生成 focus 问题。
   - 针对问题检索记忆。
   - 生成 insights。
   - 将 insight 写入 thought。
4. 读对话反思：
   - 从 `self.chats` 中找对话对象。
   - 检索最近 chat node。
   - 生成计划影响。
   - 生成值得记忆的内容。
   - 写入 thought。
5. 读反思收尾：
   - poignancy 清零。
   - chats 清空。

### 需要逐行精读的函数

- `Agent.reflect()`
- `Scratch.prompt_reflect_focus()`
- `Scratch.prompt_reflect_insights()`
- `Scratch.prompt_reflect_chat_planing()`
- `Scratch.prompt_reflect_chat_memory()`

### 图表建议

- 反思触发图：
  - event/chat poignancy -> threshold -> reflect
- reflection pipeline：
  - nodes -> focus -> retrieved memories -> insights -> thought nodes
- 对话反思图：
  - chat logs -> plan thought + memory thought

### 易错点

1. 反思不会每一步发生。
2. 反思写入的是 thought 记忆。
3. 对话反思依赖 `self.chats`，不是直接读取 `conversation.json`。
4. 当前项目没有显式可视化 reflection tree。

### 本章结束读者应掌握

- 为什么反思是高层认知来源。
- 反思如何被触发。
- 反思如何写回长期记忆。
- 后续如何设计反思消融实验。

---

## 第 19 章：模型适配：Ollama、MiniMax、OpenAI、结构化输出

### 对应论文概念

- LLM as Reasoning Engine
- Prompt-based Cognition
- Structured Language Output

### 章节目标

讲清 GenerativeAgentsCN 如何让不同大模型稳定驱动智能体。

### 阅读入口

1. `generative_agents/modules/prompt/scratch.py`
2. `generative_agents/modules/model/llm_model.py`
3. `generative_agents/modules/storage/index.py`
4. `generative_agents/data/config.json`
5. `generative_agents/data/prompts/*.txt`
6. `docs/ollama.md`

### 核心源码锚点

`scratch.py`

- `Result`
- `Scratch.build_prompt()`
- `Scratch._base_desc()`
- 所有 `prompt_xxx()` 方法。

`llm_model.py`

- `LLMModel`
- `LLMModel.completion()`
- `OpenAILLMModel`
- `OllamaLLMModel`
- `OllamaLLMModel.ollama_chat()`
- `MiniMaxLLMModel`
- `MiniMaxLLMModel.minimax_chat()`
- `parse_structured_output()`
- `create_llm_model()`

`storage/index.py`

- `OllamaHttpEmbedding`
- `LlamaIndex.__init__()`

### 精读顺序

1. 读 `Scratch.build_prompt()`：
   - prompt 模板如何从文件加载。
   - `Template.substitute()` 如何填充变量。
2. 读任意一个简单 prompt 方法：
   - `prompt_wake_up()`。
   - 观察 prompt、Pydantic model、callback、failsafe。
3. 读一个复杂 prompt 方法：
   - `prompt_schedule_decompose()` 或 `prompt_generate_chat()`。
4. 读 `LLMModel.completion()`：
   - retry。
   - callback。
   - failsafe。
   - summary 统计。
5. 读 `OllamaLLMModel._completion()`：
   - JSON schema。
   - `/api/chat`。
   - `<think>` 过滤。
   - 结构化解析。
6. 读 `MiniMaxLLMModel._completion()`：
   - schema 拼进 prompt。
   - `json_object` 模式。
7. 读 `parse_structured_output()`：
   - JSON 解析。
   - JSON 片段提取。
   - Pydantic validate。
   - 返回原始文本兜底。
8. 读 `LlamaIndex.__init__()`：
   - embedding provider。
   - Ollama / OpenAI / HuggingFace。

### 需要逐行精读的函数

- `LLMModel.completion()`
- `OllamaLLMModel._completion()`
- `MiniMaxLLMModel._completion()`
- `parse_structured_output()`
- `Scratch.prompt_schedule_decompose()`
- `Scratch.prompt_generate_chat()`

### 图表建议

- Prompt 调用生命周期：
  - prompt_xxx -> Result -> LLMModel.completion -> callback -> typed result
- 结构化输出兜底链：
  - valid JSON -> JSON fragment -> wrapped res -> raw text
- Provider 对比表：
  - OpenAI / Ollama / MiniMax
- Embedding provider 对比表：
  - Ollama / OpenAI / HuggingFace

### 易错点

1. `return_type` 是 Pydantic model，不是普通 Python 类型。
2. callback 在模型返回后执行，用于归一化。
3. failsafe 是仿真继续运行的关键。
4. Ollama 的 `base_url` 会被处理，去掉末尾 `/v1`。
5. MiniMax 不完全支持同样的 JSON schema 模式，所以使用不同策略。

### 本章结束读者应掌握

- prompt 如何组织。
- 结构化输出如何保障稳定性。
- 本地中文模型为什么需要额外处理。
- LLM 调用失败时系统如何降级。

---

## 第 20 章：回放系统：checkpoint、`movement.json`、`simulation.md`、Phaser 前端

### 对应论文概念

- End-to-end Observation
- Behavior Trace
- Evaluation Material
- Replayable Simulation

### 章节目标

讲清仿真结果如何从 checkpoint 变成可阅读的 Markdown 和可视化回放。

### 阅读入口

1. `generative_agents/start.py`
2. `generative_agents/compress.py`
3. `generative_agents/replay.py`
4. `generative_agents/live.py`
5. `generative_agents/frontend/templates/index.html`
6. `generative_agents/frontend/templates/main_script.html`
7. `generative_agents/frontend/templates/live_wait.html`
8. `generative_agents/results/compressed/example/simulation.md`
9. `generative_agents/results/compressed/example/movement.json`

### 核心源码锚点

`start.py`

- `SimulateServer.simulate()`
- checkpoint 写入逻辑。
- conversation 写入逻辑。

`compress.py`

- `insert_frame0()`
- `generate_movement()`
- `generate_report()`
- `extract_description()`
- `extract_action()`

`replay.py`

- `index()`

`live.py`

- `build_live_payload()`
- `index()`
- `live_data()`

`main_script.html`

- `refresh_live_data()`
- `preload()`
- `create()`
- `update()`
- play / pause / conversation 控制。

### 精读顺序

1. 从 checkpoint 文件结构开始：
   - `simulate-*.json`
   - `conversation.json`
2. 读 `compress.generate_report()`：
   - 如何读取角色基础人设。
   - 如何提取活动变化。
   - 如何插入对话记录。
   - 如何生成 `simulation.md`。
3. 读 `compress.generate_movement()`：
   - 如何读取 checkpoint。
   - 如何插入第 0 帧。
   - 如何加载 Maze 计算路径。
   - 如何把每个 step 展开成多个 frame。
   - 如何加入对话文本。
   - 如何生成 `movement.json`。
4. 读 `replay.index()`：
   - 如何读取压缩数据。
   - `step / speed / zoom` 如何生效。
   - 如何重新设置起始位置。
5. 读 `live.py`：
   - 如何实时读取 checkpoint。
   - 如何构建 live payload。
6. 读 `main_script.html`：
   - Phaser 如何加载地图和角色。
   - 角色如何移动。
   - 对话如何显示。
   - 实时数据如何刷新。

### 需要逐行精读的函数

- `generate_report()`
- `generate_movement()`
- `replay.index()`
- `live.build_live_payload()`
- `main_script.html` 中 `preload()`、`create()`、`update()` 的关键段落。

### 只需模块级说明的内容

- Bootstrap / jQuery 细节。
- Phaser 所有渲染细节。
- 每个 tilemap 图层的视觉含义。

### 图表建议

- 数据产物流：
  - checkpoint -> compress -> movement.json / simulation.md -> replay
- 回放帧展开图：
  - one step -> 60 frames
- 前后端职责图：
  - Flask 传 JSON 参数。
  - Phaser 加载地图、角色、运动。

### 易错点

1. `movement.json` 是回放数据，不是原始仿真状态。
2. `simulation.md` 更适合做教学和实验分析。
3. `replay.py` 显示的是压缩后的数据。
4. `live.py` 会反复从 checkpoint 生成 live payload，适合观察正在运行的仿真。
5. Phaser 前端不参与智能体决策。

### 本章结束读者应掌握

- 仿真数据如何被保存。
- 回放数据如何生成。
- Markdown 报告如何用于实验分析。
- 前端只是展示层，不是智能体逻辑层。

---

## 第三部分总体阅读路线图

建议正文写作时采用下面顺序：

1. 第 11 章先讲世界模型。
   - 让读者知道小镇是什么。
2. 第 12 章讲角色如何进入系统。
   - 让读者知道居民是什么。
3. 第 13 章讲仿真如何一轮轮运行。
   - 让读者知道系统如何动起来。
4. 第 14 章讲感知。
   - 让读者知道角色如何看到世界。
5. 第 15 章讲记忆。
   - 让读者知道角色如何记住世界。
6. 第 16 章讲日程和行动。
   - 让读者知道角色如何计划并移动。
7. 第 17 章讲社交。
   - 让读者知道角色如何互相影响。
8. 第 18 章讲反思。
   - 让读者知道角色如何形成长期认知。
9. 第 19 章讲模型和 prompt。
   - 让读者知道所有 LLM 调用如何稳定运行。
10. 第 20 章讲回放。
    - 让读者知道如何观察和分析仿真结果。

这个顺序有意没有从 `Agent` 开始。原因是：如果先读 `Agent`，读者会同时遇到地图、时间、记忆、日程、prompt、模型、回放等概念，负担过重。先搭世界，再读大脑，更符合教学路径。

---

## 函数级索引

### 必须逐行精读

`generative_agents/modules/agent.py`

- `Agent.__init__()`
- `Agent.think()`
- `Agent.move()`
- `Agent.make_schedule()`
- `Agent.percept()`
- `Agent.make_plan()`
- `Agent.reflect()`
- `Agent.find_path()`
- `Agent._determine_action()`
- `Agent._reaction()`
- `Agent._chat_with()`
- `Agent._wait_other()`
- `Agent.schedule_chat()`
- `Agent._add_concept()`

`generative_agents/modules/memory/associate.py`

- `Concept.__init__()`
- `AssociateRetriever._retrieve()`
- `Associate.__init__()`
- `Associate.add_node()`
- `Associate._retrieve_nodes()`
- `Associate.retrieve_focus()`
- `Associate.get_relation()`

`generative_agents/modules/memory/schedule.py`

- `Schedule.add_plan()`
- `Schedule.current_plan()`
- `Schedule.decompose()`
- `Schedule.scheduled()`

`generative_agents/modules/maze.py`

- `Tile.__init__()`
- `Maze.__init__()`
- `Maze.find_path()`
- `Maze.get_scope()`
- `Maze.get_address_tiles()`

`generative_agents/modules/model/llm_model.py`

- `LLMModel.completion()`
- `OllamaLLMModel._completion()`
- `MiniMaxLLMModel._completion()`
- `parse_structured_output()`
- `create_llm_model()`

`generative_agents/modules/prompt/scratch.py`

- `Scratch.build_prompt()`
- `Scratch._base_desc()`
- `prompt_schedule_daily()`
- `prompt_schedule_decompose()`
- `prompt_schedule_revise()`
- `prompt_decide_chat()`
- `prompt_generate_chat()`
- `prompt_reflect_focus()`
- `prompt_reflect_insights()`
- `prompt_retrieve_currently()`

### 函数级讲解即可

`generative_agents/start.py`

- `SimulateServer.__init__()`
- `SimulateServer.simulate()`
- `get_config_from_log()`
- `get_config()`

`generative_agents/modules/game.py`

- `Game.__init__()`
- `Game.agent_think()`
- `Game.reset_game()`
- `create_game()`

`generative_agents/modules/storage/index.py`

- `OllamaHttpEmbedding._embed()`
- `LlamaIndex.__init__()`
- `LlamaIndex.add_node()`
- `LlamaIndex.cleanup()`
- `LlamaIndex.retrieve()`
- `LlamaIndex.save()`

`generative_agents/compress.py`

- `insert_frame0()`
- `generate_movement()`
- `generate_report()`

`generative_agents/replay.py`

- `index()`

`generative_agents/live.py`

- `build_live_payload()`
- `index()`
- `live_data()`

### 模块级说明即可

- `generative_agents/frontend/static/assets/village/tilemap/*.png`
- `generative_agents/frontend/static/assets/village/tilemap/tilemap.json`
- `generative_agents/frontend/static/assets/village/agents/*/texture.png`
- `generative_agents/frontend/static/assets/village/agents/*/portrait.png`
- `generative_agents/frontend/static/css/style.css`
- `generative_agents/frontend/templates/base.html`

---

## Prompt 精读索引

### 日程类 Prompt

- `wake_up.txt`
- `schedule_init.txt`
- `schedule_daily.txt`
- `schedule_decompose.txt`
- `schedule_revise.txt`

对应章节：

- 第 16 章。

精读重点：

- 返回结构。
- 时间粒度。
- 与 `Schedule` 数据结构的对应关系。
- failsafe 如何保证系统继续运行。

### 地点和对象类 Prompt

- `determine_sector.txt`
- `determine_arena.txt`
- `determine_object.txt`
- `describe_object.txt`
- `describe_event.txt`

对应章节：

- 第 11 章。
- 第 16 章。

精读重点：

- 自然语言计划如何落到地址树。
- 模型输出如何被校验。

### 社交类 Prompt

- `decide_chat.txt`
- `decide_chat_terminate.txt`
- `generate_chat.txt`
- `generate_chat_check_repeat.txt`
- `summarize_relation.txt`
- `summarize_chats.txt`
- `decide_wait.txt`
- `decide_wait_example.txt`

对应章节：

- 第 17 章。

精读重点：

- 何时聊天。
- 如何避免复读。
- 如何判断话题结束。
- 关系摘要如何进入对话。

### 记忆重要性类 Prompt

- `poignancy_event.txt`
- `poignancy_chat.txt`

对应章节：

- 第 15 章。
- 第 18 章。

精读重点：

- importance score 如何生成。
- 为什么影响 retrieval 和 reflection。

### 反思类 Prompt

- `reflect_focus.txt`
- `reflect_insights.txt`
- `reflect_chat_planing.txt`
- `reflect_chat_memory.txt`

对应章节：

- 第 18 章。

精读重点：

- 从记忆生成问题。
- 从问题生成 insight。
- 对话如何变成长期认知。

### 跨日状态更新类 Prompt

- `retrieve_plan.txt`
- `retrieve_thought.txt`
- `retrieve_currently.txt`

对应章节：

- 第 16 章。
- 第 18 章。

精读重点：

- 前一天记忆如何影响新一天。
- `currently` 如何更新。

---

## 示例数据索引

### 必讲示例

- `generative_agents/frontend/static/assets/village/agents/伊莎贝拉/agent.json`
  - 情人节派对初始设定。
  - 咖啡馆老板身份。
  - living area 与 spatial tree。
- `generative_agents/results/compressed/example/simulation.md`
  - 角色人设。
  - 活动时间线。
  - 对话记录。
- `generative_agents/results/compressed/example/movement.json`
  - 回放帧数据。
  - persona 初始位置。
  - all_movement。
- `generative_agents/data/config.json`
  - 模型配置。
  - 感知配置。
  - 记忆配置。
  - 日程配置。

### 可选示例

- 其他角色 `agent.json`：
  - 山姆：镇长竞选。
  - 拉托亚：地方选举话题。
  - 约翰：地方市长选举好奇心。
  - 汤姆：与山姆关系紧张。

这些角色适合第四部分复现实验使用。

---

## 第三部分写作禁区

1. 不要把源码讲成 API 文档。
   - 每个函数都要放回论文概念和仿真链路中。
2. 不要只讲 Python，忽略 prompt。
   - Prompt 是智能体行为的一半。
3. 不要只讲成功路径。
   - LLM 失败、JSON 解析失败、地址找不到、路径找不到都要讲。
4. 不要把回放前端写成智能体逻辑。
   - Phaser 只展示，不决策。
5. 不要过早引入前沿升级。
   - 第三部分只讲当前项目，前沿演进放第五部分。

## 第三部分结束时读者应掌握

读完第三部分，读者应该能：

1. 从命令行启动追踪到每个 Agent 的一次思考。
2. 解释角色如何感知、记忆、计划、行动、聊天和反思。
3. 找到任意一个论文概念在当前项目中的代码位置。
4. 看懂 checkpoint、`simulation.md` 和 `movement.json`。
5. 修改一个角色或 prompt，并预测它会影响哪个模块。
6. 为第四部分复现实验做好源码基础。

## 后续衔接

本文件完成后，下一个应落盘的文档是：

- `04_experiment_design.md`

该文件应设计第四部分实验：

1. 复现论文中的情人节派对传播。
2. 复现镇长竞选信息扩散。
3. 设计自己的小镇事件。
4. 增加新角色、新地点、新关系。
5. 用中文本地模型重跑论文思想。
6. 设计“可信行为”评价指标。
7. 记录风险、伦理与边界。
