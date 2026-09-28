# 第三章图解索引

本章 14 节共 34 个说明图号：**31 组“上方 Mermaid、下方原图参考（转换前）”双图对照，另有 3 幅独立静态几何图**。正文共有 31 个 Mermaid 块和 34 个 PNG 引用。全部是教学示意或源码合同说明，不是 UI 截图、运行轨迹或模型实测。

编辑转换后的图，请打开下表“当前正文”，找到对应图号之前的 `book-figure` 标记及紧随其后的 Mermaid 代码块。Markdown 代码块是唯一当前 Mermaid 源，不另设 `.mmd` 文件。修改关系、标签与条件直接修改正文；下方 PNG 原图用于对照，和旧 SVG、Python 绘图源一样保留转换前内容，不会自动同步。

| 图 | 当前形式 | 当前编辑或阅读入口 | 静态文件（转换图为历史快照） |
| --- | --- | --- | --- |
| 图3.1-1 信息来源与角色边界 | Mermaid + 原图参考 | [当前正文](../01-case-and-boundaries.md#figure-01-information-sources) | [PNG](01-information-sources.png) / [SVG](01-information-sources.svg) |
| 图3.1-2 观察窗口与活动终点 | Mermaid + 原图参考 | [当前正文](../01-case-and-boundaries.md#figure-01-observation-window) | [PNG](01-observation-window.png) / [SVG](01-observation-window.svg) |
| 图3.2-1 公共资源与当前实验范围 | Mermaid + 原图参考 | [当前正文](../02-studio-and-resources.md#figure-02-editor-scope) | [PNG](02-editor-scope.png) / [SVG](02-editor-scope.svg) |
| 图3.2-2 资源身份、内容与实验位置 | Mermaid + 原图参考 | [当前正文](../02-studio-and-resources.md#figure-02-identity-content-placement) | [PNG](02-identity-content-placement.png) / [SVG](02-identity-content-placement.svg) |
| 图3.3-1 四个 Arena 与设计门洞 | 保留静态几何图 | [当前正文](../03-map-and-space.md#figure-03-semantic-map) | [PNG](03-semantic-map.png) / [SVG](03-semantic-map.svg) |
| 图3.3-2 显示、语义与碰撞层 | Mermaid + 原图参考 | [当前正文](../03-map-and-space.md#figure-03-map-layers) | [PNG](03-map-layers.png) / [SVG](03-map-layers.svg) |
| 图3.3-3 人物出生的页面装配 | Mermaid + 原图参考 | [当前正文](../03-map-and-space.md#figure-03-spawn-selection) | [PNG](03-spawn-selection.png) / [SVG](03-spawn-selection.svg) |
| 图3.4-1 Brain 依赖与对象 Skill 绑定 | Mermaid + 原图参考 | [当前正文](../04-brain-and-skills.md#figure-04-skill-topology) | [PNG](04-skill-topology.png) / [SVG](04-skill-topology.svg) |
| 图3.4-2 条件式 Brain 与动作结束点 | Mermaid + 原图参考 | [当前正文](../04-brain-and-skills.md#figure-04-brain-turn) | [PNG](04-brain-turn.png) / [SVG](04-brain-turn.svg) |
| 图3.5-1 视野与注意力的两个边界 | 保留静态几何图 | [当前正文](../05-perception-navigation-actions.md#figure-05-perception-limits) | [PNG](05-perception-limits.png) / [SVG](05-perception-limits.svg) |
| 图3.5-2 路径预算与实际终点 | 保留静态几何图 | [当前正文](../05-perception-navigation-actions.md#figure-05-movement-budget) | [PNG](05-movement-budget.png) / [SVG](05-movement-budget.svg) |
| 图3.5-3 动作原语与参与者控制权 | Mermaid + 原图参考 | [当前正文](../05-perception-navigation-actions.md#figure-05-action-ownership) | [PNG](05-action-ownership.png) / [SVG](05-action-ownership.svg) |
| 图3.6-1 记忆追加、替代、失效与检索 | Mermaid + 原图参考 | [当前正文](../06-memory-and-progress.md#figure-06-memory-lifecycle) | [PNG](06-memory-lifecycle.png) / [SVG](06-memory-lifecycle.svg) |
| 图3.6-2 跨步进度与动作结束边界 | Mermaid + 原图参考 | [当前正文](../06-memory-and-progress.md#figure-06-pending-confirmation) | [PNG](06-pending-confirmation.png) / [SVG](06-pending-confirmation.svg) |
| 图3.7-1 双人会话与真实回复 | Mermaid + 原图参考 | [当前正文](../07-conversation-and-objects.md#figure-07-bilateral-speech) | [PNG](07-bilateral-speech.png) / [SVG](07-bilateral-speech.svg) |
| 图3.7-2 对象交互与下一轮投递 | Mermaid + 原图参考 | [当前正文](../07-conversation-and-objects.md#figure-07-object-sequence) | [PNG](07-object-sequence.png) / [SVG](07-object-sequence.svg) |
| 图3.7-3 候选安排、公告提交与外观 | Mermaid + 原图参考 | [当前正文](../07-conversation-and-objects.md#figure-07-notice-state-change) | [PNG](07-notice-state-change.png) / [SVG](07-notice-state-change.svg) |
| 图3.8-1 物理副本与封存边界 | Mermaid + 原图参考 | [当前正文](../08-experiment-lifecycle.md#figure-08-experiment-copy-seal) | [PNG](08-experiment-copy-seal.png) / [SVG](08-experiment-copy-seal.svg) |
| 图3.8-2 保存预检与确认执行 | Mermaid + 原图参考 | [当前正文](../08-experiment-lifecycle.md#figure-08-preflight-execution) | [PNG](08-preflight-execution.png) / [SVG](08-preflight-execution.svg) |
| 图3.9-1 执行状态的不同含义 | Mermaid + 原图参考 | [当前正文](../09-running-and-cost.md#figure-09-run-states) | [PNG](09-run-states.png) / [SVG](09-run-states.svg) |
| 图3.9-2 轮次、模型尝试与时间单位 | Mermaid + 原图参考 | [当前正文](../09-running-and-cost.md#figure-09-calls-time-units) | [PNG](09-calls-time-units.png) / [SVG](09-calls-time-units.svg) |
| 图3.10-1 Run 与 Attempt 身份 | Mermaid + 原图参考 | [当前正文](../10-pause-resume-rerun.md#figure-10-run-attempt-identity) | [PNG](10-run-attempt-identity.png) / [SVG](10-run-attempt-identity.svg) |
| 图3.10-2 最新提交与恢复快照边界 | Mermaid + 原图参考 | [当前正文](../10-pause-resume-rerun.md#figure-10-recovery-boundary) | [PNG](10-recovery-boundary.png) / [SVG](10-recovery-boundary.svg) |
| 图3.10-3 取消请求与实际停止 | Mermaid + 原图参考 | [当前正文](../10-pause-resume-rerun.md#figure-10-cancel-request-versus-stop) | [PNG](10-cancel-request-versus-stop.png) / [SVG](10-cancel-request-versus-stop.svg) |
| 图3.11-1 从动作选择到已提交事实 | Mermaid + 原图参考 | [当前正文](../11-replay-and-diagnosis.md#figure-11-commit-chain) | [PNG](11-commit-chain.png) / [SVG](11-commit-chain.svg) |
| 图3.11-2 事实归约与回放隔离 | Mermaid + 原图参考 | [当前正文](../11-replay-and-diagnosis.md#figure-11-replay-reduction) | [PNG](11-replay-reduction.png) / [SVG](11-replay-reduction.svg) |
| 图3.11-3 从观察到最小可定位证据 | Mermaid + 原图参考 | [当前正文](../11-replay-and-diagnosis.md#figure-11-diagnosis-evidence) | [PNG](11-diagnosis-evidence.png) / [SVG](11-diagnosis-evidence.svg) |
| 图3.12-1 三层评价各自的证据 | Mermaid + 原图参考 | [当前正文](../12-quality-and-evaluation.md#figure-12-evaluation-dimensions) | [PNG](12-evaluation-dimensions.png) / [SVG](12-evaluation-dimensions.svg) |
| 图3.12-2 目标人群与指标分母 | Mermaid + 原图参考 | [当前正文](../12-quality-and-evaluation.md#figure-12-metric-denominators) | [PNG](12-metric-denominators.png) / [SVG](12-metric-denominators.svg) |
| 图3.13-1 三种正式包与结果 ZIP | Mermaid + 原图参考 | [当前正文](../13-packages-and-reproduction.md#figure-13-package-contents) | [PNG](13-package-contents.png) / [SVG](13-package-contents.svg) |
| 图3.13-2 迁移与复现的三个验收层次 | Mermaid + 原图参考 | [当前正文](../13-packages-and-reproduction.md#figure-13-handoff-levels) | [PNG](13-handoff-levels.png) / [SVG](13-handoff-levels.svg) |
| 图3.14-1 作者资源、实验与运行事实的交接 | Mermaid + 原图参考 | [当前正文](../14-architecture-and-delivery.md#figure-package-flow) | [PNG](package-flow.png) / [SVG](package-flow.svg) |
| 图3.14-2 对象请求与下一轮反馈 | Mermaid + 原图参考 | [当前正文](../14-architecture-and-delivery.md#figure-step-and-feedback) | [PNG](step-and-feedback.png) / [SVG](step-and-feedback.svg) |
| 图3.14-3 行为自由与内核边界 | Mermaid + 原图参考 | [当前正文](../14-architecture-and-delivery.md#figure-14-behavior-kernel-boundary) | [PNG](14-behavior-kernel-boundary.png) / [SVG](14-behavior-kernel-boundary.svg) |

## 为什么保留三幅静态图

| 图 | 保留原因 | 当前编辑源 |
| --- | --- | --- |
| 图3.3-1 四个 Arena 与设计门洞 | 需要坐标轴、范围、隔断和门洞的几何对应；关系图布局不能代替地图 | [render_visual_revision.py](render_visual_revision.py) 的 `semantic_map` |
| 图3.5-1 视野与注意力的两个边界 | 需要展示请求范围被硬上限包含的半径关系 | 同脚本的 `perception` |
| 图3.5-2 路径预算与实际终点 | 需要格子邻接和已走、剩余路径段；图中仍是人工五格路径 | 同脚本的 `movement_budget` |

地图图省略物件占用，录入仍以 [case/map](../case/map/README.md) 为准；时间与身份图中的 k、A、B 是教学符号。几何图并不证明实际运行已经发生。

## 历史绘图源与记录

- [render_visual_revision.py](render_visual_revision.py)：原 32 幅 PNG/SVG 的绘图源；仅上述 3 幅仍用于当前静态图编辑。
- [render_figures.py](render_figures.py)：原资源交接、对象反馈两图的历史绘图源。
- [visual-manifest.json](visual-manifest.json)、[visual-text-audit.json](visual-text-audit.json)：前次图解改版的历史清单与检查记录，未改写为本次结果。
- 六张 `visual-review-*.png` 为前次审图联系表，不计入正文图数。

绘图脚本只制作资料图，不导入实验、不访问数据库、不调用 Runtime 或模型。对已转换图重跑旧脚本只会再生成历史图形，不会修改正文 Mermaid；保留图重绘仍需安装 Matplotlib、Pillow 与中文字体。

[返回本章](../README.md) · [本次 Mermaid 转换记录](../mermaid-revision.md) · [前次图解改版记录](../visual-revision-2026-09-26.md)
