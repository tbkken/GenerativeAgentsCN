# 2023-2026 前沿演进与 Generative Agents 升级路线 v0.1

## 文档目标

本文件用于规划全书第五部分“前沿演进与项目升级”。

前四部分的主结构不变：

1. 先讲原论文与思想源头。
2. 再讲从论文到 Generative Agents。
3. 再讲当前项目源码。
4. 再讲复现实验与扩展。

第五部分只在读者已经理解经典 Generative Agents 架构之后追加，目的不是堆论文名，而是回答一个更重要的问题：

> 2023 年 Generative Agents 之后，业界和学界在智能体方向有哪些更先进的理念？这些理念如何基于 Generative Agents 做成可讲、可跑、可评价的升级实验？

## 时间边界

本文件按 2026-06-20 前可公开核验的一手资料整理。

这里的“前沿”不追求覆盖所有论文，而是选择对本书最有用的方向：

1. 长期记忆升级。
2. 反思与自我改进升级。
3. 规划、工具使用与技能库升级。
4. 多智能体协作升级。
5. 社会仿真升级。
6. 智能体评价体系升级。
7. 中文、本地和推理模型升级。
8. 工程化可观测性与实验工具升级。

## 主要一手资料

### 原始地基

- Generative Agents: Interactive Simulacra of Human Behavior  
  https://arxiv.org/abs/2304.03442

### 记忆系统

- MemGPT: Towards LLMs as Operating Systems  
  https://arxiv.org/abs/2310.08560
- Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory  
  https://arxiv.org/abs/2504.19413

### 反思与自我改进

- Reflexion: Language Agents with Verbal Reinforcement Learning  
  https://arxiv.org/abs/2303.11366
- Voyager: An Open-Ended Embodied Agent with Large Language Models  
  https://arxiv.org/abs/2305.16291

### 规划、推理与行动

- ReAct: Synergizing Reasoning and Acting in Language Models  
  https://arxiv.org/abs/2210.03629
- Tree of Thoughts: Deliberate Problem Solving with Large Language Models  
  https://arxiv.org/abs/2305.10601
- Language Agent Tree Search Unifies Reasoning, Acting, and Planning in Language Models  
  https://arxiv.org/abs/2310.04406

### 多智能体框架

- CAMEL: Communicative Agents for Mind Exploration of Large Scale Language Model Society  
  https://arxiv.org/abs/2303.17760
- AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation  
  https://arxiv.org/abs/2308.08155
- MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework  
  https://arxiv.org/abs/2308.00352
- AgentScope: A Flexible yet Robust Multi-Agent Platform  
  https://arxiv.org/abs/2402.14034

### 社会仿真

- Generative agent-based modeling with actions grounded in physical, social, or digital space using Concordia  
  https://arxiv.org/abs/2312.03664
- AgentSociety: Large-scale Simulation of LLM-driven Generative Agents Advances Understanding of Human Behaviors and Society  
  https://arxiv.org/abs/2502.08691

### 评价体系

- AgentBench: Evaluating LLMs as Agents  
  https://arxiv.org/abs/2308.03688
- WebArena: A Realistic Web Environment for Building Autonomous Agents  
  https://arxiv.org/abs/2307.13854
- GAIA: A Benchmark for General AI Assistants  
  https://arxiv.org/abs/2311.12983
- SWE-bench: Can Language Models Resolve Real-World GitHub Issues?  
  https://arxiv.org/abs/2310.06770
- AI Agents That Matter  
  https://arxiv.org/abs/2407.01502

### 中文与推理模型

- DeepSeek-R1: Incentivizing Reasoning Capability in LLMs via Reinforcement Learning  
  https://arxiv.org/abs/2501.12948
- DeepSeek-R1 官方仓库  
  https://github.com/deepseek-ai/DeepSeek-R1
- Qwen3 Technical Report  
  https://arxiv.org/abs/2505.09388
- Qwen3 官方博客  
  https://qwenlm.github.io/blog/qwen3/

---

## 第五部分建议章节结构

第五部分建议追加 8 章：

28. 2023-2026：生成式智能体领域发生了什么
29. 记忆系统升级：从 memory stream 到可管理长期记忆
30. 反思系统升级：从事件反思到经验学习
31. 规划系统升级：从日程拆解到目标驱动行动
32. 多智能体协作升级：从自然偶遇到组织化协作
33. 社会仿真升级：从 Smallville 到更大规模实验
34. 评价体系升级：从故事可信到可复现实验指标
35. 基于 Generative Agents 的前沿升级路线图

