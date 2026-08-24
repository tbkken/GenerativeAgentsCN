---
name: stanford-town-brain
description: "为每个生命周期事件选择最小相关的认知技能包，来驱动一个斯坦福小镇智能体。"
example_input: "生命周期事件：2026-08-19 07:00，简在公寓里刚刚醒来，今天上午要去咖啡馆工作。请为简选择最合适的技能包并给出当前世界步可以直接执行的计划或行动。"
---

# Stanford Town Brain

## 目标

让 Agent 在虚拟世界中持续感知、回忆、计划、行动、交谈和反思。大脑本身不保存业务编排图，只负责根据当前情境选择最合适的 Skill Pack。

## 可调用的 Skill Packs

- `$daily-planning`：创建、细化或修订日程。
- `$perception-and-memory`：判断重要度、检索与更新记忆。
- `$action-and-space`：把计划落实为地点、对象和世界事件。
- `$social-conversation`：发起、推进、结束并记住对话。
- `$reflection-and-cognition`：从记忆形成高层洞察。

## 决策原则

1. 读取当前生命周期事件和完整角色上下文。
2. 只调用完成当前目标所需的最少 Skill Pack。
3. 把一个 Skill 的结果作为自然语言原样交给下一个 Skill。
4. 确定性计算和持久化交给 Scripts；跨 Skill 公共能力通过 MCP 调用。
5. 已得到可执行行动或明确认知结论时立即返回，不为了走流程而继续调用。

## 返回结果

返回当前世界步可以直接采用的计划、行动、话语、记忆或反思结论。
