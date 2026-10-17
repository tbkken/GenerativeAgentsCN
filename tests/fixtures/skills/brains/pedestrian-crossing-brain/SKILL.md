---
name: pedestrian-crossing-brain
description: "测试另一个 Brain 的独立子技能闭包。"
example_input: "判断当前位置可以怎样继续前进。"
---

# Test Crossing Brain

调用 `@world-perceive` 读取真实位置和附近状态；用 `$action-and-space` 提出行动，
需要总结历史时调用 `$reflection-and-cognition`。通过 `@world-act` 提交本轮动作。
