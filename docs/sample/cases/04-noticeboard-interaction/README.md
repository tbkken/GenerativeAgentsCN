# 向公告栏查询活动安排

## 案例目标

向公告栏查询活动安排。

## 建议规模

1 人，8 步。

## 所需素材

- `map`
- `agents/陈明远`
- `skill-definitions/passive/object-state-observer`

## 所需 Skill

- `context-driven-simulation-brain`
- `object-state-observer`

## 验收要点

- Run 正常结束并生成 StepResult。
- 每个世界变化都有 SPO 与 structured_payload。
- 回放不重新调用模型。
