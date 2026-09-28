# 第二章　从对话到智能体：技术、OpenAI API 与 WorkBuddy

青禾社区学习中心的开放日需要一份可以检查的筹备方案。我们从第一次模型调用开始，逐步加入材料、约束、工具和校验，最终交付排期、说明与运行记录。每项技术都回到同一个问题：它解决了任务中的什么缺口，怎样证明它确实起作用？

本章面向能阅读少量 Python 的应用实践者。API 路线帮助理解请求、执行与结果如何连接；腾讯 WorkBuddy 路线帮助体验文件、任务、技能和工具的实际用法。两条路线采用相同教学材料，分别核对能力和结果。

## 怎样读本章的图

先沿箭头看清**谁提供信息、谁决定、谁执行、谁验证**，再读图旁的短解释和对照表，最后执行代码与操作步骤。14 节共39个图号；其中38组采用上方可编辑 Mermaid、下方静态参考的双图对照（35幅转换前原图、3幅Langfuse新增图导出），另有 1 幅独立图片作为多模态练习的实际输入。图是机制示意，不能作为工具已执行或模型已通过的证据。

先看 [2.1 的技术关系图](01-first-call.md)，再按各节问题展开；该正文中的 Mermaid 块是这幅图唯一的当前编辑源。

需要查找某种机制或编辑插图时，使用[图解索引](figures/README.md)。修改相应正文的 `mermaid` 围栏代码即可；下方原图用于对照转换前的表达；原 PNG、SVG 与绘图脚本保留为旧版快照，不再是已转换图的当前编辑入口，也不会随 Mermaid 修改自动同步。代码、参数、操作路径和失败边界仍在对应正文中，图不能替代它们。

## 按顺序阅读

| 节 | 正文 | 本节练习 |
| --- | --- | --- |
| 2.1 | [环境与第一次调用](01-first-call.md) | 配置环境，运行本地检查与首次模型请求 |
| 2.2 | [Prompt：把愿望写成可执行任务](02-prompt.md) | 用相同评分表比较不同任务描述 |
| 2.3 | [上下文工程与跨轮状态](03-context.md) | 管理材料、历史、指令与变化的事实 |
| 2.4 | [结构化输出与业务校验](04-structured-outputs.md) | 生成符合 Schema 的候选并独立检查 |
| 2.5 | [Function Calling](05-function-calling.md) | 完成查询、校验、工具结果回传的闭环 |
| 2.6 | [MCP](06-mcp.md) | 将同一只读能力接入不同宿主 |
| 2.7 | [CLI 与 Shell](07-cli-and-shell.md) | 理解命令、参数、工作目录、退出码和执行边界 |
| 2.8 | [Skill](08-skills.md) | 组织可复用的方法、资料、脚本与停止条件 |
| 2.9 | [Embedding、检索与 RAG](09-retrieval-and-rag.md) | 检索相关证据，分别检查召回与答案支持 |
| 2.10 | [Agent 循环、规划与记忆](10-agent-loop.md) | 根据真实结果继续、修正或停止 |
| 2.11 | [多模态与计算机交互](11-multimodal-and-computer-use.md) | 读取示意图、转写录音、展开教学网页 |
| 2.12 | [多 Agent 协作与工作流](12-multi-agent.md) | 并行审阅与统一汇总，比较收益与代价 |
| 2.13 | [可观测性、评估、可靠性与成本](13-evaluation-and-reliability.md) | 接入Langfuse，关联调用链与评分，再分析失败、耗时和用量 |
| 2.14 | [技术选择与完整交付](14-integrated-delivery.md) | 完成两条实践路线，准备第三章 |

第一次阅读可以先完成 2.1—2.5 的主线，再学习其余扩展。每节提供的短代码用于解释接口，完整脚本以配套目录为准；包含占位模型、远程服务地址或 Skill ID 的示例，需要读者填入真实配置。

## 配套资料

- [示例项目与运行说明](examples/README.md)：依赖、命令、输入输出和离线检查。
- [活动材料](examples/data/brief.md)、[规则](examples/data/rules.md)、[期望检查](examples/data/expected-checks.md)：统一日期、人数、时长与预约条件。
- [自包含 Skill](examples/skills/open-day-planner/SKILL.md)：可阅读、打包与本地验证的操作方法。
- [术语速查](glossary.md)、[参考资料](references.md)、[练习记录模板](practice-record.md)。
- [39 幅图解索引](figures/README.md)：38组Mermaid与静态参考、1幅独立输入图片。
- [Langfuse实操](13-evaluation-and-reliability.md#langfuse-practice)：默认离线检查，显式开启真实调用与观测记录导出。
- [Langfuse增补核查](langfuse-revision.md)：新增官方来源、SDK版本、离线测试及未实测范围。
- [Mermaid 转换记录](mermaid-revision.md)：逐节覆盖、双图对照、保留理由与实际检查。
- [原核查与验证记录](verification.md)：2026-09-25 实际检查及未运行项目。
- [2026-09-26 图解改版记录](visual-revision-2026-09-26.md)：新增图、正文编排、静态检查与范围。

本章官方资料原核查日期为 **2026-09-25**；Langfuse专题另于**2026-09-26**核查当前SDK与官方资料；静态图解编排及后续 Mermaid 转换记录分别保留。本次图形表达调整没有重新验收全部 API 或产品入口。示例使用虚构材料；`fixtures` 中的排期由教材作者编写，模拟 API 响应用于检查程序逻辑。本章没有执行收费模型请求，没有将 WorkBuddy 官方文档核查写成客户端操作验收。读者完成真实调用后，可用练习记录模板补充自己的结果。

本章交付活动筹备文件。第三章仍须在 GenerativeAgentsCN 中完成资源与实验配置，才能获得真实的 Run、StepResult 与回放证据。

[上一章](<../chapter 01/README.md>) · [下一章：在共同世界中行动](<../chapter 03/README.md>) · [返回全书入口](../README.md) · [全书详细大纲](../book-capabilities-and-case-roadmap.md)
