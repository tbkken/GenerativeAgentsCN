# 第二章配套练习：开放日筹备助手

本目录是一套独立教学材料和 Python 示例，不连接 GenerativeAgentsCN 的数据库、实验目录或运行接口。机构、活动和预约全部虚构；任何脚本都不负责实际订房、通知或发布。

先运行不需要账号的校验与测试，再选择需要体验的 API 或 WorkBuddy 路线。API 脚本只有显式运行时才发起请求；本书编写过程中没有执行收费模型调用。

## 1. 文件与共同合同

| 入口 | 用途 | 是否调用模型 |
| --- | --- | --- |
| [data/brief.md](data/brief.md)、[rules.md](data/rules.md)、[rooms.csv](data/rooms.csv)、[requests.csv](data/requests.csv)、[availability.json](data/availability.json) | 唯一一套教学事实材料 | 否 |
| [expected-checks.md](data/expected-checks.md) | 人工验收清单 | 否 |
| [fixtures/](fixtures/) | 作者手工编写的有效/无效样本；不是模型测试成绩 | 否 |
| [schemas/schedule.schema.json](schemas/schedule.schema.json) | 排期结构合同 | 否 |
| [validate_schedule.py](validate_schedule.py) | 独立业务校验；可作为 CLI 或函数工具 | 否 |
| [generate_schedule.py](generate_schedule.py) | 每分钟搜索最早可用时间的确定性基线 | 否 |
| [first_response.py](first_response.py) | 首次 Responses 调用 | 是，1 次 |
| [structured_schedule.py](structured_schedule.py) | 严格 Schema 输出，再做独立业务校验 | 是，1 次 |
| [function_calling.py](function_calling.py) | 查询、候选校验、修正与停止的完整工具回路 | 是，最多 8 次模型请求、16 个工具调用请求 |
| [rag.py](rag.py) | 小语料分块、嵌入、余弦排序、带引用回答 | 是，1 次嵌入请求和 1 次回答请求 |
| [mcp_server.py](mcp_server.py) | 暴露相同只读工具与规则资源 | 服务器本身不调用模型 |
| [skills/open-day-planner/](skills/open-day-planner/) | 自包含的 Skill、材料、Schema 和校验脚本 | 取决于加载它的主机 |
| [multimodal.py](multimodal.py) | 图片观察或读者自己准备的音频转写 | 是；需选择对应能力的模型 |
| [multi_agent.py](multi_agent.py) | 两个只读审阅角色与一次汇总 | 是，3 次请求 |
| [langfuse_trace.py](langfuse_trace.py) | 生成、独立校验、保存产物与可观测性对照 | 默认否；显式 `--live` 才发起 1 次模型请求及 Langfuse 遥测 |
| [tests/](tests/) | 业务反例、假模型回路、可选 SDK 合同检查 | 否；HTTP 使用 MockTransport |

活动日期是 `2026-10-17`，时区是 `Asia/Shanghai`，开放窗口是 09:00–12:00。`reading_share` / `craft_workshop` / `exchange` 的人数分别为 24 / 10 / 16，时长为 60 / 60 / 45 分钟，允许房间分别为 `reading` / `craft` / `discussion`。手作室 10:00–10:30 已有预约；所有时间区间为 `[开始, 结束)`。

排期根字段是 `date`、`timezone`、`activities`，活动字段是 `activity_id`、`room_id`、`start`、`end`、`participants`。时间使用 `2026-10-17T09:00:00+08:00` 这种整分钟格式。Schema 限定结构、ID 类型与枚举；容量、时长、窗口、完整性和重叠由业务校验器负责。

## 2. 不需要账号的第一条路线

在 PowerShell 中进入本目录。仓库路径不同则替换首行，后续命令保持当前工作目录为 `examples`。

```powershell
Set-Location 'E:\GenerativeAgentsCN\docs\book\chapter 02\examples'
python -X utf8 validate_schedule.py fixtures/valid-schedule.json --output output/validation-report.json
python -X utf8 validate_schedule.py fixtures/invalid-schedule.json
python -X utf8 generate_schedule.py
python -X utf8 -m unittest discover -s tests -v
```

