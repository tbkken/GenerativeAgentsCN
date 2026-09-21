---
name: lin-chen-workday-step
example_input: "串联当前工作日阶段判断、行动和事件记录。"
description: "为林晨配置版编排一轮日常作息：先核对上一动作，再选择阶段，并在安全步行和场所交互之间生成唯一动作建议。仅依赖本次新建的四个工作日 Skill。"
---

# 林晨工作日单步编排

本 Skill 只生成一轮动作建议，不直接改变世界。

依次使用以下全新子 Skill：

- `$workday-event-reconcile`：核对上一动作，形成完成阶段和事实记录。
- `$workday-stage-select`：依据时间与核对结果选择当前唯一阶段。
- `$workday-crossing-guide`：尚未到达目的地时生成一次安全通勤建议。
- `$workday-place-interaction`：已经到达目的地时生成一次设施交互建议。

首轮没有上一动作时使用空进度。移动与交互只能二选一；保留子 Skill 的真实地址与对象选择键；不得调用未列出的 Skill。

只返回 JSON：`{"stage":"当前阶段","proposal":{"action_type":"MOVE|ACT|WAIT|INTERACT","description":"动作","predicate":"普通活动谓词","object":"普通活动宾语","target_address":[],"selection_key":"","request":""},"progress":{"completed_stage":"","memory_text":""},"reason":"依据"}`。
