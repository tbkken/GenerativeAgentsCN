# 论文概念到 GenerativeAgentsCN 源码模块映射表 v0.1

## 文档目标

本文件承接 `01_original_paper_outline.md`，用于建立“论文概念 -> GenerativeAgentsCN 工程实现 -> 后续写作章节”的证据链。

写作时必须避免只做宽泛对应。例如不能只写“Memory Stream 对应 Associate”，而要继续写清楚：

- 入口文件在哪里。
- 核心类和函数是什么。
- 用到了哪些 prompt。
- 数据从哪里来，存到哪里去。
- 当前项目与论文原始设计有哪些差异。
- 后续源码精读和实验复现应该怎么展开。

## 使用方式

后续写第三部分“源码深读”时，每一章都应回到本文件找锚点：

- 讲世界模型时，看 Smallville、Sandbox Grounding、Spatial Memory。
- 讲智能体主循环时，看 Agent Runtime Loop。
- 讲记忆时，看 Memory Stream、Retrieval、Importance、Reflection。
- 讲社交时，看 Reaction、Dialogue、Conversation Propagation。
- 讲回放和评价时，看 Replay、Report、Evaluation Data。

---

## 一、总览映射

| 论文概念 | GenerativeAgentsCN 实现 | 核心讲解章节 |
|---|---|---|
| Smallville / Sandbox Environment | `frontend/static/assets/village`、`Maze`、`Tile`、Phaser 前端 | 第 9-11 章、第 20 章 |
| Agent Persona | `agent.json`、`Scratch`、`base_desc`、`currently` | 第 12 章 |
| Time and Simulation Step | `Timer`、`SimulateServer.simulate()`、`stride` | 第 13 章 |
| Observation | `Maze.get_scope()`、`Agent.percept()`、`Event` | 第 14 章 |
| Memory Stream | `Associate`、`Concept`、`Event`、LlamaIndex | 第 15 章 |
| Importance | `poignancy_event`、`poignancy_chat`、`Agent._add_concept()` | 第 15 章 |
| Retrieval | `AssociateRetriever`、`retrieve_focus()`、`LlamaIndex.retrieve()` | 第 15 章 |
| Reflection | `Agent.reflect()`、`reflect_focus`、`reflect_insights` | 第 18 章 |
| Planning | `Schedule`、`Agent.make_schedule()`、schedule prompts | 第 16 章 |
| Reacting | `Agent._reaction()`、`_wait_other()`、`_chat_with()` | 第 17 章 |
| Dialogue | `generate_chat`、`summarize_relation`、`summarize_chats` | 第 17 章 |
| Sandbox Grounding | `Spatial`、`Maze`、`determine_sector/arena/object` | 第 11、16 章 |
| Structured LLM Calls | `Scratch`、Pydantic response model、`LLMModel` | 第 19 章 |
| Checkpoint / Replay | `start.py`、`compress.py`、`replay.py`、`live.py` | 第 20 章 |
| Evaluation Material | `simulation.md`、`movement.json`、`conversation.json` | 第 26 章 |

---

## 二、运行链路总图

### 论文中的抽象循环

论文中的生成式智能体大致遵循：

1. 观察世界。
2. 写入记忆。
3. 检索相关记忆。
4. 反思形成高层认知。
5. 生成或调整计划。
6. 对环境和他人作出反应。
7. 行动改变世界。
8. 新事件再次进入记忆。

### GenerativeAgentsCN 中的工程链路

1. `start.py` 解析命令行参数，创建 simulation config。
2. `create_game()` 设置全局时间并创建 `Game`。
3. `Game.__init__()` 加载 `Maze` 和多个 `Agent`。
4. `SimulateServer.simulate()` 按 step 推进。
5. 每个 step 对每个角色调用 `Game.agent_think()`。
6. `Game.agent_think()` 调用 `Agent.think()`。
7. `Agent.think()` 依次处理：
   - 移动状态同步。
   - 日程生成或读取。
   - 睡眠判断。
   - 感知。
   - 计划或反应。
   - 反思。
   - 寻路。
8. `start.py` 写入 checkpoint 和 conversation。
9. `compress.py` 从 checkpoint 生成 `movement.json` 和 `simulation.md`。
10. `replay.py` / `live.py` 通过 Flask + Phaser 展示结果。

### 核心源码锚点

