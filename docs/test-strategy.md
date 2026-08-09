# 实验 Web 服务测试策略

> 文档版本：v0.3（最终 E2E/旧导入对抗复验）
> 测试负责人：资深测试 Agent
> 日期：2026-08-08
> 依据：`docs/experiment-web-service-technical-design.md` v1.2

## 1. 测试使命

本测试不是以“页面能打开、按钮能点击”为完成标准，而是主动寻找会破坏实验隔离、结果真实性、并发正确性和故障恢复的反例。任何仅依靠页面约束、Python 先查后写、日志文本猜测或单进程顺序执行才能成立的规则，都视为未被证明。

首期发布必须同时证明四件事：

1. 实验 A 的草稿、发布、运行、恢复、查询和导出不会改变实验 B 的任何事实。
2. 同一台机器的 N 个运行槽不会重复认领、超发、插队或启动同一 Run 的两个 worker。
3. 页面展示的每个数字、路径、消息、记忆状态和模型用量都能追溯到指定 Run 的 frame、trace 或不可变 manifest。
4. 在任意已定义故障点退出后，系统要么恢复到一个已提交边界，要么明确失败；不能继续运行混合分支。

## 2. 质量门槛与严重度

| 严重度 | 定义 | 发布处理 |
| --- | --- | --- |
| P0 | 会跨实验污染、破坏不可变版本、重复运行进程、误杀其他进程、泄露密钥或不可逆损坏结果 | 立即阻断；无规避发布 |
| P1 | 核心能力缺失、计数/恢复错误、状态机死路、结果不可追溯、常见操作不可完成 | 阻断首期发布 |
| P2 | 局部 UX、兼容、性能或诊断缺陷，有明确且安全的临时规避 | 修复或书面接受后发布 |
| P3 | 低影响一致性、文案和非关键易用性问题 | 可排期，但需记录 |

发布门槛：P0/P1 未关闭数为 0；红线测试全绿；隔离与恢复专项全绿；性能硬上限无违例。不能通过删除测试、放宽断言、使用 `xfail` 或把真实失败改为 mock 成功来关闭缺陷。

## 3. 测试分层

### 3.1 旧系统特征测试

目录：`tests/legacy/`

用途是固化重构前的真实行为和已知缺陷证据，包括配置合并、import 副作用、分钟级 checkpoint 命名、零分钟对话、记忆上限 off-by-one、序列化时隐式持久化，以及回放读取当前共享资源。特征测试应保持通过；当旧入口退出兼容期时，经评审后删除或迁入 legacy import 测试。

### 3.2 架构红线测试

目录：`tests/architecture/`

红线检查源代码和技术契约中不允许存在的结构，包括：

- Game/Timer/Model 进程全局单例。
- LlamaIndex 全局 `Settings` 写入。
- runtime 读取 bootstrap config、Prompt、Agent 或地图。
- 使用实验名、Agent 显示名或用户路径构造运行目录。
- import 阶段解析 CLI。
- 领域代码绕过 StepResultBuilder 直接写结果表。
- 模块级随机数和未进入 checkpoint 的 RNG。
- 运行时修改 Revision 输入对象。
- UX 能力没有对应 API 或状态转换。

这些测试在旧基线上预期失败；每个断言必须关联 DEF 编号，修复后转绿。

### 3.3 单元与属性测试

实施后覆盖以下纯逻辑：

- Pydantic 判别联合、边界、未知字段和跨字段约束。
- canonical JSON 的换行、时区、路径、排序、NaN 拒绝和 SHA-256 稳定性。
- Prompt 变量白名单、缺失变量、未知变量及 `$` 转义。
- `ga-cn-v1` profile 快照和未知版本拒绝。
- Run 状态机所有合法边与非法边；幂等 pause/cancel。
- RunPaths 对绝对路径、`..`、设备名、保留名、Unicode 和超长名的拒绝。
- Secret 加密、替换版本、重包、指纹和全链路脱敏。
- 稳定 ID、cursor 签名、筛选哈希和跨 Run 拒绝。
- 活动类型、对话时长、记忆容量、关系边、延迟桶与模型逻辑/物理调用口径。

规范化和 cursor 建议增加属性测试：随机打乱 JSON 键顺序、换行和等价时区，哈希不变；随机篡改 cursor 任一字节必须失败。

### 3.4 数据库约束与事务测试

使用临时磁盘 SQLite，真实执行 Alembic 从空库到 head，并核验 WAL/foreign_keys/busy_timeout。禁止用 `:memory:` 代替并发数据库测试。

必须覆盖：

