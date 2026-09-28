# 第二章参考资料

正文优先引用官方文档，核查日期为 **2026-09-25**。接口和产品页面可能持续更新；引用说明功能来源，真实可用性仍需按账户、模型、依赖与客户端版本验证。示例没有固定当前最强模型或价格，避免把动态配置写成永恒常量。

## OpenAI API

| 官方资料 | 本章用途 |
| --- | --- |
| [Developer quickstart](https://developers.openai.com/api/docs/quickstart) | 环境、SDK 与首次调用 |
| [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create) | 请求结构、返回状态与工具入口 |
| [Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering) | 指令、上下文、示例与任务组织 |
| [Prompting](https://developers.openai.com/api/docs/guides/prompting) | 提示管理与迭代 |
| [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state) | 手工历史、响应续接、状态边界 |
| [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs) | JSON Schema、严格格式、拒绝与中断 |
| [Function calling](https://developers.openai.com/api/docs/guides/function-calling) | 工具定义、参数、输出项和 `call_id` |
| [Remote MCP tools](https://developers.openai.com/api/docs/guides/tools-remote-mcp) | 远程服务、工具导入、批准与本地隧道 |
| [Shell](https://developers.openai.com/api/docs/guides/tools-shell) | 本地执行与托管环境 |
| [Skills](https://developers.openai.com/api/docs/guides/tools-skills) | 上传、托管挂载、本地路径与版本 |
| [Embeddings](https://developers.openai.com/api/docs/guides/embeddings) | 向量生成与相似度检索 |
| [File search](https://developers.openai.com/api/docs/guides/tools-file-search) | 托管文件检索路线 |
| [Images and vision](https://developers.openai.com/api/docs/guides/images-vision) | 图片输入及其限制 |
| [Audio and voice](https://developers.openai.com/api/docs/guides/audio) | 音频任务与接口路线 |
| [File transcription](https://developers.openai.com/api/docs/guides/speech-to-text) | 录音文件转写 |
| [Computer use](https://developers.openai.com/api/docs/guides/tools-computer-use) | 动作请求、执行与截图回传 |
| [Computer use integration recipes](https://developers.openai.com/api/docs/guides/tools-computer-use-integration) | 运行环境与动作处理 |
| [Working with evals](https://developers.openai.com/api/docs/guides/evals) | 任务、判定标准与样本评估 |
| [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) | 前缀复用、缓存观察与成本分析 |
| [Supervised fine-tuning](https://developers.openai.com/api/docs/guides/supervised-fine-tuning) | 何时考虑训练与独立评价 |

## 协议与编程

- [MCP Architecture](https://modelcontextprotocol.io/docs/learn/architecture)：宿主、客户端、服务端和能力类型。
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)：本章教学服务器所用 SDK。
- [MCP Python SDK 运行说明](https://py.sdk.modelcontextprotocol.io/run/)：stdio 与 HTTP 启动方式。
- [Agent Skills specification](https://agentskills.io/specification)：目录、元数据与渐进读取。
- [Python subprocess](https://docs.python.org/3/library/subprocess.html)：参数列表、工作目录、超时与返回码。
- [JSON Schema](https://json-schema.org/understanding-json-schema)：数据结构约束的基础概念。

## 腾讯 WorkBuddy

| 官方资料 | 本章用途 |
| --- | --- |
| [5.6.2 更新日志](https://www.workbuddy.cn/docs/workbuddy/Changelog-5.6.2) | 产品文档的版本基线，发布于 2026-09-21 |
| [创建任务](https://www.workbuddy.cn/docs/workbuddy/Create-Task) | 工作空间、附件、`@` 引用 |
| [任务对话](https://www.workbuddy.cn/docs/workbuddy/Conversation) | 可见过程与继续任务 |
| [结果查看](https://www.workbuddy.cn/docs/workbuddy/Results) | 文件、变更、产物与预览 |
| [新建任务栏](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Task-Bar) | Ask、Plan、默认 Agent 的入口 |
| [模型配置](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Model) | 自定义模型、URL 与能力配置 |
| [MCP 配置](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide) | MCP 接入与配置文件范围 |
| [技能](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market) | 添加、创建、启用与复用 |
| [记忆](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Memory) | 查看、更正、删除与关闭 |
| [专家](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Expert-Center) | 专家角色与专家团协作 |
| [Agent Browser](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/WorkBuddy-Zero-Cost-Skill-Top-10/Agent-Browser) | 可选的浏览器交互技能 |
| [默认权限与安全沙箱](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Permission-Modes) | 文件、命令、网络与权限设置 |

本章的“计划 Plan”入口来自新建任务栏文档；不能用名称相似的套餐管理页面代替。WorkBuddy 的模型协议支持、工具详情显示和本地依赖均按实际客户端检查。

## Langfuse与可观测性（2026-09-26增补）

本组资料按增补日期单独核查，Python示例采用当前v4 SDK；原章节API与WorkBuddy文档日期不因此自动更新。

| 官方资料 | 本节用途 |
| --- | --- |
| [可观测性概览](https://langfuse.com/docs/observability/overview) | Trace与大模型应用过程 |
| [Trace组织建议](https://langfuse.com/docs/observability/best-practices) | Observation／Trace／Session、稳定名称与输入输出 |
| [开始接入](https://langfuse.com/docs/observability/get-started) | 项目凭据与接收端 |
| [Python v3→v4](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v3-to-v4) | 当前Observation API与上下文属性 |
| [SDK埋点](https://langfuse.com/docs/observability/sdk/instrumentation) | 手工span、装饰器与嵌套关系 |
| [OpenAI Python集成](https://langfuse.com/integrations/model-providers/openai-py) | 自动generation与模型调用记录 |
| [数据集评价入门](https://langfuse.com/docs/evaluation/get-started/offline) | 官方Responses客户端示例；离线评价平台概念 |
| [SDK评分](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk) | 程序校验结果与Trace关联 |
| [评价概念](https://langfuse.com/docs/evaluation/core-concepts) | 规则、人工与模型评分分别管理 |
| [Token与成本](https://langfuse.com/docs/observability/features/token-and-cost-tracking) | 实际上报／推算、模型定义与重叠计数 |
| [SDK高级用法](https://langfuse.com/docs/observability/sdk/advanced-features) | 生命周期、导出与上下文 |
| [脱敏](https://langfuse.com/docs/observability/features/masking) | 导出前处理与旧mask覆盖范围 |

本节没有进行Langfuse远端接收验收、收费模型调用或WorkBuddy客户端接入验收；实际本地与SDK合同检查见[增补核查](langfuse-revision.md)。

## 本章证据的性质

理论讲解、图示与虚构排期用于教学。确定性脚本的本地测试证明的是相应程序行为；模拟 SDK 测试证明的是请求组织和响应处理；它们不测量真实模型能力。远程模型、托管 Shell/Skills、远程 MCP、录音转写以及 WorkBuddy 客户端操作均未在本次写作中执行。

[返回本章](README.md)