- `generative_agents/start.py`
  - `SimulateServer`
  - `SimulateServer.simulate()`
  - `get_config()`
  - `get_config_from_log()`
- `generative_agents/modules/game.py`
  - `Game`
  - `Game.agent_think()`
  - `create_game()`
- `generative_agents/modules/agent.py`
  - `Agent`
  - `Agent.think()`
  - `Agent.make_schedule()`
  - `Agent.percept()`
  - `Agent.make_plan()`
  - `Agent.reflect()`

### 写作重点

这里要写成“项目生命线”：从命令行启动，到每个智能体思考，再到存档与回放。读者必须先理解这个主链路，后面细读任何模块才不会迷路。

---

## 三、详细概念卡片

## 1. Smallville / Sandbox Environment

### 论文含义

Smallville 是论文中的沙盒环境。它让智能体不只是生成文字，而是在有地点、房间、对象、时间和其他人的世界中生活。它是观察可信行为和社会涌现的实验装置。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/frontend/static/assets/village/maze.json`
- `generative_agents/frontend/static/assets/village/tilemap/tilemap.json`
- `generative_agents/frontend/static/assets/village/tilemap/*.png`
- `generative_agents/modules/maze.py`
- `generative_agents/frontend/templates/main_script.html`

核心类与函数：

- `Tile`
  - 保存坐标、地址、碰撞信息、事件。
  - 初始化对象事件。
- `Maze`
  - 加载 `maze.json`。
  - 构建 `Tile` 网格。
  - 建立 address 到 tile 集合的索引。
  - 提供寻路、视野、对象更新能力。
- `Maze.find_path()`
- `Maze.get_scope()`
- `Maze.get_address_tiles()`
- `Maze.update_obj()`

### 数据锚点

- `maze.json`：供 Python 后端理解空间和对象。
- `tilemap.json`：供 Phaser 前端渲染地图。
- `tilemap/*.png`：地图图块资源。

### 写作重点

这一节要强调两套地图数据的区别：

- Python 后端使用 `maze.json` 做空间推理。
- Phaser 前端使用 `tilemap.json` 做视觉回放。

两者服务目标不同。前者决定“智能体能不能去哪里、看到什么、对象在哪里”；后者决定“读者在浏览器里看到什么”。

### 与论文的差异

论文强调用户可以通过自然语言干预智能体或环境。当前 GenerativeAgentsCN 主要支持仿真、断点恢复、压缩和回放，没有完整实现论文中的自然语言实时干预界面。

### 后续章节

- 第 11 章：世界模型。
- 第 20 章：回放系统。
- 第 23 章：设计自己的小镇事件。
- 第 24 章：增加新地点。

---

## 2. Agent Persona

### 论文含义

每个智能体都有自然语言形式的人设，包括身份、性格、生活习惯、关系、当前状态和初始记忆。Persona 决定角色行为的长期风格。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/frontend/static/assets/village/agents/*/agent.json`
- `generative_agents/modules/agent.py`
- `generative_agents/modules/prompt/scratch.py`
- `generative_agents/data/prompts/base_desc.txt`

核心字段：

- `name`
- `coord`
- `currently`
- `scratch.age`
- `scratch.innate`
- `scratch.learned`
- `scratch.lifestyle`
- `scratch.daily_plan`
- `spatial.address`
- `spatial.tree`

核心类与函数：

- `Agent.__init__()`
  - 加载角色配置。
  - 初始化空间记忆、日程、关联记忆、scratch prompt 状态。
- `Scratch._base_desc()`
  - 将角色基本信息、日期、当前状态拼成统一基础描述。
- `Scratch.build_prompt()`
  - 从模板文件填充 prompt。

### Prompt 锚点

- `base_desc.txt`
- `wake_up.txt`
- `schedule_init.txt`
- `schedule_daily.txt`
- `generate_chat.txt`

### 写作重点

角色不是通过硬编码规则定义，而是通过自然语言设定和 prompt 进入 LLM。源码讲解时要解释：`agent.json` 是“角色种子”，`Scratch._base_desc()` 是“角色进入每次 LLM 调用的公共上下文”。

### 与论文的差异

GenerativeAgentsCN 做了深度中文化。角色名、地点名、动作描述、prompt 都尽量使用中文，目的是降低中英混杂导致模型切换语境的风险。

### 后续章节

- 第 12 章：智能体初始化。
- 第 24 章：增加新角色、新关系。

---

## 3. Time and Simulation Step

### 论文含义

生成式智能体需要在连续时间中生活。时间不是装饰，它影响日程、睡眠、记忆近期性、行动持续时间和对话冷却。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/modules/utils/timer.py`
- `generative_agents/start.py`
- `generative_agents/modules/memory/schedule.py`
- `generative_agents/modules/memory/action.py`