这样全书结构变成：

- 第一部分：原论文与思想源头。
- 第二部分：从论文到 Generative Agents。
- 第三部分：源码深读。
- 第四部分：复现实验与扩展。
- 第五部分：前沿演进与项目升级。

## 第 31 章：2023-2026 生成式智能体领域发生了什么

### 章节目标

给读者一张领域演进地图，说明 Generative Agents 不是终点，而是很多后续方向的起点。

### 核心判断

2023 年 Generative Agents 的关键贡献是：

- 将 LLM 放入沙盒环境。
- 用 memory stream 保持经验。
- 用 retrieval 选择相关记忆。
- 用 reflection 形成高层认知。
- 用 planning 支撑长期行为。
- 用多智能体互动产生社会涌现。

2023-2026 年的发展大致沿着八条线展开：

1. 记忆从“记录和检索”走向“管理、压缩、分层、冲突处理”。
2. 反思从“总结经历”走向“失败后自我改进和技能积累”。
3. 规划从“日程拆解”走向“目标、工具、搜索和反馈闭环”。
4. 多智能体从“自然社交”走向“角色分工、组织流程和协同工作”。
5. 社会仿真从“小镇演示”走向“大规模、可测量、可复现的社会实验”。
6. 评价从“人看起来像不像”走向“任务成功率、可重复性、成本和统计显著性”。
7. 模型从通用聊天模型走向推理模型、中文本地模型和 thinking / non-thinking 模式切换。
8. 工程从 demo 走向 observability、日志、实验框架和可维护系统。

### 写作建议

这一章不要列论文流水账。应写成“从 Generative Agents 出发，后续方向分别补足了什么短板”。

### 图表建议

- 2023-2026 Agent 领域演进时间线。
- Generative Agents 架构与后续方向的雷达图：
  - memory
  - reflection
  - planning
  - collaboration
  - evaluation
  - scale
  - model capability
  - engineering

---

## 第 32 章：记忆系统升级：从 memory stream 到可管理长期记忆

### 经典架构的局限

Generative Agents 的 memory stream 已经很重要，但它仍然偏向“记下来，再检索出来”。

在长期运行中会遇到问题：

1. 记忆越积越多，检索噪声增加。
2. 相似事件反复写入，形成冗余。
3. 错误记忆或幻觉可能长期污染后续行为。
4. 角色关系需要结构化表达，单纯文本检索不够稳定。
5. 记忆需要分层：短期、情景、语义、关系、目标。

### 前沿洞察

#### MemGPT

MemGPT 将 LLM 类比为操作系统中的进程，强调外部记忆管理。核心启发是：上下文窗口应该像主存，长期记忆应该像外存，系统需要主动管理记忆的调入和调出。

对本书的价值：

- 让读者理解 memory stream 不只是向量库。
- 长期智能体需要“记忆管理策略”。
- 记忆写入、检索、压缩和淘汰都应成为显式模块。

#### Mem0

Mem0 面向生产级 Agent 长期记忆，强调可扩展、低延迟、个性化和跨会话记忆。对本项目的启发是：记忆不只为单次仿真服务，也可以服务长期角色成长。

对本书的价值：

- 将“记忆可复用”引入小镇居民。
- 角色可以跨多次仿真实验保留经验。
- 可以设计个人记忆、关系记忆、事件记忆三类存储。

### Generative Agents 当前实现

当前项目位置：

- `generative_agents/modules/memory/associate.py`
- `generative_agents/modules/storage/index.py`
- `generative_agents/modules/agent.py`

已有能力：

- event / chat / thought 三类记忆。
- LlamaIndex 向量索引。
- recency / relevance / importance 三因素检索。
- expire 和 cleanup。
- 角色级存储目录。

不足：

- 没有显式短期/长期分层。
- 没有记忆合并。
- 没有冲突检测。
- 没有人物关系图。
- 没有记忆重要性随时间更新。
- 没有跨实验稳定记忆库设计。

### 可实现升级 1：分层记忆

新增概念：

- working memory
- episodic memory
- semantic memory
- relationship memory
- goal memory

建议实现：

