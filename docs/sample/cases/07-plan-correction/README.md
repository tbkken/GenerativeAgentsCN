# 公告更新后修正旧计划

## 案例目标

公告更新后修正旧计划。

## 建议规模

2 人，16 步。

## 所需素材

- `map`
- `agents/陈明远`
- `agents/林晨`
- `skill-definitions/passive/object-state-observer`

## 所需 Skill

- `context-driven-simulation-brain`
- `memory-maintenance`
- `object-state-observer`

## 验收要点

- Run 正常结束并生成 StepResult。
- 每个世界变化都有 SPO 与 structured_payload。
- 回放不重新调用模型。