核心类与函数：

- `Timer`
  - 保存当前仿真时间。
  - `forward()` 推进时间。
  - `get_date()` 返回当前时间。
  - `daily_duration()` 返回当天分钟数。
  - `daily_time()` 将分钟数转回具体时间。
- `SimulateServer.simulate()`
  - 按 step 循环。
  - 每步结束后根据 `stride` 推进时间。
- `Action`
  - 保存行动开始时间、持续时间、结束时间。
- `Schedule.current_plan()`
  - 根据当前时间选择当前计划。

### 数据锚点

- 命令行参数 `--start`
- 命令行参数 `--step`
- 命令行参数 `--stride`
- checkpoint 中的 `time`
- checkpoint 中的 `step`

### 写作重点

要把 `step` 和小镇时间区分清楚：

- `step` 是仿真循环次数。
- `stride` 决定每次循环对应小镇中的多少分钟。
- `Timer` 是所有日程、行动、记忆访问时间的共同时间基准。

### 与论文的差异

论文以交互式沙盒展示为主。GenerativeAgentsCN 更强调命令行可复现实验，因此时间推进、checkpoint 和 replay 数据生成更显式。

### 后续章节

- 第 13 章：仿真循环。
- 第 16 章：日程系统。
- 第 20 章：回放系统。

---

## 4. Observation / Perception

### 论文含义

智能体每一步会观察周围环境，把观察结果转成自然语言记忆。观察是 memory stream 的入口。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/modules/agent.py`
- `generative_agents/modules/maze.py`
- `generative_agents/modules/memory/event.py`

核心函数：

- `Agent.percept()`
  - 根据当前位置调用 `Maze.get_scope()`。
  - 将可见范围内的对象地址加入 `Spatial`。
  - 收集同一 arena 中的事件。
  - 按距离排序并限制注意力带宽。
  - 将新事件写入关联记忆。
- `Maze.get_scope()`
  - 根据感知配置返回视野范围内的 tiles。
- `Tile.get_events()`
  - 返回当前 tile 上的事件。
- `Event.get_describe()`
  - 将事件转成自然语言描述。

### 配置锚点

- `data/config.json`
  - `percept.mode`
  - `percept.vision_r`
  - `percept.att_bandwidth`

### 写作重点

这一节要解释两个过滤：

1. 空间过滤：只看视野范围内、同一 arena 的事件。
2. 注意力过滤：只处理最近的前 `att_bandwidth` 个事件。

这对应论文中“智能体不是全知”的设定。

### 与论文的差异

当前项目的感知范围以 box 模式实现，比较直接。论文语境中的环境交互更丰富，当前项目主要围绕地图事件、人物事件和对象事件。

### 后续章节

- 第 14 章：感知。
- 第 15 章：记忆写入。

---

## 5. Memory Stream

### 论文含义

Memory stream 是智能体经验的长期记录。它保存观察、行动、对话和反思，是可信行为连续性的基础。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/modules/memory/associate.py`
- `generative_agents/modules/memory/event.py`
- `generative_agents/modules/storage/index.py`
- `generative_agents/modules/agent.py`

核心类与函数：

- `Concept`
  - 对应记忆节点。
  - 包含 node id、node type、事件、重要性、创建时间、过期时间、访问时间。
- `Associate`
  - 管理 event / thought / chat 三类记忆。
  - 通过 LlamaIndex 存储和检索记忆节点。
- `Associate.add_node()`
  - 写入新记忆。
- `Agent._add_concept()`
  - 负责给事件打重要性分数，并调用 `Associate.add_node()`。
- `LlamaIndex.add_node()`
  - 将文本和 metadata 写入向量索引。

### 记忆类型

- `event`
  - 观察到的事件。
  - 行动计划形成的事件。
- `chat`
  - 对话摘要。
- `thought`
  - 计划、反思、对话后总结出的想法。

### 数据锚点

- `results/checkpoints/<name>/storage/<agent>/associate`
- `index_config.json`
- checkpoint 中的 `associate`