- PUBLISHED revision UPDATE/DELETE 在数据库层失败。
- 每实验单 DRAFT、单 open Run、每槽单 Run 的 partial unique index。
- 非法 status/slot/current_attempt/pid 组合被 CHECK 或 trigger 拒绝。
- `BEGIN IMMEDIATE` 下多个 claimant 只认领一个 FIFO 行。
- 事务冲突完整 rollback 后才重试，重试不复用失败 Session。
- 级联、外键、分页 total/status_counts 与查询条件一致。
- StepResult 幂等重放不重复累计；frame 未就绪时 `available_step` 不推进。
- FTS、关系、usage、artifact job 的查询必须以 run_id 二次约束。

### 3.5 运行与故障注入测试

建立 `FakeLLM`、`FakeEmbedding`、5x5 可通行地图、2～3 个 Agent 的确定性夹具。FakeLLM 能按调用序号配置成功、schema 失败、超时、连接错误和重试，并返回固定 usage。

在以下边界注入退出：

1. manifest 临时文件写完、rename 前后。
2. checkpoint 某个 storage 文件写完、bundle.json 写入前。
3. checkpoint bundle rename 后、LATEST 改写前后。
4. frame rename 后、SQLite projection commit 前。
5. projection 写到一半触发事务回滚。
6. worker claim 后、Popen 前；Popen 后、worker 注册前。
7. 一个物理模型尝试完成后、LOGICAL_END 之前。
8. artifact job 认领后、输出 rename 前后。
9. Web 正在处理 SSE backlog 时重启。

每个故障点验证：数据库状态、slot、attempt、checkpoint、canonical/orphaned frame、usage cursor、事件序列和下一次恢复动作。重复 reconcile 两次的第二次必须无副作用。

### 3.6 API 与 UX/E2E

API 契约测试直接使用临时数据库与 TestClient/httpx，覆盖成功、422、404、409、500 的统一 error envelope 和 request_id。分页测试至少制造 1,001 个实验、120 个 Run、500 个 Agent/对话/记忆条目，验证首页、末页、空页、cursor 篡改和筛选变化。

Playwright 关键旅程：

1. 首页按状态/搜索/排序/分页定位实验，URL 刷新可恢复。
2. 新建、复制、编辑、保存、冲突重载、校验、模型测试、发布并排队。
3. A/B 同时运行，A 暂停、B 继续；A 恢复到队尾；排队项取消。
4. 从已发布 Revision 再运行；在 PAUSED 状态直接取消。
5. 六个结果 Tab 在 EMPTY、等待首步、PARTIAL、COMPLETE、capability unavailable 下局部呈现。
6. Run 历史超过 50 条时可继续检索/加载；切 Run 中止旧请求、SSE 和播放。
7. 收到 `result_rewound` 后删除越界缓存、收敛 URL step，并且不再显示旧分支数字。
8. artifact job 刷新页面后仍恢复排队/构建/下载状态。

无障碍最低检查：键盘可到达 Tab、弹窗、Run 选择器、分页和 timeline 控件；焦点在弹窗内约束；tooltip 不承载完成任务所必需的信息；状态不只依靠颜色。

## 4. 实验隔离矩阵

并发创建 A/B/C，故意使用相同显示名、相同 Agent 名和相同虚拟时间，但设置不同：

| 维度 | A | B | C |
| --- | --- | --- | --- |
| Prompt 标记 | `PROMPT_A` | `PROMPT_B` | `PROMPT_C` |
| random_seed | 11 | 22 | 33 |
| resolved chat model | fake-a | fake-b | fake-c |
| memory relevance_weight | 1 | 3 | 7 |
| Secret 版本 | S1 | S1 | S2 |

断言对象包括 manifest/hash、clock、RNG 序列、Prompt cache、HTTP model handle、embedding instance、Maze、Agent、logger、worker lock、frame、checkpoint generation、trace、SQLite 投影、SSE cursor、browser cache key 和 artifact 参数。运行期间替换 A 草稿 Secret 为 S3，A 当前 run、B/C、旧 Revision 仍必须引用各自原版本。

## 5. 调度与进程并发

分别以 N=1、2、3 运行至少 5 个不同实验，使用屏障让 claim 竞争同时发生。验证：

- 活跃 slot_no 唯一且数量不超过 N；排队顺序严格按 run_queue.id。
- 同一实验的第二个 open Run 由数据库约束拒绝，不依赖 Web 查询。
- PAUSED 不占槽，resume 进入队尾。
- STARTING cancel 与 worker 注册竞态只产生定义内的终态。
- PID 复用时 create_time 不匹配，force cancel 不发出 kill。
- 降低 N 不杀已有高槽 worker；提高 N 重启后从队首补足。
- Web 重启不杀 worker；双 Web 使用同 var_dir 时第二个因 scheduler lock 失败启动。
- 一个 worker 大量 LLM 重试不能长持 SQLite 事务或阻断其他 worker 心跳。

