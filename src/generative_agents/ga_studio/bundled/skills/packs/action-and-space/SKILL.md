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
- `$decide-wait-example`：形成等待判断样例。
- `$decide-wait`：决定等待还是继续。
- `$describe-event`：形成可记忆的事件。
- `$describe-object`：更新对象状态。
- `$describe-emoji`：生成界面表情。

## 执行方法

先用 `world-perceive` MCP 读取真实四层地址、可通行空间和附近 Game Object，再从抽象计划逐步收敛到当前世界允许的行动。当前 Skill 是候选动作规划器，不拥有世界提交权，也不调用 `world-act`；最终把 `MOVE`、`ACT`、`WAIT`、`SPEAK`、`INTERACT` 或 `SET_OBJECT_STATE` 的一组完整候选参数交回 Brain，由根 Brain 校验后提交。每一步把前一步结论和最新世界上下文原样交给下一个 Skill。

起床、洗漱、吃饭、办公、喝咖啡等普通活动统一建议 `ACT`，由 Skill 直接填写有意义的 `predicate`、`object` 和可选 `description`，例如 `{"action_type":"ACT","predicate":"喝","object":"咖啡","description":"林晨在咖啡水吧喝咖啡"}`。`ACT` 只能发生在 Agent 当前坐标和当前四层地址；目标在别处时，本轮必须建议 `MOVE`，到达后的后续迭代才能建议 `ACT`。不使用活动编码或数据字典；`WAIT` 只表示真实的等待。

## 返回结果

返回候选行动的完整参数和自然语言解释；它只是给 Brain 的建议，不代表世界已经变化。