### 写作重点

要明确三层对象：

1. `Event` 是世界中发生的事。
2. `Concept` 是进入记忆流后的记忆节点。
3. `Associate` 是管理这些节点的长期记忆系统。

### 与论文的差异

论文中的 memory stream 是理论架构。GenerativeAgentsCN 通过 LlamaIndex 做工程落地，并增加了过期时间、节点清理、三类记忆列表等实现细节。

### 后续章节

- 第 15 章：记忆。
- 第 18 章：反思。

---

## 6. Importance / Poignancy

### 论文含义

Importance 用来判断一条记忆有多重要。重要事件更容易被检索，也会触发反思。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/modules/agent.py`
- `generative_agents/modules/prompt/scratch.py`
- `generative_agents/data/prompts/poignancy_event.txt`
- `generative_agents/data/prompts/poignancy_chat.txt`

核心函数：

- `Agent._add_concept()`
  - 对 event / chat 调用不同的 poignancy prompt。
  - idle / 空闲事件直接给低分。
- `Scratch.prompt_poignancy_event()`
  - 让模型给事件打 1-10 分。
- `Scratch.prompt_poignancy_chat()`
  - 让模型给对话打 1-10 分。
- `Agent.percept()`
  - 将新记忆的 poignancy 累加到 `status["poignancy"]`。
- `Agent.reflect()`
  - 当累计值超过 `poignancy_max` 后触发反思。

### 配置锚点

- `data/config.json`
  - `think.poignancy_max`

### 写作重点

Poignancy 在项目里有两个作用：

1. 作为检索排序的 importance 分数。
2. 作为反思触发器的累计值。

这部分要特别讲清楚，否则读者会把它误解成“情绪值”。

### 与论文的差异

论文用 importance 表达记忆重要性。当前项目沿用 wounderland 风格使用 poignancy 命名，但功能上承担 importance 作用。

### 后续章节

- 第 15 章：记忆检索。
- 第 18 章：反思。

---

## 7. Retrieval

### 论文含义

当智能体需要做决定时，它不会读取全部记忆，而是根据 recency、importance、relevance 检索最相关的记忆。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/modules/memory/associate.py`
- `generative_agents/modules/storage/index.py`

核心类与函数：

- `AssociateRetriever`
  - 包装 LlamaIndex 的向量检索。
  - 对结果按 recency、relevance、importance 重新打分。
- `Associate.retrieve_focus()`
  - 根据一个或多个 focus 问题检索 event 和 thought。
- `Associate.retrieve_events()`
- `Associate.retrieve_thoughts()`
- `Associate.retrieve_chats()`
- `Associate.get_relation()`
- `LlamaIndex.retrieve()`

### 检索维度

- recency
  - 通过 `access` 时间排序并计算衰减。
- relevance
  - 来自 LlamaIndex / embedding 相似度分数。
- importance
  - 来自 node metadata 中的 `poignancy`。

### 配置锚点

- `Associate.__init__()`
  - `retention`
  - `max_importance`
  - `recency_decay`
  - `recency_weight`
  - `relevance_weight`
  - `importance_weight`

### 写作重点

这一章可以直接把论文三因素公式与 `AssociateRetriever._retrieve()` 对照讲。读者要看懂：向量检索只是第一步，最终进入 prompt 的记忆经过了二次排序。

### 与论文的差异

当前项目用 LlamaIndex 和可配置 embedding provider 实现 relevance。论文原始实现与此不同，但思想一致：用自然语言 embedding 做相关性检索。

### 后续章节

- 第 15 章：记忆。
- 第 17 章：社交关系检索。
- 第 18 章：反思检索。

---

## 8. Reflection

### 论文含义

