---
name: decide-game-object-interaction
description: "由 Agent 判断是否要主动请求附近 Game Object 提供信息。"
example_input: "current_action：步行前往街道北侧\ncurrent_location：南侧候行区\ninteractions：query-pedestrian-signal：查询当前行人信号"
---

# Decide Game Object Interaction

## 使用时机

Agent 看见附近可交互的 Game Object 后，判断当前行动是否需要向其中一个对象请求信息。靠近对象本身不构成交互。

## 约束

1. 只能选择给出的 `selection_key`，或者选择 `NONE`。
2. 只有当前行动确实需要该对象的信息时才发起请求。
3. Game Object 不会主动联系 Agent；选择权始终属于 Agent。

<!-- PROMPT:START -->
你是正在虚拟世界中行动的 Agent。判断是否需要主动向附近的 Game Object 请求信息。

当前行动：${current_action}
当前位置：${current_location}
计划路径：${planned_path}
附近可用交互：
${interactions}

如果当前行动需要其中一个对象的信息，返回该对象的 selection_key；否则返回 NONE。
<!-- PROMPT:END -->
