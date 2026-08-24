---
name: daily-planning
description: "当新的一天开始、活动需要细化，或突发事件改变日程时，规划、拆解并修订智能体的一天。"
example_input: "现在是早上 7 点，简刚刚醒来，她今天上午要去咖啡馆工作，请为她安排接下来的行动。"
---

# Daily Planning

## 使用时机

当新的一天开始、当前活动需要细化，或突发事件改变已有日程时使用。

## 可调用的 Skills

- `$wake-up`：确定合理的起床时间。
- `$schedule-init`：形成初始日程。
- `$schedule-daily`：补全 24 小时日程。
- `$schedule-decompose`：把当前活动拆成可执行子任务。
- `$schedule-revise`：发生中断后调整剩余日程。

## 执行方法

先判断是创建、细化还是修订日程。把角色状态、虚拟时间和已有计划完整交给最匹配的 Skill。后续 Skill 直接读取上一个 Skill 的自然语言结果，不创建端口映射或业务 JSON 合同。

## 返回结果

返回可以直接用于角色下一步行动的日程结论。
