# 全书图表清单 v0.1

## 文档目标

本文件用于规划全书图表。图表不是装饰，而是教学材料的一部分。每张图都必须服务一个明确的理解目标。

## 图表类型

- 概念图：解释论文思想。
- 架构图：解释系统组成。
- 调用链图：解释源码执行顺序。
- 数据流图：解释数据如何流动。
- 对比表：解释差异、取舍和演进。
- 实验表：记录实验配置、结果和评价。
- 截图：展示项目真实运行结果。

---

## 第一部分：原论文与思想源头

### 图 1-1：Generative Agents 的领域交叉位置

章节：第 1 章。

目的：

说明这篇论文处在 HCI、LLM、游戏 AI、社会仿真四个方向的交叉处。

元素：

- HCI
- LLM
- Game AI
- Social Simulation
- Generative Agents

### 图 1-2：从传统 NPC 到生成式智能体的演进

章节：第 1 章。

目的：

帮助读者理解为什么 Generative Agents 不是普通 NPC，也不是普通聊天机器人。

元素：

- 手写脚本 NPC
- 强化学习智能体
- LLM 角色扮演
- Generative Agents

### 表 2-1：单轮聊天机器人 vs 生成式智能体

章节：第 2 章。

目的：

对比二者在记忆、时间、空间、行动和社会互动上的差异。

字段：

- 对比点
- 单轮聊天机器人
- 生成式智能体

### 图 2-1：信息跨智能体传播链路

章节：第 2 章。

目的：

说明一个事件如何从 A 的话语变成 B 的记忆，再影响 C 的行动。

元素：

- A 的计划
- A 与 B 对话
- B 写入记忆
- B 与 C 对话
- C 改变计划

### 图 3-1：Smallville 实验环境构成

章节：第 3 章。

目的：

解释 Smallville 是实验装置，不是单纯游戏界面。

元素：

- Agent
- Map
- Object
- Time
- User Intervention
- Memory
- Dialogue

### 表 3-1：Smallville 中三类涌现行为

章节：第 3 章。

字段：

- 行为类型
- 论文案例
- 背后机制
- 本书复现实验

### 图 4-1：Observation 进入 Memory Stream

章节：第 4 章。

元素：

- Observation
- Natural language description
- Timestamp
- Memory stream
- Future retrieval

### 表 4-1：聊天历史、日志、Memory Stream 的区别

章节：第 4 章。

### 图 5-1：Retrieval 三因素模型

章节：第 5 章。

元素：

- Recency
- Importance
- Relevance
- Normalization
- Final retrieval score

### 图 6-1：Reflection Pipeline

章节：第 6 章。

元素：

- Recent memories
- Generate questions
- Retrieve related memories
- Generate insights
- Write back to memory stream

### 图 7-1：Planning、Reacting、Dialogue 的行为循环

章节：第 7 章。

### 表 8-1：论文 Controlled Evaluation 五类能力

章节：第 8 章。

字段：

- 能力
- 评价问题
- 对应架构组件
- 本书复现方式

### 表 8-2：论文消融实验条件

章节：第 8 章。

字段：

- 条件
- 保留组件
- 移除组件
- 预期影响

---

## 第二部分：从论文到 GenerativeAgentsCN

### 图 9-1：项目谱系图

章节：第 9 章。

元素：

- Stanford 原始项目
- wounderland
- GenerativeAgentsCN

### 表 9-1：三个项目对比

章节：第 9 章。

字段：

- 对比项
- Stanford 原版
- wounderland
- GenerativeAgentsCN

### 表 10-1：论文概念到源码模块映射

章节：第 10 章。

来源：

- `02_paper_to_code_mapping.md`

### 图 10-1：GenerativeAgentsCN 运行链路总图

章节：第 10 章。

元素：

- `start.py`
- `Game`
- `Agent`
- `Maze`
- `Memory`
- `Prompt`
- `Checkpoint`
- `Compress`
- `Replay`