在 `Associate` 中扩展 node_type：

```text
event
chat
thought
relationship
goal
summary
```

写作中要讲：

- `event` 是原始观察。
- `chat` 是对话摘要。
- `thought` 是反思。
- `relationship` 是对某人的稳定认知。
- `goal` 是长期目标。
- `summary` 是一段时间的压缩记忆。

### 可实现升级 2：记忆合并

问题：

同类事件可能反复出现，例如角色每天去咖啡馆、每天整理资料。

升级思路：

- 写入新记忆前，检索相似记忆。
- 如果相似度高且时间接近，则合并成更高层摘要。
- 保留证据节点。

涉及源码：

- `Associate.add_node()`
- `Associate.retrieve_focus()`
- `Agent._add_concept()`

建议新增 prompt：

- `memory_should_merge.txt`
- `memory_merge_summary.txt`

### 可实现升级 3：关系图记忆

问题：

当前项目通过文本检索关系，稳定性有限。

升级思路：

为每个角色维护关系状态：

```json
{
  "target": "山姆",
  "affinity": -2,
  "trust": 1,
  "familiarity": 4,
  "last_interaction": "20240213-10:30",
  "evidence": ["node_12", "node_45"]
}
```

适合实验：

- 汤姆不喜欢山姆。
- 伊莎贝拉多次邀请某人。
- 克劳斯和玛丽亚关系形成。

涉及源码：

- `Associate.get_relation()`
- `Agent._chat_with()`
- `Scratch.prompt_summarize_relation()`

### 可实现升级 4：记忆冲突检测

问题：

角色可能记错派对时间、地点、邀请对象。

升级思路：

- 对新记忆检索可能冲突的旧记忆。
- 若冲突，生成 clarification thought。
- 高置信旧记忆不轻易被新幻觉覆盖。

建议 prompt：

- `memory_conflict_check.txt`
- `memory_conflict_resolve.txt`

### 评价指标

- 记忆冗余率。
- 过去对话引用准确率。
- 关系连续性评分。
- 事实保持率。
- 记忆冲突次数。
- 检索命中率。

### 写作重点

这一章要让读者看到：Generative Agents 的 memory stream 是起点，而不是终点。真正长期运行的智能体需要记忆治理。

---

## 第 33 章：反思系统升级：从事件反思到经验学习

### 经典架构的局限

Generative Agents 的 reflection 主要做高层总结，例如从近期记忆中提炼出 insight。

它的不足是：

1. 反思不一定针对失败。
2. 反思结果不一定转化为可执行策略。
3. 没有明确“下次如何做得更好”的学习循环。
4. 缺少技能库或经验库。

### 前沿洞察

#### Reflexion

Reflexion 将语言反馈作为强化学习式的 verbal feedback，让 Agent 在失败后总结经验，下一次任务使用这些经验改进行为。

对本项目的启发：

- 小镇智能体可以反思“我刚才为什么没邀请成功”。
- 反思不只是记录世界，也记录自己的决策质量。
- 失败经验可以影响下一次对话策略。

#### Voyager

Voyager 在 Minecraft 环境中通过自动课程、技能库和迭代提示持续探索。关键启发是：Agent 可以把成功经验沉淀成 reusable skills。

对本项目的启发：

- 居民可以形成“邀请别人参加活动”的技能。
- 可以形成“竞选宣传话术”的技能。
- 可以形成“组织会议”的技能。

### Generative Agents 当前实现

当前反思入口：

- `Agent.reflect()`
- `reflect_focus.txt`
- `reflect_insights.txt`
- `reflect_chat_planing.txt`
- `reflect_chat_memory.txt`

已有能力：

- 重要事件累积触发反思。
- 生成 focus 问题。
- 检索相关记忆。
- 生成 thought。
- 对对话生成计划影响和记忆总结。

不足：

- 没有明确失败检测。
- 没有任务结果评估。
- 没有经验策略库。
- 没有将反思转化为可执行规则。

### 可实现升级 1：行动后复盘

新增阶段：

```text
action -> outcome -> self_evaluate -> lesson -> future_strategy
```

示例：

- 伊莎贝拉邀请亚当参加派对，亚当拒绝。
- 角色复盘：亚当正在写书，直接邀请成功率低。
- 下次策略：先询问对方工作进度，再提出短时间参与。

涉及源码：