Reflection 将原始观察提升为高层认知。没有反思，智能体只能记住碎片事件，很难形成对自己、他人和关系的稳定理解。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/modules/agent.py`
- `generative_agents/modules/prompt/scratch.py`
- `generative_agents/data/prompts/reflect_focus.txt`
- `generative_agents/data/prompts/reflect_insights.txt`
- `generative_agents/data/prompts/reflect_chat_planing.txt`
- `generative_agents/data/prompts/reflect_chat_memory.txt`

核心函数：

- `Agent.reflect()`
  - 判断 `status["poignancy"]` 是否超过阈值。
  - 取近期 event 和 thought。
  - 生成反思问题。
  - 针对问题检索记忆。
  - 生成 insight。
  - 将 insight 作为 thought 写入记忆。
  - 对最近对话进行计划反思和记忆反思。
- `Scratch.prompt_reflect_focus()`
- `Scratch.prompt_reflect_insights()`
- `Scratch.prompt_reflect_chat_planing()`
- `Scratch.prompt_reflect_chat_memory()`

### 数据锚点

- `Concept.node_type == "thought"`
- checkpoint 中的 `associate.thought`
- `simulation.md` 中可以间接观察反思后的行动变化。

### 写作重点

源码精读时要把反思拆成两个层次：

1. 普通事件触发的反思。
2. 对话结束后对计划和记忆的反思。

第二点是当前项目中很值得讲的工程设计：对话不是说完就结束，而是会被总结成影响后续行为的 thought。

### 与论文的差异

论文强调 reflection tree 和 insight 的递归形成。当前项目实现了事件反思和对话反思，但没有单独可视化 reflection tree。后续可以作为扩展实验。

### 后续章节

- 第 18 章：反思。
- 第 30 章：反思机制消融实验。

---

## 9. Planning

### 论文含义

Planning 让智能体形成长期行为结构，而不是每一步都随机即兴。论文从日计划递归拆到更细的行动。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/modules/agent.py`
- `generative_agents/modules/memory/schedule.py`
- `generative_agents/modules/prompt/scratch.py`
- `generative_agents/data/prompts/wake_up.txt`
- `generative_agents/data/prompts/schedule_init.txt`
- `generative_agents/data/prompts/schedule_daily.txt`
- `generative_agents/data/prompts/schedule_decompose.txt`
- `generative_agents/data/prompts/schedule_revise.txt`
- `generative_agents/data/prompts/retrieve_plan.txt`
- `generative_agents/data/prompts/retrieve_thought.txt`
- `generative_agents/data/prompts/retrieve_currently.txt`

核心函数：

- `Agent.make_schedule()`
  - 如果当天没有日程，生成起床时间、初始日程、24 小时日程。
  - 如果存在旧记忆，先检索过去计划和想法，更新 `currently`。
  - 将当天计划写入 thought 记忆。
  - 对当前大计划做子任务拆解。
- `Schedule.add_plan()`
- `Schedule.current_plan()`
- `Schedule.decompose()`
- `Agent.revise_schedule()`

### 写作重点

要讲出三层计划：

1. 人设中的 `daily_plan`。
2. LLM 生成的当天小时级日程。
3. 当前计划拆解出的细粒度行动。

还要讲“计划会被打断”：聊天和等待会通过 `revise_schedule()` 改写当前 decompose schedule。

### 与论文的差异

论文原始系统强调递归规划。当前项目实现了小时计划和子任务拆解，并加入中文 prompt、结构化输出和断点恢复场景下的 `currently` 更新。

### 后续章节

- 第 16 章：日程。
- 第 21 章：复现情人节派对传播。

---

## 10. Action and Object State

### 论文含义

智能体行动必须落到沙盒环境中。行动不仅是角色状态，也会改变对象状态。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/modules/memory/event.py`
- `generative_agents/modules/memory/action.py`
- `generative_agents/modules/agent.py`
- `generative_agents/modules/maze.py`

核心类与函数：

- `Event`
  - 保存 subject、predicate、object、address、describe、emoji。
- `Action`
  - 保存角色事件、对象事件、开始时间、持续时间。
- `Agent.make_event()`
  - 将自然语言动作转换成项目内部事件对象。
- `Agent._determine_action()`
  - 根据当前计划决定地点、对象和行动。
- `Agent.move()`
  - 将角色事件写入当前 tile。
  - 同步对象事件。
- `Maze.update_obj()`
  - 更新同一对象地址下的对象状态。

### Prompt 锚点

- `determine_sector.txt`
- `determine_arena.txt`
- `determine_object.txt`
- `describe_object.txt`
- `describe_event.txt`

### 写作重点

这里要解释“语言行动”和“世界状态”的转换：

- LLM 生成的是自然语言计划。
- `Spatial` 和 prompt 选择目标地址。
- `Event` 和 `Action` 把自然语言落成可存储、可移动、可回放的数据结构。

### 与论文的差异

当前项目为了稳定中文输出，部分事件描述逻辑从原先三元组解析退回为更直接的中文描述清洗。这里要如实讲，不要包装成更复杂的语义解析。

### 后续章节

- 第 16 章：日程到行动。
- 第 20 章：回放展示。

---

## 11. Sandbox Grounding / Spatial Memory

### 论文含义

智能体需要知道世界里有哪些地方和对象，并把抽象计划落到具体地点。否则计划只是文字，无法在小镇中执行。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/modules/memory/spatial.py`
- `generative_agents/modules/maze.py`
- `generative_agents/frontend/static/assets/village/agents/*/agent.json`