## 6. 结果正确性判定

每个 Fake run 预先定义事实账本，按稳定 ID 对比，不只比较总数：

- 每步每 Agent 的 from/to/path/action/location/activity 与 StepResult 一致。
- Conversation participant 恰好 2，message 按 sequence_no，未伪造逐条时间。
- Memory 的 CREATED/ACCESSED/EXPIRED/EVICTED 生命周期及 evidence 可追溯。
- Schedule revision 只在内容变化时增加，revision_no 恢复后连续。
- logical call、physical attempt、retry、fallback、token null 口径正确。
- summary、series、relationships、agent summary 是 frame/trace 的确定性投影。
- 页面从不把 completed_steps 冒充 available_step，也不把 artifact state 冒充 result_state。

结果一致性使用三方校验：frame 账本、SQLite API、最终 artifact。新运行的回放 path 必须等于 OBSERVED path；调用 `Maze.find_path` 即失败。任何前端文案中的语义结论若没有版本化观测定义或 source_id，测试将其视为 mock 泄漏。

## 7. 性能与容量

硬数据集：1,000 experiments；每 Run 25 Agent、10,000 steps、100,000 memories、20,000 conversations、5,000 run events、12 MiB 日志。运行三 simulation worker 加一 artifact worker。

| 操作 | 热缓存目标 | 硬约束 |
| --- | --- | --- |
| 实验列表分页 | <300 ms | 不全表加载后在 Python 分页 |
| summary | <200 ms | 单次有界 SQL，不扫描 frames |
| 60-step timeline | <300 ms | 压缩响应 <1 MiB，绝不超过 2 MiB |
| 50 条 conversation/memory | <300 ms | keyset cursor，不使用深 offset |
| 混合并发结果查询 P95 | <500 ms | artifact 不阻塞 SSE heartbeat |
| 任意 200 行日志窗口 | <300 ms | 使用 sidecar seek，不扫描全文件 |

性能失败需保留查询计划、响应大小、数据量、CPU/内存和并发拓扑。不能仅在空库上报告时间。

## 8. 测试执行与证据

基线命令：

```powershell
python -m pytest tests/legacy -q
python -m pytest tests/architecture -q
```

首次 2026-08-08 旧系统基线：legacy `8 passed`；architecture `15 failed`，均已登记到 `docs/defect-log.md`。第二阶段修复后最终独立结果：全量 `91 passed, 1 warning`；新增故障边界专项 `11 passed`。当前解释器为 Python 3.13.9，而目标基线是 Python 3.12；正式依赖、迁移、进程和安装验收仍必须补跑 Windows + Python 3.12，3.13 结果只能作为附加兼容信号。

每次缺陷证据至少保存：commit/worktree 状态、命令、测试名、输入 fixture、期望、实际、日志/trace/frame/DB 摘要和首次发现时间。修复回归由同一 DEF 测试复验；若断言需要变更，必须在缺陷记录说明为什么原验收口径错误。

## 9. 第二阶段对抗复验增量

新增 `tests/architecture/test_adversarial_failure_boundaries.py`，不是对开发单元测试的重复，而是把技术方案最容易产生混合分支的边界直接构造成可执行反例：

- cancel 两阶段升级：先软取消，再 force；durable event 与 supervisor action 不得丢失。
- 强杀 committed boundary：`completed_steps > recoverable_step` 时终态结果可读边界和恢复边界保持分离，finish 不得伪造 checkpoint。
- 活 PID + 陈旧心跳：必须转 INTERRUPTED、释放 slot，不能只凭进程存在判活。
- scheduler leader：同一 var_dir 的第二个 supervisor 启动失败，锁从 start 持有到 stop。
- checkpoint 非原子窗口：DB recoverable 落后于磁盘 latest 时精确选择 DB 授权步，更大 bundle 隔离为 orphan。
- checkpoint 语义损坏：required/undeclared member、目录 step、bundle step、frame hash、run/attempt/time 任一不一致都降序回退。
- 真实恢复装配：state、conversation、RNG 和索引 storage 进入新 attempt；新 attempt 改写 storage 不污染 checkpoint 或未来 attempt。
- 多 tile address：action address 同时映射多个 tile 时，恢复后的首步 `from_coord` 必须严格等于 checkpoint coord，禁止重新随机选 tile。
- 同机竞争：5 个线程并发认领 3 个槽，只能得到 FIFO 前 3 个 Run 和唯一 slot。
- 跨 Run 结果：两个 Run 使用相同 memory ID 时，CREATED/EVICTED 状态、描述、removed_step 和搜索结果仍按 run_id 隔离。
- Run selector：`available_step` 来自 `run_result_summaries`，禁止硬编码空值或用 completed_steps 替代。