- `Agent.schedule_chat()`
- `Agent.reflect()`
- `Agent._chat_with()`

建议新增 prompt：

- `self_evaluate_action.txt`
- `extract_lesson.txt`
- `apply_lesson_to_plan.txt`

### 可实现升级 2：技能库

新增 memory 类型：

```text
skill
```

技能示例：

- 邀请别人参加活动。
- 传播竞选政策。
- 安慰关系亲密的人。
- 组织小型讨论会。

技能结构：

```json
{
  "name": "invite_to_event",
  "condition": "目标人物可能对活动感兴趣",
  "steps": [
    "先问候对方当前状态",
    "说明活动主题和时间地点",
    "根据对方忙碌程度给出灵活选择"
  ],
  "evidence": ["node_31", "node_42"]
}
```

涉及源码：

- `Associate.add_node()`
- `Agent._chat_with()`
- `Scratch.prompt_generate_chat()`

### 可实现升级 3：失败标签

为行动和对话结果增加结果标签：

```text
success
partial
failed
unknown
```

应用：

- 口头邀请成功。
- 到场失败。
- 对话未传播目标信息。
- 计划被打断。

涉及数据：

- checkpoint。
- `conversation.json`。
- `simulation.md`。

### 评价指标

- 同类任务第二次成功率是否提高。
- 失败后是否生成 lesson。
- lesson 是否在后续 prompt 中被调用。
- 对话策略是否变化。
- 技能库是否减少重复错误。

### 写作重点

这一章要区分：

- Reflection：我从经历中总结了什么。
- Reflexion-style learning：我下次应该怎么做得更好。
- Skill library：我把成功做法沉淀成可复用策略。

---

## 第 34 章：规划系统升级：从日程拆解到目标驱动行动

### 经典架构的局限

Generative Agents 当前 planning 主要围绕一天日程和当前计划拆解。

局限：

1. 目标不够显式。
2. 缺少多方案比较。
3. 缺少行动反馈后的重新规划。
4. 缺少工具调用。
5. 复杂任务难以被分解成可验证步骤。

### 前沿洞察

#### ReAct

ReAct 将 reasoning 和 acting 交替进行。Agent 一边思考，一边采取行动，再根据观察继续思考。

对本项目的启发：

- 当前 `Agent.think()` 已经有观察、计划、行动循环，但 reasoning trace 没有显式保存。
- 可以为复杂目标增加“思考-行动-观察”记录。

#### Tree of Thoughts

Tree of Thoughts 强调生成多个候选思路并评估选择，而不是一次生成。

对本项目的启发：

- 在选择地点、选择邀请对象、选择竞选策略时，可以生成多个候选方案。
- 对候选方案做评分。

#### LATS

Language Agent Tree Search 将推理、行动和规划统一到搜索框架中。

对本项目的启发：

- 对复杂任务如组织派对，可以搜索多条行动路径。
- 每条路径有预期结果和风险。

### Generative Agents 当前实现

核心文件：

- `generative_agents/modules/memory/schedule.py`
- `generative_agents/modules/agent.py`
- `generative_agents/modules/prompt/scratch.py`

已有能力：

- wake up。
- schedule init。
- schedule daily。
- schedule decompose。
- schedule revise。
- determine sector / arena / object。

不足：

- 没有独立 goal 对象。
- 没有多候选方案。
- 没有计划评分。
- 没有工具调用。
- 没有显式执行反馈闭环。

### 可实现升级 1：Goal 对象

新增数据结构：

```json
{
  "goal_id": "goal_party_001",
  "owner": "伊莎贝拉",
  "description": "让至少三位居民知道并参加情人节派对",
  "deadline": "2024-02-14 17:00",
  "status": "active",
  "success_criteria": [
    "至少三位居民被邀请",
    "至少两位居民到达霍布斯咖啡馆"
  ]
}
```

涉及源码：

- `Agent.__init__()`
- `Agent.make_schedule()`
- `Agent.make_plan()`
- `Agent.to_dict()`

建议新增模块：

- `modules/memory/goal.py`

### 可实现升级 2：目标驱动计划

新增 prompt：

- `goal_decompose.txt`
- `goal_select_next_step.txt`
- `goal_evaluate_progress.txt`

示例：

伊莎贝拉的派对目标可以拆成：

