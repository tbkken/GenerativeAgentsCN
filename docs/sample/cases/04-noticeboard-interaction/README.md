# 向公告栏查询活动安排

## 案例目标

向公告栏查询活动安排。

## 建议规模

1 人，8 步。

## 所需素材

共享源素材位于 [creek-university-v2](../../../images/creek-university-v2/README.md)；此清单仅用于草案，正式实验仍须物理复制完整资源。

- [map](../../../images/creek-university-v2/map/)
- [agents/陈明远](../../../images/creek-university-v2/agents/陈明远/)
- [skill-definitions/passive/object-state-observer](../../../images/creek-university-v2/skill-definitions/passive/object-state-observer/)

## 所需 Skill

- `context-driven-simulation-brain`
- `object-state-observer`

## 验收要点

- Run 正常结束并生成 StepResult。
- 每个世界变化都有 SPO 与 structured_payload。
- 回放不重新调用模型。
