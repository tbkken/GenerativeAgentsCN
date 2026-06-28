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

### 图 4-1：Persona 在 Generative Agents 架构中的位置

章节：第 4 章。

元素：

- Persona
- Memory Stream
- Planning
- Dialogue
- Reaction

### 表 4-1：人物定义的六个层面

章节：第 4 章。

字段：

- 定义层
- 中文意思
- 对可信行为的影响

### 表 4-2：项目中定义一个角色涉及的系统模块

章节：第 4 章。

字段：

- 系统模块
- 接收的人物信息
- 对角色行为的影响

### 图 4-2：项目中角色定义进入系统的路径

章节：第 4 章。

元素：

- agent.json
- data/config.json
- Game.__init__()
- Agent.__init__()
- Scratch
- Spatial
- Schedule
- Associate
- Maze / Tile
- Prompt

### 表 4-3：基础人物描述 prompt 的字段含义

章节：第 4 章。

字段：

- 模板字段
- 主要来源
- 中文意思
- 对行为的影响

### 表 4-4：Smallville 的 25 个角色

章节：第 4 章。

字段：

- 角色
- 年龄
- 身份与背景
- 性格线索
- 当前目标或事件线索

### 图 4-3：初始人物定义与后续经验的关系

章节：第 4 章。

元素：

- 初始 Persona
- 角色进入 Smallville
- Observation / Action
- Memory Stream
- Reflection

### 表 5-1：Memory Stream 保存的经验类型

章节：第 5 章。

字段：

- 经验类型
- 例子
- 为什么要保存

### 图 5-1：经验进入 Memory Stream 后如何参与行为生成

章节：第 5 章。

元素：

- Observation
- Action
- Dialogue
- Reflection
- Memory Stream
- Retrieval
- Planning
- Reaction

### 表 5-2：聊天历史、系统日志和 Memory Stream 的区别

章节：第 5 章。

字段：

- 对比维度
- 聊天历史
- 系统日志
- Memory Stream

### 表 5-3：定义一条记忆涉及的系统模块

章节：第 5 章。

字段：

- 系统模块
- 接收或生成什么
- 对 Memory Stream 的作用

### 图 5-2：项目中一条记忆进入 Memory Stream 的路径

章节：第 5 章。

元素：

- Maze / Tile
- Event
- Agent.percept()
- Agent._add_concept()
- Associate.add_node()
- Concept
- LlamaIndex
- AssociateRetriever

### 表 5-4：Event 字段含义

章节：第 5 章。

字段：

- Event 字段
- 中文意思
- 对记忆的影响

### 表 5-5：Concept 字段含义

章节：第 5 章。

字段：

- Concept 字段
- 中文意思
- 对后续行为的影响

### 表 5-6：自然语言记忆的优点和代价

章节：第 5 章。

字段：

- 优点
- 代价
- 本书后续会如何处理

### 表 5-7：重要性评分 prompt

章节：第 5 章。

字段：

- Prompt
- 评分对象
- 用途

### 表 5-8：Memory Stream 对未来行为的影响

章节：第 5 章。

字段：

- 行为
- Memory Stream 提供什么
- 如果没有记忆会怎样

### 图 5-3：Reflection 写回 Memory Stream

章节：第 5 章。

元素：

- 原始记忆
- 检索相关记忆
- Reflection
- thought
- Memory Stream

### 表 5-9：Memory Stream 的局限

章节：第 5 章。

字段：

- 局限
- 表现
- 后续升级方向

### 表 6-1：不能读取全部记忆的原因

章节：第 6 章。

字段：

- 问题
- 表现
- 对行为的影响

### 表 6-2：Retrieval 的三个维度

章节：第 6 章。

字段：

- 维度
- 中文意思
- 回答的问题
- 如果缺失会怎样

### 图 6-1：Recency、Importance、Relevance 三因素检索模型

章节：第 6 章。

元素：

- 当前情境 / focus
- 候选记忆 Concept
- Recency
- Importance
- Relevance
- Final retrieval score
- Top-K 记忆
- Prompt

### 表 6-3：三个维度如何互相补足

章节：第 6 章。

字段：

- 当前情境
- 只看相关性的问题
- 只看近期性的问题
- 只看重要性的问题
- 三因素平衡后的结果

### 表 6-4：Retrieval 涉及的系统模块

章节：第 6 章。

字段：

- 系统模块
- 负责什么
- 对 Retrieval 的作用

### 图 6-2：项目中 retrieve_focus() 的检索流程

章节：第 6 章。

元素：

- focus / query
- Associate.retrieve_focus()
- event + thought 候选范围
- LlamaIndex.retrieve()
- AssociateRetriever._retrieve()
- recency / relevance / importance
- final score
- Top-K Concept

### 表 6-5：项目中的检索接口

章节：第 6 章。

字段：

- 接口
- 查询范围
- 典型用途
- 注意点

### 表 6-6：AssociateRetriever 的重排序步骤

章节：第 6 章。

字段：

- 步骤
- 代码中的来源
- 中文意思

