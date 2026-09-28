# 第二章图解索引

**当前形式：38组“上方Mermaid、下方静态参考”对照＋1幅独立实际输入图片，共39个图号。** 其中35组保留转换前原图，3组Langfuse新增图提供本次导出的PNG/SVG参考。 每个 Mermaid 块前有 `book-figure` 注释，标明旧图片文件名的 stem；按该标记即可找到当前可编辑源。图号、图注及原教学代码保持不变。

Mermaid 源只保存在对应正文，不另建 `.mmd` 副本。原有35个Mermaid块下方标为“原图参考（转换前）”；新增3图标为“静态参考（本次新增图）”，随后保留图注。PNG、SVG 和 Python 脚本仍是旧版快照，便于比较转换前后的表达；它们可能与后续正文不同，不再用作当前编辑入口，也不会自动同步 Mermaid 修改。

## 当前编辑入口

| 图号 | 教学问题 | 当前形式与唯一编辑位置 | 转换前原图参考 |
| --- | --- | --- | --- |
| 2.1-1 | 请求经过的层与故障归属 | [正文 Mermaid](../01-first-call.md) · `01-request-lifecycle` | [旧 PNG](01-request-lifecycle.png) · [旧 SVG](01-request-lifecycle.svg) |
| 2.1-2 | 从任务要求到可验证交付 | [正文 Mermaid](../01-first-call.md) · `technology-map` | [旧 PNG](technology-map.png) · [旧 SVG](technology-map.svg) |
| 2.1-3 | API 与 WorkBuddy 共用材料、分别取证 | [正文 Mermaid](../01-first-call.md) · `01-routes-and-proof` | [旧 PNG](01-routes-and-proof.png) · [旧 SVG](01-routes-and-proof.svg) |
| 2.2-1 | 六问组成任务合同 | [正文 Mermaid](../02-prompt.md) · `02-task-contract` | [旧 PNG](02-task-contract.png) · [旧 SVG](02-task-contract.svg) |
| 2.2-2 | 只改变 Prompt 的对照设计 | [正文 Mermaid](../02-prompt.md) · `02-prompt-comparison` | [旧 PNG](02-prompt-comparison.png) · [旧 SVG](02-prompt-comparison.svg) |
| 2.3-1 | 筛选、压缩与本轮上下文 | [正文 Mermaid](../03-context.md) · `03-context-packing` | [旧 PNG](03-context-packing.png) · [旧 SVG](03-context-packing.svg) |
| 2.3-2 | 手工历史与响应关联两条路线 | [正文 Mermaid](../03-context.md) · `03-conversation-state` | [旧 PNG](03-conversation-state.png) · [旧 SVG](03-conversation-state.svg) |
| 2.3-3 | 新事实使旧校验结论失效 | [正文 Mermaid](../03-context.md) · `03-fact-refresh` | [旧 PNG](03-fact-refresh.png) · [旧 SVG](03-fact-refresh.svg) |
| 2.4-1 | 响应、格式、业务与来源逐层检查 | [正文 Mermaid](../04-structured-outputs.md) · `04-validation-gates` | [旧 PNG](04-validation-gates.png) · [旧 SVG](04-validation-gates.svg) |
| 2.4-2 | 半开时间区间的三个候选 | [正文 Mermaid](../04-structured-outputs.md) · `04-interval-collision` | [旧 PNG](04-interval-collision.png) · [旧 SVG](04-interval-collision.svg) |
| 2.5-1 | 模型请求、应用执行与结果回传 | [正文 Mermaid](../05-function-calling.md) · `05-function-sequence` | [旧 PNG](05-function-sequence.png) · [旧 SVG](05-function-sequence.svg) |
| 2.5-2 | 用 call_id 关联每条工具结果 | [正文 Mermaid](../05-function-calling.md) · `05-function-correlation` | [旧 PNG](05-function-correlation.png) · [旧 SVG](05-function-correlation.svg) |
| 2.5-3 | 执行前校验与无进展阻断 | [正文 Mermaid](../05-function-calling.md) · `05-function-failure` | [旧 PNG](05-function-failure.png) · [旧 SVG](05-function-failure.svg) |
| 2.6-1 | MCP 的宿主、客户端与服务器 | [正文 Mermaid](../06-mcp.md) · `06-mcp-boundaries` | [旧 PNG](06-mcp-boundaries.png) · [旧 SVG](06-mcp-boundaries.svg) |
| 2.6-2 | 本地 stdio 与远端 HTTP 的地址边界 | [正文 Mermaid](../06-mcp.md) · `06-mcp-transports` | [旧 PNG](06-mcp-transports.png) · [旧 SVG](06-mcp-transports.svg) |
| 2.6-3 | 工具范围、审批与服务权限分别核对 | [正文 Mermaid](../06-mcp.md) · `06-mcp-approval` | [旧 PNG](06-mcp-approval.png) · [旧 SVG](06-mcp-approval.svg) |
| 2.7-1 | CLI 输入、环境与结果合同 | [正文 Mermaid](../07-cli-and-shell.md) · `07-cli-contract` | [旧 PNG](07-cli-contract.png) · [旧 SVG](07-cli-contract.svg) |
| 2.7-2 | 受控进程与原生 Shell 的环境区别 | [正文 Mermaid](../07-cli-and-shell.md) · `07-cli-execution` | [旧 PNG](07-cli-execution.png) · [旧 SVG](07-cli-execution.svg) |
| 2.8-1 | 渐进读取方法与辅助文件 | [正文 Mermaid](../08-skills.md) · `08-skill-package` | [旧 PNG](08-skill-package.png) · [旧 SVG](08-skill-package.svg) |
| 2.8-2 | 托管、本地与应用 SOP 的三种装载路线 | [正文 Mermaid](../08-skills.md) · `08-skill-hosting` | [旧 PNG](08-skill-hosting.png) · [旧 SVG](08-skill-hosting.svg) |
| 2.8-3 | 安装、读取、执行与通过分别取证 | [正文 Mermaid](../08-skills.md) · `08-skill-proof` | [旧 PNG](08-skill-proof.png) · [旧 SVG](08-skill-proof.svg) |
| 2.9-1 | 从授权资料到可引用答案的 RAG 通路 | [正文 Mermaid](../09-retrieval-and-rag.md) · `09-rag-pipeline` | [旧 PNG](09-rag-pipeline.png) · [旧 SVG](09-rag-pipeline.svg) |
| 2.9-2 | 检索命中、引用支持与业务通过分别测量 | [正文 Mermaid](../09-retrieval-and-rag.md) · `09-rag-checks` | [旧 PNG](09-rag-checks.png) · [旧 SVG](09-rag-checks.svg) |
| 2.10-1 | 观察、决定、执行与核验 | [正文 Mermaid](../10-agent-loop.md) · `agent-loop` | [旧 PNG](agent-loop.png) · [旧 SVG](agent-loop.svg) |
| 2.10-2 | Agent 的成功、继续与停止条件 | [正文 Mermaid](../10-agent-loop.md) · `10-loop-stop` | [旧 PNG](10-loop-stop.png) · [旧 SVG](10-loop-stop.svg) |
| 2.10-3 | 会话、任务状态与长期记忆的边界 | [正文 Mermaid](../10-agent-loop.md) · `10-memory-layers` | [旧 PNG](10-memory-layers.png) · [旧 SVG](10-memory-layers.svg) |
| 2.11-1 | 青禾教学平面示意：非比例图，不含可导航地图数据 | 保留 PNG 视觉输入 · [正文](../11-multimodal-and-computer-use.md) | [实际 PNG](../examples/data/reference-layout.png) · [矢量源图](../examples/data/reference-layout.svg) |
| 2.11-2 | 图像、语音与截图的证据范围 | [正文 Mermaid](../11-multimodal-and-computer-use.md) · `11-multimodal-evidence` | [旧 PNG](11-multimodal-evidence.png) · [旧 SVG](11-multimodal-evidence.svg) |
| 2.11-3 | 观察、执行和新截图组成闭环 | [正文 Mermaid](../11-multimodal-and-computer-use.md) · `11-computer-loop` | [旧 PNG](11-computer-loop.png) · [旧 SVG](11-computer-loop.svg) |
| 2.12-1 | 两个并行审阅与一次统一汇总 | [正文 Mermaid](../12-multi-agent.md) · `12-multi-agent-forkjoin` | [旧 PNG](12-multi-agent-forkjoin.png) · [旧 SVG](12-multi-agent-forkjoin.svg) |
| 2.12-2 | 保留范围与来源的交接合同 | [正文 Mermaid](../12-multi-agent.md) · `12-handoff-contract` | [旧 PNG](12-handoff-contract.png) · [旧 SVG](12-handoff-contract.svg) |
| 2.13-1 | 离线程序测试与端到端模型评价分层 | [正文 Mermaid](../13-evaluation-and-reliability.md) · `13-eval-levels` | [旧 PNG](13-eval-levels.png) · [旧 SVG](13-eval-levels.svg) |
| 2.13-2 | 从错误产物倒查调用链 | [正文 Mermaid](../13-evaluation-and-reliability.md) · `13-diagnosis-chain` | [旧 PNG](13-diagnosis-chain.png) · [旧 SVG](13-diagnosis-chain.svg) |
| 2.13-3 | 逻辑调用、物理尝试、费用与耗时单位 | [正文 Mermaid](../13-evaluation-and-reliability.md) · `13-cost-units` | [旧 PNG](13-cost-units.png) · [旧 SVG](13-cost-units.svg) |
| 2.13-4 | Trace层次与业务评分关联 | [正文Mermaid](../13-evaluation-and-reliability.md#langfuse-practice) · `13-langfuse-trace-tree` | [本次PNG](13-langfuse-trace-tree.png) · [本次SVG](13-langfuse-trace-tree.svg) |
| 2.13-5 | 业务执行与观测导出的两条路径 | [正文Mermaid](../13-evaluation-and-reliability.md#langfuse-practice) · `13-langfuse-data-flow` | [本次PNG](13-langfuse-data-flow.png) · [本次SVG](13-langfuse-data-flow.svg) |
| 2.13-6 | WorkBuddy执行埋点脚本的观测范围 | [正文Mermaid](../13-evaluation-and-reliability.md#langfuse-practice) · `13-langfuse-workbuddy-scope` | [本次PNG](13-langfuse-workbuddy-scope.png) · [本次SVG](13-langfuse-workbuddy-scope.svg) |
| 2.14-1 | 按任务缺口选择技术 | [正文 Mermaid](../14-integrated-delivery.md) · `14-technology-choice` | [旧 PNG](14-technology-choice.png) · [旧 SVG](14-technology-choice.svg) |
| 2.14-2 | 筹备文件与第三章仿真事实的交接边界 | [正文 Mermaid](../14-integrated-delivery.md) · `14-integrated-delivery` | [旧 PNG](14-integrated-delivery.png) · [旧 SVG](14-integrated-delivery.svg) |

## 编辑与阅读

1. 打开表中对应正文，搜索 `book-figure` 标记。
2. 修改其后的 `mermaid` 围栏代码，保留图号、图注与教学示例。
3. 在支持 Mermaid 的 Markdown 阅读器中检查节点、方向、分组及换行，并与下方转换前原图对照；不支持的阅读器会显示可编辑源码与原图。
4. 需要导出当前图时从正文 Mermaid 渲染；下方旧 PNG 只作转换前参考，不代表后来已修订的当前图。

35 幅转换图采用常见的 `flowchart` 或 `sequenceDiagram`；分组表示能力分类或实现路线，并不意味着固定的内核运行流水线。蓝、绿、橙用于教学区分，不是产品运行状态编码。

## 为什么保留图 2.11-1

[reference-layout.png](../examples/data/reference-layout.png) 是 [multimodal.py](../examples/multimodal.py) 实际读取并提交的视觉输入夹具。图中相对位置、房间标注、遮挡练习都是读图任务的一部分。将正文改成另一个自动布局的 Mermaid，会让可见材料与 Python 示例输入不一致，因此保留原 PNG/SVG；它仍不是按比例绘制的系统地图，不含门洞、碰撞或导航事实。

[render_figures.py](render_figures.py) 保留原三图与该输入图的制作过程；若调整输入图，应同步评估视觉练习与样本意义，不能当作纯排版修改。

## 历史快照

[render_visual_revision.py](render_visual_revision.py) 及其 33 对 PNG/SVG 保留 2026-09-26 静态图解版本。重新执行这些脚本只会重建旧版图片，不会读取或更新正文 Mermaid，也不能验收当前图。

[Mermaid 转换记录](../mermaid-revision.md) · [静态图改版历史](../visual-revision-2026-09-26.md) · [原技术核查](../verification.md) · [返回本章](../README.md)

2026-09-26新增Langfuse图的代码块仍是唯一当前源；PNG/SVG由该块导出，供下方对照，不是系统或Langfuse界面截图。导出与检查见[增补核查](../langfuse-revision.md)。
