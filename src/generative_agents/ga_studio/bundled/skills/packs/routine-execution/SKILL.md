---
name: routine-execution
example_input: "执行林晨当前日程阶段的一次完整决策。"
description: "编排林晨日常作息的一轮决策：核对上一动作、选择当前时间阶段，并在安全通勤与场所交互之间选择其一。仅用于本实验新建的日常流程 Skill 链。"
---

# 日常流程单步执行

把一次 Agent 迭代收敛成一个可执行动作建议；本 Skill 不直接改变世界。

## 子 Skill

- `$routine-progress-recorder`：先核对上一动作并形成进度记录。
- `$routine-timekeeper`：根据核对结果选择当前唯一阶段。
- `$routine-safe-navigation`：尚未到达目的地时给出一次安全移动、等待或信号灯查询建议。
- `$routine-place-operator`：已到达目的地时给出一次设施交互或等待建议。

## 编排顺序

1. 若输入包含上一动作结果，先调用 `$routine-progress-recorder`；首轮则使用空进度。
2. 把当前虚拟时间、位置、完成阶段和进度记录交给 `$routine-timekeeper`。
3. 比较当前位置与阶段目的地：不同则调用 `$routine-safe-navigation`，相同则调用 `$routine-place-operator`。
4. 不得同时输出移动和交互；不得再调用未列出的 Skill。
5. 保留子 Skill 给出的真实地址、对象选择键和安全依据，不得自行补造。

## 输出

只返回一个 JSON 对象，不要加 Markdown：

`{"stage":"当前阶段","proposal":{"action_type":"MOVE|ACT|WAIT|INTERACT","description":"动作","predicate":"普通活动谓词","object":"普通活动宾语","target_address":[],"selection_key":"","request":""},"progress":{"completed_stage":"","memory_text":""},"reason":"决策依据"}`