1. 准备食物和饮品。
2. 邀请常来咖啡馆的人。
3. 确认参加者。
4. 17:00 前回到霍布斯咖啡馆。

### 可实现升级 3：多候选行动选择

当前：

- `_determine_action()` 直接确定行动。

升级：

- 生成 3 个候选行动。
- 评估每个行动对当前 goal 的贡献。
- 选择最高分。

适用场景：

- 派对邀请谁。
- 竞选宣传去哪。
- 关系修复该怎么说。

### 可实现升级 4：轻量工具调用

不必直接引入复杂工具框架，可以先加入小镇内部工具：

- 查询当前时间。
- 查询某角色最近位置。
- 查询某事件传播人数。
- 查询某地点当前人数。

建议新增：

- `modules/tools/simulation_tools.py`

示例工具：

```text
get_agents_near(location)
get_recent_conversations(agent_name)
get_event_spread(keyword)
get_current_plan(agent_name)
```

### 评价指标

- 目标完成率。
- 计划步骤完成率。
- 计划变更次数。
- 无效行动比例。
- 多候选选择是否优于单次生成。

### 写作重点

这一章要说明：日程让角色“像人在生活”，目标驱动规划让角色“能完成复杂任务”。二者不是互相替代，而是上下层关系。

---

## 第 35 章：多智能体协作升级：从自然偶遇到组织化协作

### 经典架构的局限

Generative Agents 中的多智能体互动主要依赖：

- 空间偶遇。
- 当前记忆。
- 对话判断。
- 计划调整。

这适合社会涌现，但不适合组织化协作任务。

局限：

1. 没有明确团队结构。
2. 没有角色分工。
3. 没有共享任务面板。
4. 没有任务状态同步。
5. 协作依赖偶遇和聊天，效率低。

### 前沿洞察

#### CAMEL

CAMEL 通过 role-playing communicative agents 研究大模型社会中的协作与对话。

启发：

- 角色扮演可以用于任务驱动协作。
- 对话不只是社交，也可以是任务协议。

#### AutoGen

AutoGen 强调多 Agent conversation framework，让多个可配置 Agent 通过对话协作完成任务。

启发：

- 小镇居民可以围绕一个任务形成临时工作组。
- 可引入 manager / worker / critic 等协作角色。

#### MetaGPT

MetaGPT 用 SOP 和角色分工组织软件开发任务。

启发：

- 派对筹备可以有 SOP。
- 竞选宣传可以有 SOP。
- 社区活动可以有负责人、宣传者、执行者、评价者。

#### AgentScope

AgentScope 强调多智能体平台的灵活性、鲁棒性和可扩展性。

启发：

- 当前项目可以增加实验配置和运行观测机制。
- 不必一次重构成大平台，但可以吸收可配置和可观察思想。

### Generative Agents 当前实现

已有能力：

- 多 Agent 同一世界。
- Agent 之间能感知、聊天、等待。
- 对话影响记忆和日程。

不足：

- 没有团队任务。
- 没有共享目标。
- 没有协作角色分工。
- 没有公共任务状态。

### 可实现升级 1：公共事件板

新增数据：

```json
{
  "event_id": "valentine_party",
  "owner": "伊莎贝拉",
  "title": "情人节派对",
  "time": "2024-02-14 17:00",
  "location": "霍布斯咖啡馆",
  "participants": [],
  "tasks": [
    {"name": "准备饮品", "assignee": "伊莎贝拉", "status": "active"},
    {"name": "布置钢琴区", "assignee": "埃迪", "status": "todo"}
  ]
}
```

作用：

- 让活动从“某人的记忆”变成“公共对象”。
- 便于评价协作是否成功。

### 可实现升级 2：临时工作组

为某个事件创建工作组：

```text
organizer
helper
messenger
critic
participant
```

示例：

- 伊莎贝拉：organizer
- 埃迪：helper
- 阿伊莎：participant
- 乔治：critic 或 advisor

### 可实现升级 3：协作对话协议

新增 prompt：

- `team_assign_role.txt`
- `team_update_task.txt`
- `team_resolve_conflict.txt`
- `team_summarize_progress.txt`

### 可实现升级 4：公共记忆

当前记忆主要是个人记忆。可以为事件或团队增加公共记忆：

```text
results/checkpoints/<name>/storage/shared/<event_id>/
```

用途：