核心类与函数：

- `Spatial`
  - 保存角色已知地点树。
  - 保存特定活动到地址的映射。
- `Spatial.add_leaf()`
  - 感知到新对象时加入空间记忆。
- `Spatial.find_address()`
  - 根据活动描述查找已知地址。
- `Spatial.get_leaves()`
  - 获取某个地址下的子地点或对象。
- `Spatial.random_address()`
  - 随机选择一个地址。
- `Agent._determine_action()`
  - 在 `Spatial.find_address()` 不足时调用 LLM 选择 sector、arena、object。

### Prompt 锚点

- `determine_sector.txt`
- `determine_arena.txt`
- `determine_object.txt`

### 写作重点

要讲清楚项目中的地址层级：

1. world
2. sector
3. arena
4. game_object

这四级地址是连接“我要吃饭”与“去某个地点的某个对象附近行动”的关键。

### 与论文的差异

论文中的环境支持更丰富的用户交互和对象语义。当前项目主要用地址树和对象名称做 grounding，适合教学和源码分析，但不是完整物理世界模拟。

### 后续章节

- 第 11 章：地址树。
- 第 16 章：从计划到目标地点。
- 第 24 章：增加新地点。

---

## 12. Reaction

### 论文含义

Reaction 让智能体对环境变化和他人行为做出即时反应，而不是死板执行原计划。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/modules/agent.py`
- `generative_agents/modules/prompt/scratch.py`

核心函数：

- `Agent.make_plan()`
  - 先尝试 `_reaction()`。
  - 如果没有反应且当前行动结束，再决定新行动。
- `Agent._reaction()`
  - 从当前感知到的概念中选择关注对象。
  - 优先关注其他 Agent。
  - 检索与对方相关的事件和想法。
  - 尝试聊天或等待。
- `Agent._skip_react()`
  - 判断睡觉、深夜、行动待开始等情况下是否跳过反应。
- `Agent._wait_other()`
  - 判断是否等待占用目标地点或对象的人。

### Prompt 锚点

- `decide_chat.txt`
- `decide_wait.txt`
- `decide_wait_example.txt`

### 写作重点

Reaction 要写成“计划与现实之间的调解器”。智能体有日程，但不是只按日程走；它会被看到的人、占用的对象、最近记忆和对话机会打断。

### 与论文的差异

当前项目的 reaction 类型主要是聊天和等待。论文中也讨论更广义的反应和重规划。后续扩展可以加入更多反应类型，如避让、拒绝、邀请、协作任务。

### 后续章节

- 第 17 章：社交。
- 第 23 章：设计小镇事件。

---

## 13. Dialogue

### 论文含义

Dialogue 是智能体社会行为的核心媒介。信息通过对话传播，对话又会进入记忆，进而改变后续行动。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/modules/agent.py`
- `generative_agents/modules/prompt/scratch.py`
- `generative_agents/data/prompts/generate_chat.txt`
- `generative_agents/data/prompts/generate_chat_check_repeat.txt`
- `generative_agents/data/prompts/decide_chat.txt`
- `generative_agents/data/prompts/decide_chat_terminate.txt`
- `generative_agents/data/prompts/summarize_relation.txt`
- `generative_agents/data/prompts/summarize_chats.txt`

核心函数：

- `Agent._chat_with()`
  - 判断是否能聊天。
  - 检查最近聊天冷却。
  - 判断是否主动聊天。
  - 生成双方关系摘要。
  - 多轮生成对话。
  - 检查复读和终止。
  - 写入 conversation。
  - 总结对话。
  - 双方调用 `schedule_chat()`。
- `Agent.schedule_chat()`
  - 将对话摘要作为事件写入行动。
  - 修改当前日程。
  - 将对话内容暂存在 `self.chats`，等待反思。
- `Scratch.prompt_generate_chat()`
  - 使用角色描述、记忆、地点、当前时间、上一轮对话生成发言。