首次执行专项暴露 DEF-024/025/026；继续扩展后暴露 DEF-028/029，DEF-027 由源码红线发现。修复后以原断言复跑，不使用 `xfail`、skip 或降低期望。最终证据：

```text
python -m pytest tests/architecture/test_adversarial_failure_boundaries.py -q
11 passed

python -m pytest -q
91 passed, 1 warning
```

尚未完成、不能被上述全绿替代的门槛：Windows + Python 3.12 复跑；真实多进程长时压力；10,000 step/100,000 memory 容量数据；Web 浏览器 E2E/无障碍；kill -9 位于 frame/checkpoint/SQLite 每个原子边界的系统级故障矩阵；真实 LlamaIndex 后端损坏与重建。它们应在发布候选环境执行，当前结果只证明已编码场景。

## 10. 最终 E2E 与旧导入回归增量

新增 `tests/architecture/test_final_e2e_regressions.py`，覆盖从真实 Web 入口、worker 装配到 artifact/legacy import 的跨层闭环。该文件不把原型 DOM 存在视为高保真完成，而要求空库、零步、失败和大 backlog 也保持事实与 schema 正确。

新增门槛：

- worker 必须在重型 import 前建立 heartbeat ownership；运行时 lock 引用不得进入 deepcopy。
- model trace 在每步提交后及 worker 退出时都幂等投影；零步失败不得丢失完整 trace。
- artifact replay 必须从 committed frame 得到 OBSERVED path；有一步真实结果的 job/build/download JSON 三方闭环。
- timeline 在零步与非零步返回相同字段集合；集合为空用 `[]`，不能省略。
- SSE 初始 cursor 必须是真实最新事件而非第一页末尾；至少用 1,001 条 backlog 验证当前终态不回退。
- 生产 `/` 初始文档不得包含真实感 prototype facts、硬编码 badge/实验卡或内联演示 listener；正式 bundle 只加载一次。
- 删除原型 inline script 后，正式 bundle 必须拥有全部 UI 状态与 primitive；使用静态符号所有权检查加真实动态卡点击验证，禁止以恢复演示脚本修复未定义全局。
- 首页 shell 必须是 wheel/package-data 内的运行时资产；在没有仓库 `docs/` 的安装布局中仍可启动并返回 200。
- 非法 asset 走统一 422 envelope；合法 world asset 从上传、hash 引用、Draft 保存到受控内容读取闭环。
- Agent 的 add/patch/delete 必须逐次使用最新 lock_version；从同一 PUBLISHED Revision 可在前一 Run 终止后再次创建隔离 Run。
- legacy import 的 dry-run/apply/重复跳过、`snapshot_complete=false` 与 `legacy-v1` capability 必须一致；checkpoint/compressed 同名制品需保持独立 logical identity。
- legacy RunAttempt 声明的 log_path 必须真实存在；conversation/message 明细与 RunStep、AgentSummary、summary 计数必须一致。
- 复制到 Run 目录的 legacy artifact 必须保持 hash/字节不变，relative_path 受 containment 约束，源目录不得被修改。

浏览器增量结论：根任务后续获得 fresh in-app Browser。DEF-045 已通过动态实验卡、详情/Agent 导航、五个结果 Tab、返回列表和新建 modal 开关旅程，累计 console errors=[]。DEF-036 首轮在 1280×720、devicePixelRatio=1 下的布局/滚动/Save 可见性通过但 Tab 逃逸；焦点生命周期修复后，真实正向 16 次 Tab 全在 modal 内并 wrap，Shift+Tab 反向 wrap 正确，Esc 与保存均关闭、解除 inert、恢复原触发焦点，保存 toast 正确且 errors=[]。CUA `Ctrl+=` 后 inner/visual viewport 未变化，因此 125% zoom 仅有 CSS/DOM 约束证据，不能标记为真实通过。Browser 插件的 CUA/DOM_CUA/locator `press('Enter')` 未触发原生 button 默认 click，Enter 激活不宣称实测通过，也不要求产品为适配器增加重复 handler。所有 modal E2E 必须同时验证首焦点、背景 inert、Tab/Shift+Tab trap、Esc/保存/取消关闭和触发焦点恢复；只测按钮像素位置不得通过无障碍门槛。

阶段性证据：新增最终对抗测试初次执行稳定暴露 DEF-034/038/039/040；扩大零步与 backlog 后暴露 DEF-032/035 的残余边界。已修复项用同一断言复跑，不采用 `xfail`、skip 或把真实值改成 mock 成功。最终命令固定为：

