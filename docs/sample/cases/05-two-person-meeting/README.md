# 两个人在阅读角见面

## 案例目标

两个人在阅读角见面。

## 建议规模

2 人，12 步。

## 所需素材

共享源素材位于 [creek-university-v2](../../../images/creek-university-v2/README.md)；此清单仅用于草案，正式实验仍须物理复制完整资源。

- [map](../../../images/creek-university-v2/map/)
- [agents/陈明远](../../../images/creek-university-v2/agents/陈明远/)
- [agents/林晨](../../../images/creek-university-v2/agents/林晨/)

## 所需 Skill

- `context-driven-simulation-brain`
- `social-interaction-decision`

## 验收要点

- Run 正常结束并生成 StepResult。
- 每个世界变化都有 SPO 与 structured_payload。
- 回放不重新调用模型。