### 表 6-7：Retrieval 默认参数

章节：第 6 章。

字段：

- 参数
- 默认值
- 中文意思
- 行为倾向

### 表 6-8：三因素排序示例

章节：第 6 章。

字段：

- 候选记忆
- recency
- relevance
- importance
- 综合判断

### 表 6-9：Retrieval 的触发场景

章节：第 6 章。

字段：

- 任务
- focus 从哪里来
- 检索用途

### 表 6-10：派对邀请的检索路径

章节：第 6 章。

字段：

- 因素
- 派对记忆的表现
- 结果

### 表 6-11：竞选话题需要同时检索事实和立场

章节：第 6 章。

字段：

- 记忆
- 作用

### 表 6-12：检索失败的常见后果

章节：第 6 章。

字段：

- 失败类型
- 表现
- 读者在实验中应该观察什么

### 表 6-13：检索参数对行为风格的影响

章节：第 6 章。

字段：

- 调整方向
- 角色表现
- 风险

### 图 7-1：Reflection 的基本链路

章节：第 7 章。

元素：

- 近期观察与想法
- 生成焦点问题
- 围绕问题检索记忆
- 证据片段
- insight
- thought
- Memory Stream

### 图 7-2：Reflection 与 Memory、Retrieval、Planning、Dialogue 的连接

章节：第 7 章。

元素：

- Memory Stream
- Retrieval
- Reflection
- Thought Memory
- Planning
- Dialogue
- Action

### 表 7-1：Reflection 与其他架构模块的关系

章节：第 7 章。

字段：

- 连接模块
- Reflection 的关系

### 表 7-2：Reflection 的候选输入边界

章节：第 7 章。

字段：

- 输入类型
- 中文意思
- 在 Reflection 中的作用
- 边界

### 图 7-3：Reflection 的输入筛选路径

章节：第 7 章。

元素：

- poignancy 达到阈值
- retrieve_events()
- retrieve_thoughts()
- access 排序
- max_importance
- reflect_focus
- self.chats
- reflect_chat_planing / reflect_chat_memory

### 表 7-3：焦点问题与证据结构

章节：第 7 章。

字段：

- 做法
- 结果
- 风险

### 表 7-4：Insight 的证据边界

章节：第 7 章。

字段：

- 原始证据
- 过度推断
- 更合理的 insight

### 表 7-5：Thought 写回 memory stream 的作用

章节：第 7 章。

字段：

- 结果
- 含义

### 表 7-6：Klaus 与 Maria 的反思链路

章节：第 7 章。

字段：

- 层次
- 内容

### 表 7-7：Reflection 对行为链路的影响

章节：第 7 章。

字段：

- 影响对象
- 示例 thought
- 可能改变的行为

### 表 7-8：对话后的 Reflection

章节：第 7 章。

字段：

- Prompt
- 作用

### 表 7-9：Reflection 的工程成本

章节：第 7 章。

字段：

- 成本
- 表现
- 控制入口

### 表 7-10：Reflection 的失败模式

章节：第 7 章。

字段：

- 失败模式
- 表现
- 检查方法

### 表 7-11：如何观察 Reflection

章节：第 7 章。

字段：

- 观察入口
- 看什么

### 表 7-12：项目中 Reflection 的工程化差异

章节：第 7 章。

字段：

- 差异
- 项目做法
- 影响

### 表 7-13：Reflection 与后续章节的关系

章节：第 7 章。

字段：

- 后续章节
- 关联点

### 图 8-1：Planning 的基本链路

章节：第 8 章。

### 图 8-2：新一天日程生成前的状态接续

章节：第 8 章。

### 图 8-3：Planning 的递归拆解

章节：第 8 章。

### 图 9-1：Reacting 在行动循环中的优先级

章节：第 9 章。

### 图 9-2：等待机制

章节：第 9 章。

### 图 10-1：Dialogue 的完整链路

章节：第 10 章。

### 图 10-2：对话写回

章节：第 10 章。

### 图 10-3：情人节派对的对话传播链路

章节：第 10 章。

### 图 11-1：Generative Agents 的两层评价结构

章节：第 11 章。

### 表 11-1：五类访谈问题与对应架构能力

章节：第 11 章。

字段：

- 能力
- 评价问题
- 对应架构组件
- 本书复现方式

### 表 11-2：访谈评价的三层文本

章节：第 11 章。

字段：

- 层次
- 来源
- 作用

### 图 11-2：原始 Stanford 项目的访谈回答链路

章节：第 11 章。

元素：

- 访谈问题
- 记忆检索
- `summarize_ideas_v1.txt`
- 问题相关摘要
- 角色回答

### 图 11-3：情人节派对邀请扩散路径

章节：第 11 章。

### 表 11-3：论文评价指标到 Generative Agents 评价指标的映射

章节：第 11 章。

字段：

- 论文评价
- 本书复现实验
- 可检查证据
- 失败信号

---

## 第二部分：从论文到 Generative Agents

### 图 12-1：项目谱系图

章节：第 12 章。

元素：

