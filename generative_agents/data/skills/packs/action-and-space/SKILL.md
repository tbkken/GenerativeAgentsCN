---
name: action-and-space
description: "决定智能体应去往何处、与什么交互，以及该动作在世界中如何呈现。"
example_input: "2026-08-19 09:00，简已经到达咖啡馆，今天上午的排班要开始为客人制作拿铁。请确定简应该去哪个区域、在哪个场所、使用哪个对象，并给出可执行的世界事件。"
---

# Action And Space

## 使用时机

当 Agent 需要把计划落实为地点、对象、等待决策或可展示的世界事件时使用。

## 可调用的 Skills

- `$determine-sector`：选择区域。
- `$determine-arena`：选择区域内部的场所。
- `$determine-object`：选择交互对象。
- `$decide-game-object-interaction`：决定是否主动请求附近 Game Object。
- `$decide-game-object-response`：把 Game Object 的返回作为外部信息并决定等待或继续。
- `$decide-wait-example`：形成等待判断样例。
- `$decide-wait`：决定等待还是继续。
- `$describe-event`：形成可记忆的事件。
- `$describe-object`：更新对象状态。
- `$describe-emoji`：生成界面表情。

## 执行方法

从抽象计划逐步收敛到当前世界允许的行动。每一步把前一步结论和最新世界上下文原样交给下一个 Skill。

## 返回结果

返回一个在当前地图和角色状态下可执行的行动结论。