```powershell
python -m pytest tests/architecture/test_final_e2e_regressions.py -q
python -m pytest tests/foundation/test_legacy_import.py -q
python -m pytest -q
```

历史独立结果（2026-08-08，发现 DEF-045 前）：最终 E2E + legacy import 组合 `18 passed, 1 warning`；仓库全量 `118 passed, 1 warning in 46.43s`。此后真实浏览器发现并推动修复 DEF-045 与 DEF-036 键盘焦点生命周期；静态所有权/DOM/焦点红线和上述真实旅程均已通过。最终独立全量结果为 `126 passed, 1 warning in 46.49s`，warning 仍为 Python 3.13 下 Starlette TestClient/httpx 弃用提示；DEF-001～045 全部 `VERIFIED`，无未解决缺陷。125% zoom 与 Enter 原生激活仍按上文记录工具证据边界，不以自动化能力缺口伪造通过。

## 11. 全局状态同步回归增量

DEF-046 的验收不允许用手动刷新替代事件同步，至少覆盖：

- 实验列表停留期间，由另一请求创建 Run，卡片状态、筛选计数、latest Run 和进度在同一文档内更新。
- 当前实验停留在概览/配置页时，顶部状态、动作语义、只读模式和最近运行摘要随 Run 状态更新。
- 结果页区分实验 latest Run 与用户选择的历史 Run；外部新 Run 可进入历史，未主动选择历史 Run 时默认打开 latest。
- 发布后前端重新读取 Experiment/Revision，旧 Draft 不得继续表现为可编辑。
- artifact queued/running/retry/ready/error 均能驱动持久化任务视图；result_rewound 会刷新可读结果边界。
- SSE Last-Event-ID、初始 tail+reconcile、窗口 focus、visibility、online、pageshow 和周期 sync 均可修复断线窗口；慢列表/实验/筛选响应不得覆盖更新状态。

当前独立自动化证据：生产 JS 函数已在同一未刷新 runtime 完成 DRAFT→QUEUED→RUNNING→COMPLETED，列表/筛选/详情/概览/Run selector/history/动作/制品任务同步并拒绝旧 EventSource 迟到事件；动态 API/SSE 用 207 条事件验证 31 条分页无缝重组、消费 73 条后 `Last-Event-ID` 精确续接，以及 tail 后只接收新增 COMPLETED 而不重放 RUNNING。最终 fresh Browser 又在同一 tab、无刷新条件下完成真实 QUEUED 0/10→RUNNING 5/10→COMPLETED 10/10：列表/筛选/概览/selector/动作在 2.2/2.6 秒内收敛，两个自动制品任务均显示 QUEUED 0%，全过程 console warn/warning/error 为空。DEF-046 已转 VERIFIED。

## 12. 运行可观测性与结果生命周期（ROL）覆盖矩阵

本轮依据 `docs/run-observability-lifecycle-product-spec.md` 冻结验收。单个“路由存在”“文件存在”或静态字符串断言只作为入口红线，不代表 ROL 完成；每项必须逐步补齐服务语义、跨层闭环、双 Run 隔离和 fresh Browser 证据。

