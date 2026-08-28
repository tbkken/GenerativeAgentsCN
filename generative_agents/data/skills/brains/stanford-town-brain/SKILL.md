---
name: stanford-town-brain
description: "为每个生命周期事件选择最小相关的认知技能包，来驱动一个斯坦福小镇智能体。"
example_input: "生命周期事件：2026-08-19 07:00，简在公寓里刚刚醒来，今天上午要去咖啡馆工作。请为简选择最合适的技能包并给出当前世界步可以直接执行的计划或行动。"
---

# Stanford Town Brain

## 目标

让 Agent 在虚拟世界中持续感知、回忆、排程、计划、行动、交谈和反思。Brain 是自然语言 SOP：它决定子 Skill 的调用顺序，并把上一个 Skill 的完整自然语言结果交给下一个 Skill。

## 可调用的 Skill Packs

- `$daily-planning`：创建、细化或修订日程。
- `$perception-and-memory`：判断重要度、检索与更新记忆。
- `$action-and-space`：把计划落实为地点、对象和世界事件。
- `$social-conversation`：发起、推进、结束并记住对话。
- `$reflection-and-cognition`：从记忆形成高层洞察。

## 公共 MCP

- `world-perceive`：读取地图四层语义、事件、附近 Agent 与 Game Object。
- `memory-stream-search`：检索当前 Agent 的记忆。
- `memory-stream-append`：保存当前 Agent 的文本记忆及可选 SPO 语义。
- `world-act`：选择本轮唯一真实世界动作。

## 每轮 SOP

1. 读取 `IterationContext.now`、步骤、角色状态和当前四层地址。
2. 调用 `world-perceive` 获取当前空间语义与可交互对象；需要历史时调用 `memory-stream-search`。
3. 检查已有排程。新一天、没有排程或发生中断时调用 `$daily-planning`；否则沿用并细化当前计划。
4. 按情境选择其余最少 Skill Pack，并把前一个 Skill 的输出原样交给后一个 Skill。
5. 通过 `world-act` 提交且只提交一个动作；起床、洗漱、吃饭、办公等普通活动使用 `ACT` 并直接写 Event 的 `predicate`、`object`、`description`；只有真实等待时才提交 `WAIT`。
6. 值得长期保留的事实通过 `memory-stream-append` 保存；反思只在情境确实需要时执行。

Scripts 负责确定性计算，MCP 负责公共读写与真实世界变化。Skill 的文本输出只驱动 Agent，不承担回放协议。

## 返回结果

返回本轮决策摘要；真实世界结果以 `world-act` 的执行事实为准。