校验器只使用 Python 3.11+ 标准库。有效 fixture 退出码为 `0`，JSON 报告的 `valid` 为 `true`；无效 fixture 退出码为 `1`，应报告人数不一致、超过容量和预约冲突三项错误；输入文件缺失、无效 JSON 或报告覆盖输入等情况退出码为 `2`，错误写入 stderr。成功和业务失败时 stdout 都是 JSON；`--output` 只是额外保存相同报告。PowerShell 用 `$LASTEXITCODE` 查看最近命令退出码。

确定性基线输出 `output/baseline-schedule.json`，选择手作室 09:00–10:00；有效人工 fixture 选择 10:30–11:30，两者都满足规则。基线是小规模贪心算法，不保证在任意新增约束下找到整体解，也不证明模型能力。

## 3. OpenAI API 路线

创建独立环境，不需要改变主项目依赖。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

下面为会话环境变量占位写法。将模型 ID 换为当前账号可用且支持对应能力的 ID；聊天模型与嵌入、转写模型分别配置。凭据通过当前终端会话或机器的密钥管理机制提供，不保存到教材文件。不要把真实密钥放进版本库、截图或对话内容。

```powershell
$env:OPENAI_API_KEY = '<当前账号的API密钥>'
$env:OPENAI_MODEL = '<支持Responses及本项能力的模型ID>'
$env:OPENAI_EMBEDDING_MODEL = '<嵌入模型ID；只运行RAG时需要>'
$env:OPENAI_TRANSCRIPTION_MODEL = '<转写模型ID；只运行转写时需要>'
```

按需要单独运行一个示例；不要求一次全部执行。

```powershell
.\.venv\Scripts\python.exe -X utf8 first_response.py
.\.venv\Scripts\python.exe -X utf8 structured_schedule.py
.\.venv\Scripts\python.exe -X utf8 function_calling.py --output-dir output/run-01
.\.venv\Scripts\python.exe -X utf8 rag.py '手作体验什么时候可以举办？' --top-k 3
.\.venv\Scripts\python.exe -X utf8 multimodal.py image data/reference-layout.png
.\.venv\Scripts\python.exe -X utf8 multi_agent.py output/run-01/schedule.json --plan output/run-01/plan.md
```

`structured_schedule.py` 保存原响应、`structured-candidate.json` 和 `structured-validation.json`，只有业务检查通过才以成功退出；结构正确不代表排期正确。`function_calling.py` 保存真实 `function-trace.json`，通过门槛后交付 `schedule.json`、`validation-report.json`、`plan.md`。已有成功产物时该脚本拒绝覆盖，请用新的 `--output-dir` 保留不同尝试；失败时保留可用 Trace，不把上次成功结果当成本次结果。

函数回路保留全部 `response.output`，将工具结果按 `call_id` 回传；缺少/重复 ID、未完成响应、拒绝、没有验证成功就结束、超过预算均停止。同一工具与同样参数累计第三次出现时，在执行前记录 `not_executed` 并停止。`get_room_availability` 和 `validate_schedule` 都是只读工具。这里为了让各节可独立运行，初始材料也包含预约快照；这一设计不保证模型一定先查询全部房间。真实在线业务应明确动态事实的唯一查询源。

`rag.py` 按 Markdown 二级标题段、CSV 行（带字段名）、已有预约条目分块，将向量保存在单次进程内。它保存 `rag-retrieval.json` 和原响应，核对引用编号后输出 `rag-answer.md`。top-k 检索不一定召回全部排期事实；相似度不是正确率，引用存在也不证明断言受原文支持。对完整排期仍须运行确定性校验器。

图片练习使用 [reference-layout.png](data/reference-layout.png)，它只表达示意布局，不表达真实房间容量、预约状态或可达性。转写可运行 `multimodal.py transcribe <自己准备的录音文件>`；本目录没有伪造录音或转写结果。浏览器练习用 [review-page.html](data/review-page.html)，页面按钮只在本地展开检查项，不预订、不联网发送数据。

