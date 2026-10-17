---
name: stanford-town-brain
description: "测试多层技能依赖和根 Brain 的动作提交边界。"
example_input: "决定当前迭代的行动。"
---

# Test Town Brain

调用 `@world-perceive` 读取当前事实；需要记忆时使用 `@memory-stream-search`。
根据任务选择 `$daily-planning`、`$perception-and-memory`、`$action-and-space`、
`$social-conversation` 或 `$reflection-and-cognition`，把子技能结果作为后续输入。
需要保留已发生的事实时使用 `@memory-stream-append`，最后通过 `@world-act`
提交本轮唯一动作。成功提交后结束本轮。