---

## 第三部分：源码深读

### 图 11-1：后端地图与前端地图的双层结构

章节：第 11 章。

元素：

- `maze.json`
- Python `Maze`
- `tilemap.json`
- Phaser

### 图 11-2：地址层级

章节：第 11 章。

元素：

- world
- sector
- arena
- game_object

### 图 12-1：角色配置进入 Agent 对象

章节：第 12 章。

元素：

- `data/config.json`
- `agent.json`
- `agent_base`
- `Game.__init__`
- `Agent.__init__`

### 图 13-1：仿真主循环

章节：第 13 章。

元素：

- step
- each agent
- `Game.agent_think`
- `Agent.think`
- checkpoint
- timer forward

### 图 14-1：感知范围与事件收集

章节：第 14 章。

### 图 15-1：Event -> Concept -> Vector Index

章节：第 15 章。

### 图 15-2：GenerativeAgentsCN 中的 Retrieval 排序

章节：第 15 章。

### 图 16-1：三层计划结构

章节：第 16 章。

元素：

- `daily_plan`
- daily schedule
- decomposed action

### 图 17-1：社交反应决策树

章节：第 17 章。

### 图 17-2：对话如何变成记忆和日程

章节：第 17 章。

### 图 18-1：反思触发和写回流程

章节：第 18 章。

### 图 19-1：Prompt 调用生命周期

章节：第 19 章。

### 表 19-1：OpenAI、Ollama、MiniMax Provider 对比

章节：第 19 章。

### 图 20-1：Checkpoint 到 Replay 的数据产物流

章节：第 20 章。

### 图 20-2：一个 Step 展开成多帧回放

章节：第 20 章。

---

## 第四部分：复现实验与扩展

### 表 21-1：情人节派对实验配置

章节：第 21 章。

### 表 21-2：派对传播路径表

章节：第 21 章。

### 表 21-3：派对到场行为表

章节：第 21 章。

### 表 22-1：镇长竞选实验配置

章节：第 22 章。

### 图 22-1：竞选信息传播网络

章节：第 22 章。

### 表 22-2：支持、反对、中立态度表

章节：第 22 章。

### 表 23-1：自定义小镇事件设计模板

章节：第 23 章。

### 图 24-1：项目扩展难度阶梯

章节：第 24 章。

### 表 25-1：中文本地模型实验对比表

章节：第 25 章。

### 表 26-1：可信行为评分表

章节：第 26 章。

### 表 27-1：风险、证据和应对策略

章节：第 27 章。

---

## 第五部分：前沿演进与项目升级

### 图 28-1：2023-2026 Agent 领域演进时间线

章节：第 28 章。

### 图 29-1：从 Memory Stream 到分层记忆

章节：第 29 章。

### 图 30-1：Reflection 与 Reflexion-style Learning 对比

章节：第 30 章。

### 图 31-1：日程规划与目标驱动规划

章节：第 31 章。

### 图 32-1：自然社交与组织化协作

章节：第 32 章。

### 图 33-1：从单次小镇运行到批量社会仿真

章节：第 33 章。

### 表 34-1：Agent 评价基准对照

章节：第 34 章。

### 图 35-1：GenerativeAgentsCN 五阶段升级路线

章节：第 35 章。

### 表 35-1：升级收益与风险矩阵

章节：第 35 章。

---

## 图表制作原则

1. 优先使用清晰结构图，不追求装饰。
2. 源码图必须带文件名或函数名。
3. 实验表必须能从实际输出材料中填充。
4. 前沿图必须回到 GenerativeAgentsCN 的升级方向。
5. 同一类图保持统一风格。

## 后续任务

实际写正文时，每章需要在开头标注本章图表需求，并在图表位置写：

```text
[图 X-X：图名]
```

如果图表来自项目真实截图，需要记录截图来源路径和生成命令。