- Stanford 原始项目
- wounderland
- Generative Agents

### 表 12-1：三个项目对比

章节：第 12 章。

字段：

- 对比项
- Stanford 原版
- wounderland
- Generative Agents

### 表 13-1：论文概念到源码模块映射

章节：第 13 章。

来源：

- `02_paper_to_code_mapping.md`

### 图 13-1：Generative Agents 运行链路总图

章节：第 13 章。

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

### 图 14-1：后端地图与前端地图的双层结构

章节：第 14 章。

元素：

- `maze.json`
- Python `Maze`
- `tilemap.json`
- Phaser

### 图 14-2：地址层级

章节：第 14 章。

元素：

- world
- sector
- arena
- game_object

### 图 15-1：角色配置进入 Agent 对象

章节：第 15 章。

元素：

- `data/config.json`
- `agent.json`
- `agent_base`
- `Game.__init__`
- `Agent.__init__`

### 图 16-1：仿真主循环

章节：第 16 章。

元素：

- step
- each agent
- `Game.agent_think`
- `Agent.think`
- checkpoint
- timer forward

### 图 17-1：感知范围与事件收集

章节：第 17 章。

### 图 18-1：Event -> Concept -> Vector Index

章节：第 18 章。

### 图 18-2：Generative Agents 中的 Retrieval 排序

章节：第 18 章。

### 图 19-1：三层计划结构

章节：第 19 章。

元素：

- `daily_plan`
- daily schedule
- decomposed action

### 图 20-1：社交反应决策树

章节：第 20 章。

### 图 20-2：对话如何变成记忆和日程

章节：第 20 章。

### 图 21-1：反思触发和写回流程

章节：第 21 章。

### 图 22-1：Prompt 调用生命周期

章节：第 22 章。

### 表 22-1：OpenAI、Ollama、MiniMax Provider 对比

章节：第 22 章。

### 图 23-1：Checkpoint 到 Replay 的数据产物流

章节：第 23 章。

### 图 23-2：一个 Step 展开成多帧回放

章节：第 23 章。

---

## 第四部分：复现实验与扩展

### 图 24-1：情人节派对传播证据链

章节：第 24 章。

### 图 24-2：情人节派对传播的真实证据桌面

章节：第 24 章。

### 图 24-3：10 人派对传播实验的角色分工

章节：第 24 章。

### 图 24-4：10 人派对实验的人物自然关系

章节：第 24 章。

### 表 24-1：情人节派对实验配置

章节：第 24 章。

### 表 24-2：派对传播路径表

章节：第 24 章。

### 表 24-3：派对到场行为表

章节：第 24 章。

### 表 25-1：镇长竞选实验配置

章节：第 25 章。

### 图 25-1：竞选信息传播网络

章节：第 25 章。

### 图 25-2：镇长竞选信息扩散证据板

章节：第 25 章。

### 图 25-3：镇长竞选实验任务关系图

章节：第 25 章。

### 图 25-4：镇长竞选实验人物关系图

章节：第 25 章。

### 表 25-2：支持、反对、中立态度表

章节：第 25 章。

### 表 26-1：自定义小镇事件设计模板

章节：第 26 章。

### 图 27-1：项目扩展难度阶梯

章节：第 27 章。

### 图 27-2：扩展小镇的四层断层风险

章节：第 27 章。

### 图 27-3：林晓头像资源预览

章节：第 27 章。

### 图 27-4：林晓行走纹理资源预览

章节：第 27 章。

### 图 28-2：模型配置与运行稳定性观察台

章节：第 28 章。

### 表 28-1：中文本地模型实验对比表

章节：第 28 章。

### 表 29-1：可信行为评分表

章节：第 29 章。

### 表 30-1：风险、证据和应对策略

章节：第 30 章。

---

## 第五部分：前沿演进与项目升级

### 图 31-1：2023-2026 Agent 领域演进时间线

章节：第 31 章。

### 图 32-1：从 Memory Stream 到分层记忆

章节：第 32 章。

### 图 33-1：Reflection 与 Reflexion-style Learning 对比

章节：第 33 章。

### 图 34-1：日程规划与目标驱动规划

章节：第 34 章。

### 图 35-1：自然社交与组织化协作

章节：第 35 章。

### 图 36-1：从单次小镇运行到批量社会仿真

章节：第 36 章。

### 表 37-1：Agent 评价基准对照

章节：第 37 章。

### 图 38-1：Generative Agents 五阶段升级路线

章节：第 38 章。

### 表 38-1：升级收益与风险矩阵

章节：第 38 章。

---

## 图表制作原则

1. 优先使用清晰结构图，不追求装饰。
2. 源码图必须带文件名或函数名。
3. 实验表必须能从实际输出材料中填充。
4. 前沿图必须回到 Generative Agents 的升级方向。
5. 同一类图保持统一风格。

## 后续任务

实际写正文时，每章需要在开头标注本章图表需求，并在图表位置写：

```text
[图 X-X：图名]
```

如果图表来自项目真实截图，需要记录截图来源路径和生成命令。