| 需求 | 自动化入口与主要对抗场景 | DEF | 基线 |
| --- | --- | --- | --- |
| ROL-LOG-001 | byte cursor/UTF-8/物理 bounded I/O；真实 worker child stdout/stderr producer encoding；SSE identity/reconnect/heartbeat；`test_def_056_*` 跨页 record/mid-line/终态无换行；`test_def_059_*` backlog drain；auto-follow | DEF-047/048/055/056/059/068 | VERIFIED：reader/SSE/assembler 与实际 supervisor child 红线均绿；修复后真实 qwen Run 的 4857-byte 日志经正式 HTTP 7-byte/629-page 无损重组，strict UTF-8、file_id、final cursor、terminal/eof 全部一致 |
| ROL-LOG-002 | DB log_path、跨 Run、绝对/`..`/symlink；`test_def_047_artifact_log_stream_is_run_owned_and_terminal` | DEF-047 | VERIFIED：所有权/路径组件/轮转/下载/SSE 自动化已绿，fresh Browser Attempt/job log 与切 Run stale guard 通过；制品真实 symlink 未覆盖项单列 DEF-061 |
| ROL-TRACE-001 | OpenAPI + 真实 ModelTraceWriter/projector；Attempt/purpose/status、重试/延迟、>16 KiB payload byte 分页与脱敏；EOF append、筛选重置、Attempt stale guard、200+ trace/event；零模型调用无文件清理 | DEF-047/048/058/071 | VERIFIED：服务/payload/SSE/零调用清理已绿；fresh Browser 实际点击 PHYSICAL_ATTEMPT/LOGICAL_END 并显示 Qwen/延迟/token/脱敏 payload，切 Run 无 stale |
| ROL-CHK-001 | DB+磁盘四态；`test_def_050_checkpoint_list_separates_pruned_retained_and_recoverable`；`test_def_057_*` 列表 attempt/step/hash/status/error | DEF-050/057 | VERIFIED：四态/完整性服务合同与 fresh Browser Step 2 RECOVERABLE/VALID 列表、Attempt/hash/size/files 均通过 |
| ROL-CHK-002 | 结构化详情/HTTP preview/tamper envelope/单 ZIP；通用 job full validation 与 source 单一权威；Agent/对话/storage/manifest 与 >32 KiB UI 继续加载 | DEF-050/053/057/062 | VERIFIED：source_step/full validation/API/UI 均绿；fresh Browser 显示 Agent/对话/storage/manifest/state JSON，并提供继续加载与选中 Step ZIP |
| ROL-REC-001 | `test_def_049_resume_requires_a_verified_authorized_checkpoint` 加既有 checkpoint round-trip/coord/RNG/storage/future orphan；`test_def_069_*` 跨 Web 重启复用原 manifest 与 immutable 负控；`test_def_070/071_*` 旧 memory 时间与零 trace 清理 | DEF-049/069/070/071，既有 DEF-004/005/013/025/026 | VERIFIED：manifest 重启复用、权威边界、coord/RNG/storage/conversation、旧 wall-clock memory 语义及零调用清理均过；真实 Attempt 4 从 checkpoint 94 进入 Step 95 模型流程且未虚抬边界 |
| ROL-REC-002 | 血缘字段、创建新 Run、原 Run 不变、未实现前无伪入口；后续增加分支闭环 | 待实现审计 | 未覆盖，不虚报 |
| ROL-ART-001 | 统一 V2 builder/validator；artifact/live window/compress 共用；真实 5.8 MiB legacy compressed delta+conversation adapter | DEF-051/060 | VERIFIED：V2/source identity 与真实 legacy carry-forward/时间/对话语义均过 |
| ROL-ART-002 | partial10→final100 冻结/不可变、并发 finish 自动任务；RunArtifact path/size/SHA；Checkpoint step 单权威；DB frame path/SHA 三生产者一致；Windows reparse chain | DEF-050/051/052/061/062/063/072 | PARTIAL：非 symlink 完整性、幂等/source_step/三生产者与真实 NTFS Junction 父/中/叶/跨 Run 8 项冻结复验均绿；Artifact 原生 final-file/intermediate-directory/cross-Run-directory 三节点仍须严格 CI 0 skip |
| ROL-RPL-001 | Replay V2 metadata、Agent/path/对话/事件/memory delta/schedule revision/checkpoint/Attempt boundary；DB verified frame/reparse ownership | DEF-051/063/072 | PARTIAL：schema/事实/非 symlink frame integrity 与真实 Junction manifest/window/builder 冻结复验已绿；Replay 原生 final-file/intermediate-directory/cross-Run-file 三节点仍须严格 CI 0 skip |
| ROL-RPL-002 | 外部播放器、包内 Phaser/tile/sprite/wheel；显式 renderer 与 card-owned canvas；play/step/speed/camera/follow/layers/Inspector；切 Run destroy+abort 但保留 shell canvas；选择/Inspector 所有权；Node module contract | DEF-054/064/065/066/067 | VERIFIED：自动化/真实 wheel 均绿；fresh Browser running→completed→running 均 READY，同 Revision Agent/Inspector 恢复、唯一 card-owned canvas、规范化地图资源可见且 console 全空 |
| ROL-RPL-003 | 真实 10k frame window、limit/next cursor、running 增量、chunk-independent Attempt boundary、legacy 语义、reparse frame | DEF-054/060/063/072 | PARTIAL：window/legacy/Browser Run 切换、非 symlink frame integrity 与 Junction 冻结复验已绿；原生 symlink 七节点 Linux/Windows Required gate 待通过 |
| ROL-SYNC-001 | DEF-046 全局活动流；SSE identity/append/replacement/terminal/backlog；Attempt DOM owner、新 Attempt 保留选择、trace EOF append/stale guard、event 250+ merge；播放器 teardown；跨 Web restart resume | DEF-046/047/048/054/057/058/059/065/067/069/070/071 | VERIFIED：日志/Trace/Checkpoint、播放器、跨重启恢复、同 runtime JS、207-event backlog/断点续接/tail 终态均绿；fresh Browser 同一 tab 无刷新完成 QUEUED→RUNNING→COMPLETED，所有全局表面/动作/制品状态收敛且 console 全空 |