- 保存活动目标。
- 保存参与者确认。
- 保存任务状态。

### 评价指标

- 团队任务完成率。
- 分工明确度。
- 任务状态一致性。
- 冲突解决次数。
- 协作对话有效率。

### 写作重点

这一章要区分两种多智能体：

- 社会型多智能体：自然偶遇、聊天、传播。
- 组织型多智能体：角色分工、任务流、共享状态。

Generative Agents 的当前优势是前者，升级方向是补上后者。

---

## 第 36 章：社会仿真升级：从 Smallville 到更大规模实验

### 经典架构的局限

Smallville 的 25 个智能体足以展示现象，但对社会仿真来说仍有局限：

1. 数量小。
2. 运行成本高。
3. 缺少统计重复实验。
4. 缺少外部社会数据校准。
5. 现象解释容易依赖个案故事。

### 前沿洞察

#### Concordia / Generative Agent-Based Modeling

Concordia 方向强调用 LLM 构建 grounded generative agent-based models，把智能体行动放到物理、数字或社会空间中，并由环境管理者解释和约束行动。

启发：

- 小镇实验不只看个体故事，还要统计群体模式。
- 传播、聚集、态度变化都可以量化。
- 环境不只是地图背景，还要承担行动可行性判断。

#### AgentSociety

AgentSociety 面向更大规模 LLM-driven generative agents，用于理解人类行为和社会现象。

启发：

- 大规模仿真需要更强工程支撑。
- 需要 agent profile、环境、交互、指标和可视化。
- 小规模项目可以先借鉴实验方法，不必直接追求大规模。

### Generative Agents 当前实现

优势：

- 角色设定完整。
- 地图和回放完整。
- 运行链路清晰。
- 适合教学和小规模实验。

不足：

- Agent 数量扩展受 LLM 成本限制。
- 缺少批量实验管理。
- 缺少统计脚本。
- 缺少多次重复运行比较。
- 缺少随机种子和实验配置文件。

### 可实现升级 1：实验配置文件

新增：

```text
experiments/party_small.json
experiments/election_small.json
experiments/reflection_ablation.json
```

配置内容：

```json
{
  "name": "party_small",
  "start": "20240214-08:00",
  "step": 72,
  "stride": 10,
  "agents": ["伊莎贝拉", "亚当", "阿伊莎", "埃迪"],
  "metrics": ["diffusion", "attendance"]
}
```

### 可实现升级 2：批量运行

新增脚本：

```text
tools/run_experiment_batch.py
```

能力：

- 读取实验配置。
- 多次运行。
- 自动 compress。
- 输出指标。

### 可实现升级 3：传播统计

基于 `conversation.json` 统计：

- 谁先提到事件。
- 谁接收事件。
- 谁再次传播。
- 事实是否被保留。

### 可实现升级 4：群体轨迹统计

基于 `movement.json` 统计：

- 指定时间窗内地点人数。
- 聚集峰值。
- 到场率。
- 活动路径。

### 评价指标

- 信息传播覆盖率。
- 到场率。
- 平均传播深度。
- 事实保真度。
- 群体聚集程度。
- 多次运行方差。

### 写作重点

这一章要明确：本书不追求把 Generative Agents 直接改成万级 Agent 平台，而是教读者用严谨实验方法从小规模仿真走向可分析仿真。

---

## 第 37 章：评价体系升级：从故事可信到可复现实验指标

### 经典架构的局限

Generative Agents 论文已有 controlled evaluation、ablation study 和 end-to-end evaluation。但后续 Agent 领域暴露出更大的评价问题：

1. 演示效果和真实能力容易混淆。
2. 单次运行结果不可重复。
3. 成本、延迟、失败率常被忽略。
4. benchmark 与真实使用之间存在差距。
5. 评价指标容易被模型或框架过拟合。

### 前沿洞察

#### AgentBench

AgentBench 关注在多种环境中评估 LLM as agents。

启发：

- 评价应覆盖多任务、多环境。
- 不应只看单个故事。

#### WebArena

WebArena 提供真实网页环境，强调 Agent 在复杂真实环境中的任务完成能力。

启发：

- 环境 grounding 对 Agent 评价很重要。
- 小镇地图同样需要检查“承诺是否落地到位置”。

#### GAIA

GAIA 测试通用 AI 助手完成真实问题的能力，强调多步骤推理和工具使用。