所有示例客户端默认单次 HTTP 超时 45 秒、SDK 重试为 0，便于观察物理请求次数；网络/限流错误不会在后台无限重试。模型、输入大小和输出预算仍会影响收费。API 账号与 WorkBuddy 订阅是不同配置；仅设置一个自定义聊天地址也不能据此推断它兼容所有 Responses 工具类型。

## 4. MCP 与 WorkBuddy 路线

MCP 服务使用官方 Python SDK v2，依赖单独安装。以下命令不会启动模型请求。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
.\.venv\Scripts\python.exe -X utf8 mcp_server.py
```

默认 `stdio` 会等待客户端发送协议消息，终端安静不是启动失败；手工观察后可用 Ctrl+C 停止，由 WorkBuddy 启动自己的进程。日志使用 stderr，stdout 留给协议。

在 WorkBuddy 的“插件 → MCP 服务器 → 配置 MCP”中按客户端说明配置，下面是路径示例，需换成自己机器上实际存在的绝对路径：

```json
{
  "mcpServers": {
    "qinghe-open-day": {
      "command": "E:\\GenerativeAgentsCN\\docs\\book\\chapter 02\\examples\\.venv\\Scripts\\python.exe",
      "args": ["-X", "utf8", "E:\\GenerativeAgentsCN\\docs\\book\\chapter 02\\examples\\mcp_server.py"]
    }
  }
}
```

连接后检查工具列表和真实调用卡片：查询 `craft`、`2026-10-17` 应得到容量 12 与 10:00–10:30 的已有预约；调用校验工具应区分两个 fixture。主机是否读取 `open-day://rules` 资源、是否支持当前协议和是否允许命令执行，要以实际连接和结果为准。本书没有完成 WorkBuddy 客户端操作验收。

需要本机 HTTP 客户端练习时可运行 `python mcp_server.py --transport streamable-http --port 8000`，端点是 `http://127.0.0.1:8000/mcp`。它只绑定本机，没有生产鉴权；OpenAI 云端远程 MCP 不会直接访问读者电脑的这个地址。公开部署或官方支持的隧道路线见第二章正文，不把本地服务自动暴露到网络。

WorkBuddy 原生文件路线则选择 `examples` 作为工作空间，明确提供五份事实材料和 `expected-checks.md`，要求生成草案、执行校验、读取报告，再写说明。模型宣称“完成”不等于命令真的执行；查看真实文件、工具输出和退出码。

## 5. 自包含 Skill

`skills/open-day-planner/` 内的 `references/`、`scripts/`、`assets/` 已物理复制必要内容，因此可以作为独立目录提供给支持技能的主机。脚本通过自身位置找到事实材料，不依赖仓库当前工作目录或系统私有 API。上传整个技能目录/压缩包时排除 `__pycache__`、`.pyc` 和任何个人运行产物。

阅读 [SKILL.md](skills/open-day-planner/SKILL.md) 中的输入、步骤、三轮修正上限、停止规则和检查清单。Skill 本身不授予执行权限；没有 Python/Shell 时不得声称通过程序检查。OpenAI 原生 Skills 与 WorkBuddy 的安装/启用方式见正文，各产品的附件、版本和权限合同分别核对；它不是本系统的实验包。

为避免出现两个不一致版本，离线测试会比较 Skill 副本与教材主材料的字节内容。若修改练习，先同步这两处材料及脚本，再测试；同时检查 `validate_schedule.py` 中的日期/窗口常量和 Schema 的固定 ID，这个小例子并非任意活动配置引擎。

## 6. 已完成与待完成的验证

