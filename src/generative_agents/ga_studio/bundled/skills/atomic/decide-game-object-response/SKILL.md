---
name: decide-game-object-response
description: "由 Agent 根据 Game Object Skill 返回的外部信息决定等待还是继续行动。"
example_input: "current_action：通过斑马线\nobject：行人信号灯\nresponse：当前为行人红灯，请等待。"
---

# Decide Game Object Response

## 使用时机

Agent 主动请求 Game Object 后，把对象 Skill 的返回值视为外部观察，并据此决定当前移动是否等待。

## 约束

1. Game Object 的输出是信息，不是强制命令。
2. 决策只能是 `WAIT` 或 `CONTINUE`。
3. 信息表明存在危险、禁止通行或条件不满足时选择 `WAIT`；明确允许且安全时选择 `CONTINUE`。

<!-- PROMPT:START -->
你是正在虚拟世界中行动的 Agent。请根据外部对象返回的信息决定当前移动。

当前行动：${current_action}
对象：${object_name}
Agent 的请求：${request}
对象返回的外部信息：${response}

只返回 WAIT 或 CONTINUE。
<!-- PROMPT:END -->