启发：

- 智能体评价需要看复杂任务链，而不是单轮回答。

#### SWE-bench

SWE-bench 测试模型解决真实 GitHub issue。

启发：

- 任务完成应有可验证结果。
- 小镇任务也应设计可验证的成功条件。

#### AI Agents That Matter

该论文强调很多 Agent 评测存在可重复性、基线、成本和统计显著性问题。

启发：

- 本书实验必须记录成本和失败。
- 不应只挑好看的 replay。

### Generative Agents 当前评价基础

已有材料：

- `simulation.md`
- `conversation.json`
- checkpoint
- `movement.json`
- LLM summary
- 运行日志

不足：

- 没有自动指标脚本。
- 没有多次运行统计。
- 没有统一实验配置。
- 没有成本记录。
- 没有基线对照。

### 可实现升级 1：实验指标脚本

建议工具：

- `tools/analyze_conversation_keywords.py`
- `tools/analyze_attendance.py`
- `tools/compare_reflection_runs.py`
- `tools/export_experiment_report.py`

### 可实现升级 2：统一评价报告

每次实验生成：

```text
results/evaluations/<name>/report.md
results/evaluations/<name>/metrics.json
```

指标示例：

```json
{
  "diffusion": {
    "unique_informed_agents": 5,
    "diffusion_depth": 2,
    "fact_preservation_score": 0.8
  },
  "attendance": {
    "invited": 4,
    "accepted": 3,
    "arrived": 2
  },
  "runtime": {
    "steps": 72,
    "total_minutes": 95,
    "llm_failures": 3
  }
}
```

### 可实现升级 3：基线对照

基线条件：

1. 默认完整系统。
2. 高反思阈值。
3. 少记忆检索。
4. 禁用对话。
5. 小模型 vs 大模型。

### 可实现升级 4：成本记录

记录：

- LLM 调用次数。
- 成功次数。
- 失败次数。
- 每个 provider 的耗时。
- 本地模型运行时间。

已有基础：

- `LLMModel.get_summary()`
- `Agent.abstract()` 中的 llm summary。

### 写作重点

这一章要直接回应“AI Agents That Matter”式批评：一本严肃的项目书不能只展示成功 replay，必须记录基线、失败、成本和可重复性。

---

## 第 38 章：基于 Generative Agents 的前沿升级路线图

### 章节目标

将前面几章的前沿洞察收束成一个可执行升级计划，告诉读者如何从当前项目一步步改造，而不是一次性大重构。

## 升级阶段 1：低风险观测增强

### 目标

不改 Agent 行为，只增强实验观测能力。

### 改造内容

1. 增加实验配置文件。
2. 增加 conversation 关键词统计。
3. 增加到场统计。
4. 增加 LLM 调用统计输出。
5. 增加实验报告生成。

### 涉及文件

- 新增 `experiments/*.json`
- 新增 `tools/analyze_conversation_keywords.py`
- 新增 `tools/analyze_attendance.py`
- 新增 `tools/export_experiment_report.py`

### 风险

低。基本不改变原系统行为。

### 适合写书位置

第五部分第 36-37 章。

## 升级阶段 2：记忆系统增强

### 目标

在不破坏原 memory stream 的基础上增加记忆治理能力。

### 改造内容

1. 增加 `relationship` 记忆类型。
2. 增加记忆合并 prompt。
3. 增加冲突检测 prompt。
4. 增加关系状态导出。

### 涉及文件

- `modules/memory/associate.py`
- `modules/agent.py`
- `modules/prompt/scratch.py`
- `data/prompts/memory_should_merge.txt`
- `data/prompts/memory_merge_summary.txt`
- `data/prompts/memory_conflict_check.txt`

### 风险

中等。会影响检索和对话。

### 评价实验

- 关系形成实验。
- 派对时间地点保真度。
- 选举事实保真度。

## 升级阶段 3：目标驱动规划

### 目标

让智能体不只按日程生活，还能围绕事件目标行动。

### 改造内容

1. 新增 `Goal` 数据结构。
2. 给角色配置或运行时状态增加 goals。
3. 增加 goal decomposition prompt。
4. 在 `make_plan()` 前检查 active goals。
5. 增加 goal progress evaluation。

### 涉及文件