2026-09-25 在 Python 3.13.9 上运行了 35 项检查：32 项标准库测试（含多模态与多角色扩展）、2 项 OpenAI SDK 2.15.0 的 MockTransport 测试、1 项 MCP SDK 2.2.0 进程内 Client 集成，全部通过。覆盖边界时间、预约冲突、错误类型、缺失/重复 ID、非有限 JSON 数、退出码、独立 Skill、工具回路、引用排序与失败交付边界；SDK 检查使用假模型响应，MCP 查询只读取教学文件。

未安装可选依赖时，对应 SDK 测试会显示 `skipped`，不能把跳过算作通过。该次检查使用基础与 MCP 两份 requirements；新增 Langfuse 可选练习见第 7 节。运行当前测试集合：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

API 真实模型成功率、真实费用/延迟、WorkBuddy UI、远程 MCP 部署以及云端 Skill 执行均需要读者在自己的账户和权限环境下另行验收。本目录没有这些结果，也不承诺所有兼容 API 服务都实现原生 OpenAI 工具合同。

<a id="langfuse"></a>

## 7. Langfuse 可观测性练习

对应[2.13.7 Langfuse 专题](../13-evaluation-and-reliability.md#langfuse-practice)。[langfuse_trace.py](langfuse_trace.py) 复用本目录的材料、Schema 和业务校验器。一次真实尝试只有一次 Responses 请求；不自动修正或重试失败排期。默认路线只读取人工 fixture，没有模型请求，也不导入 Langfuse / OpenAI SDK。

```powershell
# 默认有效人工样本；每次生成独立的 output/langfuse/<随机标识> 目录
python -X utf8 langfuse_trace.py
# 业务失败的人工对照；仍保留候选与报告
python -X utf8 langfuse_trace.py --fixture fixtures/invalid-schedule.json
# --output-dir 指定的目录必须尚不存在，避免混入上次结果
python -X utf8 langfuse_trace.py --output-dir output/langfuse-offline-01
```

真实路线的依赖在 [requirements-langfuse.txt](requirements-langfuse.txt)，不改变主项目依赖。下面的环境变量均为占位示范，应替换为读者选择的账户、项目和部署；OpenAI 还需设置前文的 `OPENAI_API_KEY`、`OPENAI_MODEL`。五项必要变量缺失时，脚本在构造 SDK、创建输出目录和网络请求前停止。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-langfuse.txt
$env:LANGFUSE_PUBLIC_KEY = '<Langfuse项目public key>'
$env:LANGFUSE_SECRET_KEY = '<同一项目secret key>'
$env:LANGFUSE_BASE_URL = '<该项目所在Langfuse部署的完整URL>'
# 本命令会请求真实模型，并向配置的 Langfuse 发送输入输出遥测
.\.venv\Scripts\python.exe -X utf8 langfuse_trace.py --live --output-dir output/langfuse-live-01
```

`--live` 与 `--fixture` 互斥。本例只使用虚构材料；默认 OpenAI 集成会记录模型输入、输出和用量。`store=False` 控制 OpenAI 的响应存储选项，不关闭 Langfuse 遥测；接入自己的资料前应确认采集范围。示例没有实现泛用脱敏器。[Langfuse OpenAI 集成](https://langfuse.com/integrations/model-providers/openai-py)、[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)。

真实追踪的根 span 名为 `qinghe-schedule-attempt`，包含 `load-materials`、`validate-schedule`、`save-artifacts` 三个应用 span，以及 SDK 自动生成的 `generate-open-day-schedule` generation。本地 `phases` 中的 `model-generation` 只是计时记录，不会再创建一个重复 generation。`session_id` 默认使用本次 `example_id`；可用 `--session-id open-day-comparison` 将多次独立尝试归入同一比较分组（1—200 字符），各次仍使用新 trace、新 `example_id` 和新输出目录。`propagate_attributes` 包在根 span 外，根与子 observation 均携带同一 session 和 metadata。传播 metadata 只有字符串 `example_id` 和 `scenario`，标签为 `book-ch02`、`fictional`。`source_sha256` 记录五份材料、Schema、`common.py`、校验器和本脚本的摘要，`sdk_versions` 记录实际使用的 Python / Langfuse / OpenAI 版本；离线模式的两项 SDK 版本为 `null`。这些是教学尝试标识，不是本系统的 Run / Attempt 身份。[Langfuse instrumentation](https://langfuse.com/docs/observability/sdk/instrumentation)、[Python v4 迁移规则](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v3-to-v4)。

| 本次输出 | 生成条件与用途 |
| --- | --- |
| `execution.json` | 输出目录创建后在退出时保存；含模式、`example_id`、`session_id`、`trace_id`、实际 SDK 版本、业务状态、阶段记录、来源与产物 SHA256，以及遥测状态 |
| `model-response.json` | 真实模式收到响应后保存；未完成或拒绝响应也保留，不当作可交付候选 |
| `candidate.json` | 成功解析候选后保存，业务失败时也保留 |
| `validation-report.json` | 确定性业务校验完成后保存 |
| `schedule.json` | 仅当业务校验通过时保存 |

stdout 输出一条 JSON 摘要，包括 `mode`、`business_status`、`trace_id`、`output_dir`、`exit_code`、`telemetry`。离线 `mode=offline_fixture`、`trace_id=null`、模型请求数为 `0`；不要将本地阶段记录当作已上传的 trace。真实模式用 `trace_id` 到对应项目定位，再用 `example_id` 和文件摘要核对本地材料；脚本不额外查询项目 ID，也不生成 `trace_url`。

| 退出码 | 含义 |
| --- | --- |
| `0` | 本次候选通过教材业务规则，并已保存结果文件 |
| `1` | 确定性校验未通过；查候选与报告 |
| `2` | 设置、依赖、响应、解析或文件读写错误；能写出时查 `execution.json`，否则查 stderr |

业务状态分为 `passed`、`failed`、`not_evaluated`，对应本地 `business_score` 的 `1`、`0`、`null`。只有实际完成校验，才调用 `create_score` 写入 `business_valid`（`BOOLEAN`，与真实 `trace_id` 关联）；`null` 不会变成通过。这个分数只说明教材规则校验结果，不说明已订房或已完成系统仿真。

OpenAI 单次超时为 45 秒，SDK 重试为 `0`；Langfuse 的遥测传输有自身处理机制，这个重试值不限制遥测请求数。根 span 结束后，`finally` 中会尝试 `flush()`。`score_call_returned` 与 `flush_returned` 只记录方法是否正常返回；`server_delivery_verified` 始终为 `false`，本脚本没有查询服务端确认入库。遥测异常只记入 `telemetry.errors`，不会把已经测得的业务失败改为通过，也不会将“上传成功”当作业务结果。[Langfuse Python SDK：score、flush 与 trace ID](https://python.reference.langfuse.com/langfuse)。

2026-09-26 新增检查使用 Python 3.13.9、Langfuse 4.15.6 与 OpenAI 2.54.0，依赖安装在临时虚拟环境。11 项模拟行为测试和 1 项真实 SDK 合同测试通过；后者使用 `MockTransport` 和内存 OTel exporter，核对唯一 generation 的父 span、trace ID、根与全部子 span 的 session / metadata、参数透传和重试配置，并禁止 socket 连接。模拟响应中的 Token 数是测试输入，不是实测用量。未安装可选 SDK 时，最后一项明确跳过。本次完整示例回归共 47 项，46 项通过，1 项因临时环境未安装 MCP v2 而跳过；没有把跳过项写成通过。

```powershell
python -X utf8 -m unittest discover -s tests -p test_langfuse.py -v
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_langfuse.py -v
```

本轮没有执行真实模型请求、Langfuse 上传或后台页面验收；真实账号的采集、入库、费用和页面显示需读者另行确认。

相关官方资料：[Responses Function Calling](https://developers.openai.com/api/docs/guides/function-calling)、[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)、[MCP Python SDK v2](https://py.sdk.modelcontextprotocol.io/)、[SDK运行方式](https://py.sdk.modelcontextprotocol.io/run/)。返回[第二章](../README.md)。
