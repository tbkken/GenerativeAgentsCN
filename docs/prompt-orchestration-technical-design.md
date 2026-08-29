# Prompt 流程编排技术设计

> 已废止：本方案与自然语言 Brain Skill 架构冲突，不得实现或恢复。系统不提供固定五流程和可视化工作流编排器；当前设计见 [用户地图与 Skill Brain 仿真架构](capability-composition-platform-design.md) 与 [实验资源组合 UX 与交互边界](experiment-resource-composition-ux.md)。本文仅保留为历史决策记录。

## 1. 边界

Prompt 编排属于实验 Draft Revision。系统固定提供五条可编辑流程：日程与状态、感知与记忆、行动与空间、社交与对话、反思与认知；首期不允许新建或删除顶层流程。流程之间独立保存，任一流程的画布、Prompt 和版本操作不得改变其他流程或其他实验。

画布节点为声明式数据，不执行浏览器输入的任意源码。Script 节点只能选择服务端注册表中的确定性 Function，注册表页面同时展示容器内实现路径和输入输出契约；LLM 节点引用当前实验 Prompt，并固定保存响应 JSON Schema 与 Schema 失败重试策略；If / Else、Switch、Loop、Parallel / Join、Read State、Write State、Subflow、Start、End 由统一 Schema 校验。

## 2. 数据模型

- `experiment_workflows`：某个 Revision 下某条流程的当前画布，唯一键为 `(revision_id, workflow_key)`，保存完整 graph JSON 和规范化 SHA-256。
- `experiment_workflow_versions`：某个实验、某条流程的不可变历史版本，唯一键为 `(experiment_id, workflow_key, version_no)`，同时保存 graph JSON 与该流程全部 LLM 节点所引用的 Prompt 快照。
- 默认流程在实验创建时生成 V1，`is_default=true`，永远不可修改或删除。
- 数据库触发器禁止修改/删除历史版本，也禁止修改/删除已发布 Revision 的流程。
- Alembic head 为 `0009_prompt_workflow_ux`；升级时只把未改动的旧版线性日程草稿迁移为分支图，历史 V1 保持不可变。

## 3. 保存与一键恢复

保存采用 Draft Revision 的 `lock_version` 乐观锁。画布、当前流程 Prompt、definition hash、workflow hash、版本记录和 lock version 在同一个数据库事务中提交。保存成功自动生成下一版本；过期 lock 返回 `REVISION_CONFLICT`，不会产生半版本。

一键恢复不是客户端撤销：服务端在单事务内恢复目标 graph 与 Prompt 快照，清理目标版本中不存在的当前流程专属自定义 Prompt，并立即生成一个新的恢复版本，例如 `V4 · 恢复默认流程`。被恢复的历史版本与其后版本均保留，页面无需再次点击“保存草稿”。恢复版本只能在相同 experiment 和 workflow_key 内使用，跨实验版本 ID 返回 404。

## 4. API

- `GET /api/v1/experiments/{experiment_id}/draft/workflows`
- `GET /api/v1/workflow-functions`
- `GET /api/v1/experiments/{experiment_id}/draft/workflows/{workflow_key}`
- `PUT /api/v1/experiments/{experiment_id}/draft/workflows/{workflow_key}`
- `POST /api/v1/experiments/{experiment_id}/draft/workflows/{workflow_key}/validate`
- `POST /api/v1/experiments/{experiment_id}/draft/workflows/{workflow_key}/versions/{version_id}/restore`

所有时间输出为带 UTC offset 的 ISO-8601，前端再按浏览器时区显示，避免 SQLite naive datetime 被误当成本地时间。

## 5. 发布与运行

发布校验要求五条流程齐全、29 个系统 Prompt 各放置一次、Prompt 引用存在、Script Function 已注册、LLM 输出契约完整、端口类型兼容且所有节点从 Start 可达并能到达 End。发布后的 workflow rows 与 ExperimentDefinition 一起只读。Worker 在模型重试循环内校验节点的 JSON Schema，失败后按节点 `max_attempts` 重试。

首次 Attempt 将完整 workflow bundle 和 `workflow_bundle_hash` 写入不可变 Run manifest。后续 Attempt/Web 重启只验证并复用首次 manifest，不按当前服务时间或代码环境重建。Worker 使用 `WorkflowPromptRepository` 按 `(workflow_key, node_id)` 解析 Prompt；旧 manifest 没有 workflow bundle 时继续使用原 PromptRepository，保证旧 Run 可恢复。

## 6. 前端状态

- 顶部五个流程 Tab 是唯一顶层切换入口。
- Function 管理是 Prompt 编排内的只读服务端注册表视图，不会与五条顶层业务流程混在一起。
- 每条流程维护独立内存草稿与 dirty 状态；拖动节点、编辑端口、修改 Prompt、增删节点都会立刻显示“未保存”。
- 画布提供独立的二维滚动工作区：空白处按住鼠标左键可横向或纵向平移，并支持纵向、横向两种自动布局；端口支持“点击输出、再点击输入”的显式连线，Inspector 可查看并断开与当前节点相关的边。
- Prompt 变量统一显示为 `{变量名.属性路径}`；运行时继续兼容旧的 `${变量名}` 模板。
- 全局“保存草稿”按当前 Draft lock 顺序保存所有已加载的 dirty 流程。
- 版本面板展示当前状态、V1 默认标记、保存版本和恢复版本；点击“一键恢复”即完成持久化。
- 画布坐标属于版本快照，因此错误拖动也可通过恢复版本还原。

## 7. 验收底线

自动化测试必须覆盖默认 V1、五流程 Prompt 放置、保存递增、过期锁、跨实验隔离、发布不可变、历史不可变、自定义 LLM Prompt 的 V1/V2 双向恢复、HTTP 合同、Run manifest 防篡改、旧 manifest 兼容、前端 DOM/JS 语法。真实 Browser 还需覆盖流程 Tab、Prompt 编辑即时 dirty、节点拖动、保存生成版本、默认 V1 一键恢复、画布坐标恢复和时区显示。