- 新增 `modules/memory/goal.py`
- `modules/agent.py`
- `modules/memory/schedule.py`
- `modules/prompt/scratch.py`
- `data/prompts/goal_decompose.txt`
- `data/prompts/goal_select_next_step.txt`
- `data/prompts/goal_evaluate_progress.txt`

### 风险

高。会改变核心行为决策。

### 评价实验

- 情人节派对到场率。
- 镇长竞选传播覆盖率。
- 自定义校园讲座实验。

## 升级阶段 4：反思学习与技能库

### 目标

让角色从失败中学习，并形成可复用经验。

### 改造内容

1. 增加 action outcome。
2. 增加 self-evaluation prompt。
3. 增加 lesson 记忆。
4. 增加 skill 记忆。
5. 在对话生成和计划生成时检索相关 skill。

### 涉及文件

- `modules/memory/associate.py`
- `modules/agent.py`
- `modules/prompt/scratch.py`
- `data/prompts/self_evaluate_action.txt`
- `data/prompts/extract_lesson.txt`
- `data/prompts/apply_lesson_to_plan.txt`

### 风险

高。容易引入模型自我解释幻觉。

### 评价实验

- 同类邀请任务重复运行。
- 第一次失败和第二次成功率对比。
- 对话策略变化分析。

## 升级阶段 5：组织化多智能体协作

### 目标

从自然社交扩展到团队任务。

### 改造内容

1. 增加公共事件板。
2. 增加任务列表。
3. 增加团队角色。
4. 增加共享记忆。
5. 增加团队进度总结。

### 涉及文件

- 新增 `modules/memory/shared.py`
- 新增 `modules/memory/team.py`
- `modules/game.py`
- `modules/agent.py`
- `data/prompts/team_assign_role.txt`
- `data/prompts/team_update_task.txt`
- `data/prompts/team_summarize_progress.txt`

### 风险

很高。会显著改变系统范式。

### 评价实验

- 派对筹备分工。
- 竞选团队协作。
- 校园讲座组织。

---

## 与当前项目章节的衔接

| 前沿方向 | 前四部分基础 | 第五部分升级 |
|---|---|---|
| 长期记忆 | 第 18 章记忆 | 第 32 章记忆治理 |
| 反思学习 | 第 21 章反思 | 第 33 章经验学习 |
| 目标规划 | 第 19 章日程 | 第 34 章目标驱动 |
| 多智能体协作 | 第 20 章社交 | 第 35 章组织化协作 |
| 社会仿真 | 第 23-24 章传播实验 | 第 36 章统计仿真 |
| 可信评价 | 第 29 章评价框架 | 第 37 章可复现指标 |
| 中文模型 | 第 21、27 章模型适配 | 第 38 章模式切换与成本优化 |

## 第五部分写作禁区

1. 不要改写前四部分结构。
   - 前沿演进只追加在最后。
2. 不要堆论文名。
   - 每个前沿成果必须回答“对本项目有什么用”。
3. 不要承诺当前项目已经具备这些能力。
   - 必须明确哪些是已有，哪些是建议升级。
4. 不要一上来大重构。
   - 先从观测工具、评价脚本、记忆增强做起。
5. 不要只讲成功。
   - 前沿升级也可能引入更多幻觉、成本和复杂性。

## 第五部分结束时读者应掌握

读完第五部分，读者应该能：

1. 说清 2023 年 Generative Agents 之后主要演进方向。
2. 理解 memory stream 与现代长期记忆系统的差异。
3. 区分 reflection、Reflexion-style learning 和 skill library。
4. 知道日程规划和目标驱动规划的关系。
5. 区分自然多智能体互动和组织化多智能体协作。
6. 用更严谨的指标评价智能体系统。
7. 基于 Generative Agents 设计渐进式升级路线。

## 后续衔接

本文件完成后，基础规划文件已经形成闭环：

1. `01_original_paper_outline.md`
2. `02_paper_to_code_mapping.md`
3. `03_source_reading_index.md`
4. `04_experiment_design.md`
5. `05_frontier_upgrade_notes.md`

下一步应进入：

- `06_book_master_blueprint.md`

该文件需要把五个部分合并成全书总蓝图，包括：

- 全书定位。
- 读者画像。
- 最终章节目录。
- 每章写作目标。
- 每章预计篇幅。
- 图表清单。
- 实验清单。
- 写作顺序。
- 不做事项。