Windows 链接能力审计（2026-08-09）严格区分“不能创建原生 symlink”和“没有可利用的 reparse point”：Developer Mode 注册项未启用，当前 token 不含 `SeCreateSymbolicLinkPrivilege`，file/directory symlink 创建均返回 `UnauthorizedAccessException: Administrator privilege required for this operation`；WSL 无发行版且要求安装，Docker executable 不存在，均不能在不改系统/不提权前提下承担原断言。`fsutil SymlinkEvaluation` 的 local-to-local enabled 只表示可解析，不授予创建权限。与此同时普通用户可真实创建 NTFS Junction，Python 3.13 报告 `is_junction=True/is_symlink=False`；DEF-072 因而以父/中/叶/跨 Run Junction 覆盖 preview/download/manifest/window/builder，并通过“非递归删 link 后 target bytes 不变”验证安全清理。此证据是最强 Windows 替代边界，但不降低 DEF-061/063 的原生 symlink CI 要求。

原生 symlink 发布门禁冻结为七个真实 OS node：Attempt log final-file 一项；Artifact final-file、intermediate-directory、cross-Run-directory 三项；Replay final-file、intermediate-directory、cross-Run-file 三项。每个链接创建后必须 `is_symlink()==True` 且 `is_junction()==False`；Artifact 同时覆盖 preview/download，Replay 同时覆盖 manifest/window/builder 与无 READY，跨 Run 新项还要求 unlink 后目标 bytes 不变、响应不泄物理路径。runner 必须设置 `GA_REQUIRE_NATIVE_SYMLINK_TESTS=1` 并校验 JUnit `tests=7/failures=0/errors=0/skipped=0`；Linux 与 Windows matrix check 都设为 Required。`--allow-unavailable` 只允许本地显式诊断，CI 使用该参数必须失败；Junction、monkeypatch 或普通 pytest skip 不得替代发布门禁。

首次稳定基线：

```text
python -m pytest tests/architecture/test_run_observability_lifecycle_redlines.py -q
15 failed, 1 warning in 6.91s
```

已补充并通过的深层边界：日志 SSE 的 `file_id+byte offset` 续接、文件替换 409 reset、空闲 keepalive 与终态 EOF 关闭；Artifact job SSE/download 跨 Run 隔离；Checkpoint Agent/conversation/storage 摘要、受控 manifest/raw preview、active recovery boundary 缺 bundle 的 INVALID 分类。该阶段尚缺的 UI stale guard、Checkpoint 详情、Replay 状态机、双 Run 隔离、legacy 语义、fresh Browser 与 wheel 资产闭环已在后续轮次补齐；最终仍未覆盖的真实 symlink 只按 DEF-061/063 保留，不把主机权限 skip 计作通过。

第三批 A+B 深层红线首次定向结果：`1 passed, 6 failed`。通过项仅为“tail 首屏从 mid-line 开始时丢弃前缀但保留下一完整行”；失败对应 DEF-056 的长行/终态无换行、DEF-059 backlog 固定 sleep、DEF-048 SSE reset loop、DEF-057 Checkpoint UI 和 DEF-058 trace/event 分页。该批不得被前述服务层绿色替代。

后续以同一断言独立复验，A+B 自动化层已取得以下证据：长 UTF-8 单行、mid-line tail、无换行终态行、非 EOF backlog 连续追赶、SSE reset stop、Checkpoint 完整详情/preview、Attempt DOM owner、新 Attempt 无强制切换、trace >16 KiB/EOF append/筛选重置/Attempt stale guard、250+ event merge 均通过。`-k "def_053 or def_056 or def_057 or def_059 or def_048"` 为 `9 passed`，`-k def_058` 为 `5 passed`。随后 fact-rich fresh Browser 又完成真实 Attempt 日志、Trace 行点击、Checkpoint 详情/preview/ZIP 与制品切 Run旅程，DEF-047/048/050/057/058 据此转 VERIFIED；真实 symlink 未覆盖仍只归 DEF-061/063。

C+D 对抗升级为共享语义 validator，而不是只检查文件存在：