### 数据锚点

- `results/checkpoints/<name>/conversation.json`
- `simulation.md` 中的对话记录。
- `movement.json` 中的 conversation 字段。

### 写作重点

要强调对话不是终点，而是传播机制：

1. 对话由记忆和场景触发。
2. 对话内容被总结。
3. 摘要成为双方事件。
4. 对话内容进入反思。
5. 后续计划可能被改变。

### 与论文的差异

当前项目针对中文模型增加了：

- 对话复读检测。
- 对话终止判断。
- 中文对话原则。
- 对 Qwen / DeepSeek 这类模型输出 `<think>` 的过滤。

这些是 GenerativeAgentsCN 很值得写的工程特色。

### 后续章节

- 第 17 章：社交。
- 第 21 章：情人节派对传播。
- 第 22 章：镇长竞选信息扩散。

---

## 14. Structured Prompt and LLM Adapter

### 论文含义

论文中的智能体大量依赖 LLM 调用。工程实现必须让这些调用稳定、可解析、可兜底。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/modules/prompt/scratch.py`
- `generative_agents/modules/model/llm_model.py`
- `generative_agents/data/prompts/*.txt`
- `generative_agents/data/config.json`

核心类与函数：

- `Scratch`
  - 管理 prompt 模板。
  - 每个 `prompt_xxx()` 返回 prompt、callback、failsafe、return_type。
- `LLMModel`
  - 提供统一 completion 重试、统计和失败兜底。
- `OpenAILLMModel`
- `OllamaLLMModel`
- `MiniMaxLLMModel`
- `parse_structured_output()`
- `create_llm_model()`

### 工程机制

- 每个 prompt 使用 Pydantic 定义返回结构。
- Ollama 尽量使用 JSON schema 作为 response format。
- MiniMax 使用 `json_object` 模式并把 schema 拼进 prompt。
- `<think>...</think>` 会在解析前被过滤。
- callback 负责进一步校验和归一化。
- failsafe 保证失败时系统还能继续运行。

### 写作重点

这一章要告诉读者：智能体系统不是“写个 prompt 就完了”。真正的工程难点是：

- 输出格式不稳定。
- 本地模型会输出思考过程。
- 小模型容易返回不合法 JSON。
- 每一步失败都会影响长期仿真。

因此结构化输出、callback、failsafe 是项目可运行性的关键。

### 与论文的差异

论文时期主要依赖 GPT-3.5 / GPT-4 API。GenerativeAgentsCN 面向中文本地模型和 OpenAI-compatible API 做了更多适配。

### 后续章节

- 第 19 章：模型适配。
- 第 25 章：用中文本地模型重跑论文思想。

---

## 15. Checkpoint, Compression and Replay

### 论文含义

论文需要观察和评价智能体行为。工程项目需要把仿真过程保存下来，供回放、分析和教学。

### GenerativeAgentsCN 实现

核心文件：

- `generative_agents/start.py`
- `generative_agents/compress.py`
- `generative_agents/replay.py`
- `generative_agents/live.py`
- `generative_agents/frontend/templates/index.html`
- `generative_agents/frontend/templates/main_script.html`

核心函数：

- `SimulateServer.simulate()`
  - 每步写 checkpoint。
  - 写 `conversation.json`。
- `get_config_from_log()`
  - 支持断点恢复。
- `generate_report()`
  - 生成 `simulation.md`。
- `generate_movement()`
  - 生成前端回放需要的 `movement.json`。
- `replay.index()`
  - 读取压缩后的回放数据并渲染页面。
- `live.build_live_payload()`
  - 实时读取 checkpoint 并刷新前端数据。

### 数据锚点

- `results/checkpoints/<name>/simulate-*.json`
- `results/checkpoints/<name>/conversation.json`
- `results/compressed/<name>/movement.json`
- `results/compressed/<name>/simulation.md`

### 写作重点

要把“运行时数据”和“回放数据”分开讲：

- checkpoint 是仿真恢复和后处理的原始材料。
- compressed result 是给浏览器和读者看的压缩结果。

`simulation.md` 对写书尤其重要，因为它能作为实验案例的文本证据。

### 与论文的差异

论文展示的是 Smallville 系统和实验结果。GenerativeAgentsCN 增加了更直接的 Markdown 时间线输出，适合教学、写作和实验复盘。

### 后续章节

- 第 20 章：回放系统。
- 第 21-22 章：论文实验复现。
- 第 26 章：如何评价智能体是否可信。

---

## 16. Evaluation and Ablation

### 论文含义

论文不只展示小镇故事，还通过 controlled evaluation、ablation study 和 end-to-end evaluation 证明架构组件的作用。

### GenerativeAgentsCN 现状

当前项目没有完整内置论文级评价框架，但具备搭建教学版评价的基础数据：

- checkpoint 记录每步 agent 状态。
- `conversation.json` 保存对话传播。
- `simulation.md` 保存活动和对话时间线。
- `movement.json` 保存移动轨迹和回放数据。
- 命令行可以通过 `--agent-count`、`--agents` 做轻量实验。

### 可实现评价方向

1. 记忆一致性：
   - 角色是否记得之前对话。
   - 是否在后续行动中体现记忆。
2. 计划完成率：
   - 当天计划是否被执行。
   - 被聊天打断后是否合理调整。
3. 社交传播：
   - 某个事件从发起人传播到多少人。
   - 传播路径是什么。
4. 协同行动：
   - 被邀请者是否按时到达。
   - 是否出现多人聚集。
5. 反思作用：
   - 关闭 reflection 后，关系和计划是否变差。

### 写作重点

这一节不能假装当前项目已经完整复现论文评价。要如实写：

- 当前项目提供数据和运行基础。
- 本书第四部分将基于这些数据设计复现实验和消融实验。

### 后续章节

- 第 26 章：如何评价智能体是否可信。
- 第 30 章：反思机制消融实验。

---

## 四、论文概念到章节分配

| 后续章节 | 主要论文概念 | 主要代码锚点 |
|---|---|---|
| 第 9 章 项目谱系 | 原始系统、重构、中文化 | README、README_en、项目目录 |
| 第 10 章 概念映射 | 全部核心概念 | 本文件 |
| 第 11 章 世界模型 | Smallville、Sandbox Grounding | `maze.py`、`maze.json`、tilemap |
| 第 12 章 智能体初始化 | Persona、Memory、Schedule | `Agent.__init__()`、`agent.json` |
| 第 13 章 仿真循环 | Agent Runtime Loop | `start.py`、`game.py`、`Agent.think()` |
| 第 14 章 感知 | Observation | `Agent.percept()`、`Maze.get_scope()` |
| 第 15 章 记忆 | Memory Stream、Retrieval、Importance | `associate.py`、`storage/index.py` |
| 第 16 章 日程 | Planning | `schedule.py`、`make_schedule()` |
| 第 17 章 社交 | Reaction、Dialogue | `_reaction()`、`_chat_with()` |
| 第 18 章 反思 | Reflection | `reflect()`、reflect prompts |
| 第 19 章 模型适配 | LLM Calls、Structured Output | `scratch.py`、`llm_model.py` |
| 第 20 章 回放系统 | Observation for Evaluation | `compress.py`、`replay.py`、`live.py` |
| 第 21-22 章 实验复现 | Information Diffusion | `conversation.json`、`simulation.md` |
| 第 26 章 可信评价 | Evaluation、Ablation | checkpoint、统计脚本、实验设计 |

---

## 五、写作时必须坚持的判断

1. 论文概念优先于源码表象。
   - 例如讲 `Associate` 时，先讲 memory stream 和 retrieval，再讲类和函数。
2. 工程实现必须如实描述。
   - 当前项目没有完整自然语言用户干预，不要写成已经实现。
3. 中文化是本项目的独立贡献。
   - 不只是翻译名称，而是为了降低中文模型在混合语境中的不稳定。
4. Prompt 是源码的一部分。
   - 不能只讲 Python 文件，必须同时讲 `data/prompts/*.txt`。
5. 评价和失败案例必须进入正文。
   - 不能只展示小镇跑起来，要解释它哪里可能不可信。

## 六、后续需要补充的证据

后面进入第三部分前，还需要做三个补充文件：

1. `03_source_reading_index.md`
   - 按文件、类、函数列出源码精读顺序。
2. `04_experiment_design.md`
   - 情人节派对、镇长竞选、关系形成、反思消融的实验设计。
3. `05_frontier_upgrade_notes.md`
   - 2023-2026 前沿演进与项目升级清单。

其中 `05_frontier_upgrade_notes.md` 只追加到全书第五部分，不改动前四部分主结构。