- Replay V2 artifact 与 live window 同时验证 source_kind/revision/definition/world、Agent key/display/sprite、OBSERVED path/action/address、virtual time、partial/source step、conversation/domain event、`memory_deltas`、`schedule_revisions`、checkpoint 和 Attempt boundary。
- partial10 job 创建后 Run 推进100再构建，内容仍锁10；final100 产生第二个不可变 RunArtifact/物理文件，旧下载 hash 不变。顺序与八线程并发 finish 只产生一对自动 REPLAY+REPORT。
- 10k 测试真实写入并抽样读取 frame5001–5100，验证 path/action/time、100-step 上限、next cursor、limit 0/101、unknown Run 404；同 Attempt 的窗口首 step 不得伪标 boundary，新 Attempt 首 step 才为 true；运行中 result_version/available_step 增量扩展。
- Artifact preview/download 对 absolute/`..`/size/SHA/cross Run 进行完整性测试；Checkpoint generic job 对 full validation 与 `checkpoint_step==source_step` 做双红线。
- Replay frame 对 absolute/`..`/cross Run/symlink/内容重压缩/DB SHA 做 manifest+window+artifact 三方验证，防止一个生产者校验、另一个生产者绕过。
- shipped legacy movement fixture 必须逐 delta carry-forward 并恢复 conversation/message，源 hash 不变；不能用人工缩小 fixture 代替。
- 时间探索 UI 合同覆盖 play/pause、前后 step、速度、虚拟时间、自由镜头/跟随/Agent 选择、五种图层、四类时间轴标记、地点/行动/状态/对话/新记忆/日程 inspector；切 Run 必须 destroy player、abort 窗口并拒绝迟到响应。外部 player 还需通过 Node 可构造/方法合同。

当前 C+D 历史证据：`-k "def_051 or def_052"` 为 `7 passed`，证明 V2/source identity/自动任务转绿；包含播放器、legacy 与 artifact 边界的初次组合为 `21 passed, 8 failed, 2 skipped`，失败曾归属 DEF-054/060/062。随后 DEF-060/062 原断言组合 `3 passed`；DEF-063 初次 `5 failed, 2 skipped` 后，共享 verified reader 原断言为 `5 passed, 2 skipped`；DEF-054 正式 player/控制面/Node module/10k/真实 wheel install+HTTP boot 为 `9 passed`。完整 ROL 文件在新增 Browser 红线前为 `71 passed, 5 skipped`，加入 DEF-064/065 后为 `73 passed, 5 skipped`；DEF-066 的 card-owned canvas、精确 layer、规范化 PNG+tilemap 与真实 wheel 动态 HTTP 纳入后为 `74 passed, 5 skipped`。仓库阶段全量为 `209 passed, 5 skipped, 1 warning`。此后 DEF-067 lifecycle 与 DEF-068 真实 child-spawn 组合定向 `2 passed`；fresh Browser running→completed→running 均 READY，同 Revision `resident-001` 与 Inspector 分别恢复 Step 3 `[89,18]`、Step 2 `[88,18]` 事实，唯一 canvas 始终归属 `#resultMap`，规范化地图/纹理可见且 console warn/warning/error 均为空。修复后真实 qwen Run 的 4857-byte Attempt 日志也已通过正式 HTTP 7-byte/629-page 无损重组。该阶段之后曾由 DEF-069 暂停，后续修复与最终全量结果见下一段；`skipped` 始终只代表 symlink 主机权限，不计通过。

最终冻结复验补齐 DEF-069/070/071：跨 Web restart manifest 复用、旧 checkpoint wall-clock memory 精确保留语义、零模型调用无 trace 文件清理组合 `3 passed`；真实 Attempt 4 从 checkpoint 94 以 Step 95 启动，越过旧崩溃点并恢复 32 条乔治 concepts 进入模型流程，提交前 completed/recoverable/available 仍为 94。随后 DEF-046/061/063/072 相邻门禁为 `19 passed, 4 skipped, 71 deselected, 1 warning in 44.93s`，其中 DEF-072 的 8 个真实 Junction 与 DEF-046 两项均绿。新增两个跨 Run 原生 symlink 后，ROL 专项为 `79 passed, 7 skipped, 1 warning in 96.86s`，仓库全量为 `233 passed, 7 skipped, 1 warning in 136.71s`。直接以 required 环境执行冻结七节点得到 `tests=7/failures=7/errors=0/skipped=0`，严格 runner capability 返回 exit 2，证明能力不足不会在门禁中伪通过；本地显式 `--allow-unavailable` 才输出 SKIPPED/exit 0，而 `CI=true --allow-unavailable` 为 exit 2。严格 runner 静态收集精确七节点，foundation runner/workflow 合同 `2 passed`；尚待 Linux/Windows CI 真实执行成功。

系统级隔离补充 `test_rol_system_two_active_runs_isolate_log_checkpoint_artifact_and_replay`：同一 SQLite/var_dir 同时认领两个 Run 到不同 slot，分别写入唯一日志、checkpoint、frame/replay 与派生 Replay Artifact；正向读取均核对 run_id/attempt_id，交叉 Attempt log/Artifact download 均 404，物理 artifact 留在各自 Run root。独立结果 `1 passed`。这证明已编码的双 Run 链路隔离，但不替代三 worker 长时压力、真实 supervisor 子进程、symlink CI 和 Browser 切 Run 旅程。
