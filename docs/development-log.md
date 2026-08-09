# 开发实施记录

> 记录范围：实验隔离 Web 服务实现、缺陷根因、修复归属和验证证据
> 开始日期：2026-08-08
> 规则：只记录实际落地能力；未通过测试 Agent 复验的缺陷不标记关闭。

## 2026-08-08 — 第一阶段：配置、持久化和实验草稿纵向切片

### 已实现

1. 新增可导入的 `generative_agents` 包，以及 `config`、`persistence`、`services`、`web` 分层骨架。
2. 实现严格 Pydantic v2 `ExperimentDefinition`：所有对象 `extra="forbid"`；覆盖 experiment、engine、simulation、results、models、behavior、world、agents、prompts。
3. Chat 与 Embedding 使用 `provider` 判别联合。OpenAI/Ollama/Hugging Face 禁止 `model=auto`；vLLM/openai-compatible 允许 auto，但发布时必须已有 `resolved_model`。
4. 实现跨字段约束：带时区起始时间、步骤与 checkpoint 边界、结果投影边界、有符号 64 位 seed、memory 权重、唯一 agent_key、安全资源相对路径等。
5. 固化 `ga-cn-v1` 只读算法 profile，并提供快照测试。算法版本参与定义哈希。
6. 实现 NFC、换行和 JSON key 规范化的 canonical JSON 与 SHA-256 定义哈希。`None` 显式进入快照，避免相同 Schema 在不同调用点产生不同 hash。
7. 将草稿可保存性与发布完整性分离。草稿可以暂时缺 Agent/世界/Prompt/模型解析；发布校验会返回结构化 errors/warnings 并阻止不完整定义。
8. 实现 SQLAlchemy 2 同步数据库层；每个 SQLite 连接强制 `foreign_keys=ON`、WAL、`synchronous=NORMAL`、`busy_timeout=5000`；worker 可使用 `NullPool`。
9. 建立显式 Alembic `0001_core` 基线，包括 Experiment、Revision、Run、Queue、Attempt、Event、Secret、Asset、LegacyImport、RunArtifact、ArtifactJob。
10. 数据库直接强制关键不变量：单实验单草稿、活跃 Run 唯一、slot 唯一、active artifact job 唯一、队列只允许 QUEUED Run、Run 只允许绑定 PUBLISHED revision。
11. 增加数据库 trigger，已发布 Revision 不能 UPDATE/DELETE。发布由 DRAFT→PUBLISHED 单向完成；继续编辑只能 fork 新草稿。
12. 实现 `ExperimentService`：创建、服务端分页/搜索/排序、读取草稿、乐观锁保存、分区保存、校验、发布、版本历史和 fork。
13. `publish_draft_in_session()` 对外提供事务组合点，运行服务可在同一个事务内完成发布、创建 QUEUED Run 和 run_queue 入队，避免“版本已发布但运行未入队”的半完成状态。
14. 实现最小 `/api/v1` FastAPI：实验创建/列表/详情、草稿读取/替换/分区更新/校验、版本列表/详情/fork、live/ready 健康检查；统一错误 envelope 和 request ID。
15. 新增 foundation 单元/数据库/API 测试。真实临时 SQLite 文件执行 Alembic，不用内存数据库替代 WAL 行为。

### 验证证据

```text
python -m pytest tests/foundation -q
21 passed, 1 warning in 2.64s

Alembic programmatic check:
No new upgrade operations detected.
```

警告来自 FastAPI 0.141.1/Starlette 对旧 `TestClient` httpx 适配层的弃用提示，不影响接口测试结果；后续 E2E 迁移到独立 ASGI transport/Playwright 后移除。

当前开发机为 Python 3.13.9，方案基线仍是 Python 3.12。第一阶段在 3.13 通过不能替代 Windows + Python 3.12 CI 安装、迁移与测试门槛。

### 第一阶段已知技术债

- `BUILTIN_DEFAULT` 已设计为可注入的 catalog factory；在 bootstrap catalog 导入服务完成前，默认 factory 生成显式空白草稿。它不会在运行时读取共享 `data/`，但还不能直接生成可发布的内置 AI 小镇实验。
- 第一阶段没有开放单独“发布”HTTP 接口，因为正式用户流程要求发布与创建 Run 同事务。当前 `publish_draft()` 仅供集成测试；运行服务应调用 `publish_draft_in_session()`。
- `assets` 已有 ORM 和资源引用 Schema，上传、内容寻址落盘、同源读取与 ETag 属于 DEF-023 后续切片。
- Secret 只有核心 ORM 与引用存在性校验；加解密、replacement API 和脱敏由安全切片实现。
- 世界坐标可通行性、Prompt 变量白名单、模型网络最小调用属于完整发布校验后续适配器；当前已完成无需外部 IO 的确定性校验。

## 缺陷根因与修复归属

以下状态以 `docs/defect-log.md` 为准；本表只记录开发分析和责任边界。第一阶段的基础能力不代表运行时红线已关闭。

| 缺陷 | 根因确认 | 修复归属/第一阶段影响 |
| --- | --- | --- |
| DEF-001 | 旧 CLI 以单实验进程为前提，用 service locator 查 Game/Timer | Runtime/旧引擎适配；第一阶段不改用户脏源码 |
| DEF-002 | LlamaIndex 便捷全局 Settings 被当成实例配置 | Runtime + storage adapter；配置 Schema 已提供每 Revision 独立 embedding 定义 |
| DEF-003 | 显示名称同时承担存储主键 | Runtime `RunPaths`；ORM Run 只保存受控 `run_dir`，实验 key/name 不作为目录输入 |
| DEF-004 | 虚拟分钟错误承担提交序号 | Runtime checkpoint；Schema 已禁止 Web stride=0 |
| DEF-005 | `to_dict()` 同时做序列化和 IO | Runtime StepCommitter/CheckpointWriter；Persistence 保留短事务边界 |
| DEF-006 | bootstrap 目录被当成实时配置 | Catalog + runtime manifest；Revision 已保存完整 JSON，服务层不读 bootstrap |
| DEF-007 | 脚本顶层解析宿主 argv | CLI/旧入口适配；新 web/config/persistence import 无命令行副作用 |
| DEF-008 | 模拟循环只保留 plan，没有结果 collector | Runtime result DTO/投影；Run/Artifact 核心表已建立 |
| DEF-009 | `memory[:max_memory - 1]` off-by-one | 旧引擎 memory 适配；Schema 已定义 `-1` 或正整数 |
| DEF-010 | `int(chars/240)` 向下取整且无下界 | Runtime algorithm adapter；`ga-cn-v1.chat_chars_per_minute=240` 已锁定 |
| DEF-011 | 模型统计是进程内调试信息 | Runtime recorder/trace projector；结果表由后续 migration 扩展 |
| DEF-012 | Definition JSON 与 runtime state 共用可变字典 | 旧引擎适配；Pydantic 快照、深复制 fork 和 canonical hash 已提供检测基础 |
| DEF-013 | 使用模块 random，checkpoint 未保存 RNG state | Runtime `RandomSource` + checkpoint |
| DEF-014 | 制品缺真实 path，只能读当前地图重算 | StepResult + ArtifactService |
| DEF-015 | 旧 Flask 把 name 当可信路径且 debug 启动 | Web/replay 兼容适配；新 API 只暴露数据库 ID |
| DEF-016 | logger name/handler 与领域控制流混合 | Runtime logging adapter |
| DEF-017 | 固定 sleep/print 重试未进入 Run context | Model/storage adapter + runtime control；Schema 已版本化 timeout/retry/backoff |
| DEF-018 | API 只设计 publish-and-run，遗漏同 Revision 复跑 | Run service/API；第一阶段可读取任意 PUBLISHED revision，后续直接创建新 Run |
| DEF-019 | `available_step` 事实边界与 `recoverable_step` 恢复边界未明确分工 | Runtime control/reconcile + 技术方案修订 |
| DEF-020 | cancel 只考虑有 worker 的状态 | Run 状态机；PAUSED→CANCELLED 应为无 attempt 短事务 |
| DEF-021 | UI 简化时隐藏异常终态 | Web 高保真；实验 API 已支持 FAILED/CANCELLED 筛选和 counts |
| DEF-022 | 原生 select 没有承载 cursor 历史 | 结果 UI + Run 分页 API |
| DEF-023 | 只设计 Asset 表，遗漏上传/读取闭环 | Asset service/API；第一阶段完成不可变 hash 引用和 ORM 基础 |

## 变更保护

第一阶段未修改或回滚以下用户已有脏文件：`README.md`、`README_en.md`、`data/config.json`、`modules/model/llm_model.py`、`modules/storage/index.py`，也未恢复用户删除的根 `requirements.txt`。Web 依赖只追加到现有 `generative_agents/requirements.txt`，开发测试依赖放在同目录独立文件。

## 2026-08-08 — 第二阶段：DEF-001～DEF-015 旧引擎运行隔离

### 运行时装配和隔离

1. `Game`、`Agent`、`Scratch`、`Maze`、`Action`、`Schedule`、`SpatialMemory`、`Associate`、日志器改为显式接收 Run 级 clock、random、logger、prompt repository 和 model bundle；移除 `GenerativeAgentsMap`、`get_timer()` 及模块级 random 依赖。
2. `start.py` 变为无 import 副作用的 `main(argv=None)`，由 `RunPaths`、已校验 manifest 和 `SimulationContext` 装配单次 Run；模拟循环对每个 agent 捕获真实 outcome、移动 path、会话、记忆、日程和领域事件，再由 `StepCommitter` 提交。
3. 新增纯函数式 `ConfigAdapter`，只把不可变的 `ExperimentDefinition` 快照转换为旧引擎需要的内存对象，不读取 `data/config.json`、agent 名称目录或共享 bootstrap 目录。
4. `Game.snapshot_state()` 保存 Run 自有 RNG 状态，`restore_runtime_state()` 在 JSON 往返后恢复 tuple 结构，保证断点续跑的随机序列一致。
5. `Maze`、`Action.from_dict()` 和 `ConfigAdapter` 对输入深拷贝，禁止把运行态字段写回 Revision 快照。

### 模型、记忆和结果事实

1. `LlamaIndex` 不再读写 `llama_index.core.Settings`；每个实例显式持有 `embed_model` 和 `transformations`。用户已有的 vLLM 与 OpenAI-compatible 支持被保留，并增加 resolved embedding metadata。
2. `LLMModel` 将每次物理尝试和一次逻辑结束写入 `ModelTraceWriter`，记录状态、延迟和可获得的 token usage；默认不保存 prompt/response 正文和 Secret。
3. `Associate.to_dict()` 保持纯序列化，存储写入通过显式 exporter 完成；容量算法改为精确保留 `max_memory` 条，并把淘汰 ID 作为 `EVICTED` 结果事实交给 collector。
4. 对话时长固定为 `max(1, ceil(chars / 240))`，使用真实消息内容且不伪造逐条消息时间。
5. `compress.py` 和 `replay.py` 只接收受控 `RunPaths`、已校验 manifest 与不可变 frame；正式制品只允许 `OBSERVED` 路径，插值样本标为 `DERIVED`，不再按实验显示名访问文件或从当前地图重算路径。

### 用户未提交改动保护

第二阶段任务明确要求修改 `modules/model/llm_model.py` 和 `modules/storage/index.py`。修改采用增量适配方式，保留了用户已有的 vLLM/OpenAI-compatible provider、base URL、模型解析和文档内容；未修改或回滚 `README.md`、`README_en.md`、`data/config.json`，也未恢复用户删除的根 `requirements.txt`。共享 `generative_agents/runtime` 的生产投影器、checkpoint 和 manifest 实现由根 Agent 维护；发生文件所有权冲突后，本 Agent 停止修改这些文件，旧引擎只依赖其公开接口。

### 测试与缺陷状态

新增真实行为测试覆盖：Run 间 clock/RNG 交错隔离、RNG checkpoint JSON 往返、Revision 输入不变、完整 observed StepResult、显式 embedding 实例、模型重试 trace 和受控 replay 制品。旧 characterization 中用于证明缺陷存在的反向断言已替换为修复后的正向行为断言；架构红线未弱化。

开发侧不会自行标记 `VERIFIED`。最终 worker 恢复路径已从数据库 `recoverable_step` 选择匹配的已校验 checkpoint，把 state、conversation 与索引 storage 复制到新 attempt 的独占可写目录，再通过旧入口的 `checkpoint_state`、`checkpoint_conversation`、`storage_root` 接口恢复 Agent、clock/RNG 和 collector 起点。DEF-001～015 为 `READY_FOR_RETEST`，独立测试 Agent 的回归结果和证据继续记录在 `docs/defect-log.md`。

```text
python -m pytest tests/architecture tests/legacy -q
18 passed

python -m pytest tests/legacy/test_engine_isolation_regression.py -q
9 passed

python -m pytest tests/legacy tests/architecture tests/foundation tests/runtime -q
89 passed, 1 warning in 36.67s
```

最终 warning 仍为 FastAPI/Starlette `TestClient` 的 httpx 兼容层弃用提示，不是功能或隔离失败。

## 2026-08-08 — 最终集成：同机并发、六结果视图、真实模型与迁移

### 运行、恢复与制品

1. 实现固定单 Web worker + 一 Run 一子进程的本机调度：SQLite 事务分配唯一 slot，FIFO 队列、leader lock、PID/create_time 对账、心跳、暂停/继续、软取消/强制取消和孤儿恢复均有持久状态。
2. `manifest.json` 由不可变 PUBLISHED Revision 物化；每个 attempt 拥有独立日志、trace、storage 和恢复工作区。Frame 先原子落盘，checkpoint 再提交，数据库最后推进 `available_step/recoverable_step`。
3. 修复 Windows 冷启动边界：`runtime.worker` 导入从 40.36 秒降到 2.329 秒；先建立 ownership 心跳再延迟加载 LlamaIndex/模型/旧引擎。
4. 实现持久 ArtifactJob scheduler 与 `BUILD_REPLAY`、`RESULT_BUNDLE`、筛选记忆/对话、checkpoint bundle；回放只读不可变 Frame 中的实际 path，不再读取不存在的投影字段或当前地图重算。

### Web 工作台

1. 首页为真实实验列表，支持服务端搜索、状态 Tab、页码翻页与异常终态筛选；移除当前无意义的项目/Worker 切换和重复指标。
2. 实验内覆盖概览、Agent CRUD、模型连接测试、29 Prompt 独立副本、完整世界 JSON、内容寻址资源上传、行为参数、发布/复跑/暂停/继续/取消。
3. 结果工作台覆盖总览、时间探索、Agent、对话、记忆、运行与制品六视图；运行历史使用 cursor 继续加载；筛选请求发到服务端，筛选导出创建持久制品任务。
4. 删除结果页加载前的演示数字和假事件。0 步中断、空对话、无 Agent、SSE 断线均使用紧凑空态；说明文字收进 `?` tooltip。Agent 编辑弹窗限制视口高度并保持操作栏可达。

### 旧目录与目录版本

1. Alembic `0003_builtin_catalog` 增加不可变内置目录快照。`python -m generative_agents.cli.import_legacy bootstrap-catalog --apply` 保存 124 个源文件的哈希清单；相同指纹重复执行跳过。
2. `runs --dry-run/--apply` 按单源事务导入旧 checkpoint/compressed；原目录不修改，源制品复制到受控 Run 目录，Revision 标记 `snapshot_complete=false`，查询能力使用 `legacy-v1` audit。
3. 当前仓库真实旧样本演练：`example` 导入 121 个重建步骤与 2 个源制品，`sim-test` 导入 10 个 checkpoint 步骤与 11 个源制品；第二次 apply 为 2 skipped / 0 duplicate / 0 failed。

### 真实模型 E2E 证据

- 对话服务：`F:\qwen3.6-windows-server\start.bat --snapshot start_mtp4`，解析模型 `qwen3.6-27b-autoround`，服务地址 `http://127.0.0.1:5001/v1`。
- Embedding：`F:\qwen3-embedding-cpu-server\start.bat -Background`，解析模型 `qwen3-embedding-0.6b`，服务地址 `http://127.0.0.1:5002/v1`；官方 `test.bat` 通过，3 vectors / 1024 dimensions。
- 已发布 Revision `1b75a74f-75d7-41b4-879d-f37f92a9b102` 复跑为 Run `2b9e56ff-e3be-436c-ac4a-4a01420dd407`：真实完成 1/1 步，9 次逻辑模型调用、1 条记忆、1 个 observed 行动，Frame/checkpoint/storage/log/trace 全部存在。
- 真实制品：Replay、Result bundle、Filtered memories 均为 SUCCEEDED；Replay 含 4 个实际路径坐标，结果包含 summary、manifest 与压缩 Frame。

### 最终开发侧验证

```text
python -m pytest tests/legacy tests/architecture tests/foundation tests/runtime -q
101 passed, 1 warning in 40.85s

python -m generative_agents.cli.import_legacy runs --apply ...
first: created=2, failed=0
second: created=0, skipped=2, failed=0
```

该记录是开发证据，不替代资深测试 Agent 在 `defect-log.md` 中给出的 VERIFIED 结论。尚未在本机执行 Python 3.12 CI、超长时真实多进程容量测试和完整系统级 kill 矩阵，不能把当前 3.13 结果外推为那些环境已经验收。

### 最终边界加固补充

1. Web 增加 `/api/v1/health`，实际进程会执行数据库 `SELECT 1` 后才返回 `{"status":"ok"}`。
2. Asset 内容校验异常统一转换为 `422 INVALID_ASSET`，不再让不支持的 MIME 或畸形内容泄漏为 500；真实接口已验证 Markdown 被拒绝、合法 JSON 被内容寻址登记并关联到实验 Draft。
3. 旧导入源制品的逻辑名增加 `checkpoints/`、`compressed/` 前缀，避免两棵旧目录存在同名文件时触发 RunArtifact 唯一键冲突。
4. 旧导入 attempt 的结构化日志真实写入受控 Run 目录；已导入对话的 Step/Agent conversation/message 计数按事实回填，不再出现详情有消息但摘要为零。

```text
python -m pytest tests/legacy tests/architecture tests/foundation tests/runtime -q
102 passed, 1 warning in 40.68s
```

## 2026-08-08 — 最终回归 P1 加固：DEF-032/034/035/038～040/044

1. Worker 在 `finally`、`finish_worker` 之前对当前 attempt 的模型 JSONL 执行幂等最终投影；首步提交前失败或零步退出也不会丢失完整的 PHYSICAL_ATTEMPT/LOGICAL_END。
2. Timeline 空范围返回与非空路径相同的 collection schema，显式包含 `requested_steps` 与空 `agent_steps`。
3. 结果页先读取当前事实状态，再把 RunEvent 历史分页推进到末游标后建立 SSE；超过 500 条的旧 RUNNING backlog 不再覆盖当前终态。
4. 生产首页不再使用仓库 `docs/experiment-console.html`。新增包内 `web/static/experiment-console.html` 中性高保真 shell，物理移除实验卡、运行历史、地图角色、Agent/对话/记忆示例结果与内联原型脚本；所有 API-owned 容器只保留中性空态。
5. `snapshot.png` 同步进入 Web 静态包并通过 `/static/console/snapshot.png` 提供；删除运行时 `docs/resources` mount，最小安装包不再依赖仓库文档目录。

```text
python -m pytest tests/architecture/test_final_e2e_regressions.py -k "def_032 or def_034 or def_035 or def_038 or def_039 or def_040 or def_044" -q
9 passed, 7 deselected, 1 warning in 30.64s

python -m pytest tests/foundation/test_web_api.py tests/foundation/test_result_projection.py tests/architecture/test_final_e2e_regressions.py -q
24 passed, 1 warning in 34.57s
```

warning 仍是 Starlette `TestClient` 的 httpx 兼容层弃用提示。上述生产文件编辑已结束，等待根 Agent 重启真实 Web 做双实验并发与浏览器回归。

## 2026-08-08 — DEF-045 正式控制台运行时收口

1. 根因不是两个偶发 `ReferenceError`，而是 `console-api.js` 长期作为原型页的增量 adapter，借用了原型 inline script 的状态、Toast、导航、脏状态、弹窗和向导监听；DEF-040 删除 inline prototype 后，这条隐藏依赖链整体暴露。
2. 正式 bundle 现在自持单一 `state`，并显式实现 Toast、页面导航、只读模式、状态 pill、dirty/leave、结果 Tab、发布弹窗、新建向导、模板选择、context menu、历史 Run 搜索和 modal 生命周期。动态实验卡的成功路径与 Promise 错误路径均只进入正式 listener；没有恢复任何原型业务脚本或假状态。
3. 发布确认弹窗不再预填假 Revision、模型、世界和哈希；所有字段在打开时由当前 Draft 定义生成，哈希明确显示为发布事务生成。
4. 第二个真实浏览器断点来自 renderer 顺序：中性 shell 已把 `overviewAgentStrip` 清成空态，代码却先访问其已不存在的子 ID，再通过 parent `innerHTML` 重建。现已删除前置子节点访问，由 parent 一次性创建内容；开发契约同时比较正式脚本全部 `$('<id>')` lookup 与包内 shell ID，防止清空容器后残留类似 eager lookup。

同机双实验真实并发证据：Run A `88d9edf7-8044-4015-b7db-4905fb337ca5`（experiment `9c18…` / revision `4c740…`）与 Run B `7e1f00e8-bd14-42ed-b497-4b04f476bfca`（experiment `f958…` / revision `1b75…`）同时处于 `RUNNING`，capacity 为 `active=2`、slot 1+2、`available=0`；最终均 `COMPLETED 1/1`，容量恢复为 `available=recoverable=1`。两者各有 1 frame、1 checkpoint、1 attempt、1 action、1 memory、9 model calls、0 retries，attempt 分别为 `6f29…` 与 `e6ff…`，Run 目录物理独立。

```text
node --check generative_agents/web/static/console-api.js
通过

python -m pytest tests/foundation/test_console_runtime.py tests/architecture/test_final_e2e_regressions.py -k "console_runtime or DEF045 or def_045" -q
5 passed, 16 deselected, 1 warning in 25.78s

python -m pytest tests/foundation/test_web_api.py tests/architecture/test_final_e2e_regressions.py -q
24 passed, 1 warning in 33.64s
```

开发侧状态仅为 `READY_FOR_RETEST`。当前子任务的 in-app Browser runtime 无可用浏览器实例，因此未伪造 fresh-tab 结论；根 Agent 需重启实际 Web 后继续验证动态卡、左侧导航、Agent modal 与整段旅程 `console error = 0`，再由测试侧决定是否 `VERIFIED`。

### DEF-036 键盘可达性补充

真实 1280×720 浏览器验证证明弹窗视觉滚动已正确，但背景控件仍在 Tab 顺序内，Agent 保存对键盘用户不可达。正式控制台现用统一 modal 生命周期保存 opener、对 `.app-shell` 启用 `inert`、聚焦第一个有效编辑字段，并将 Tab/Shift+Tab 圈定在当前 dialog；Escape、取消、保存均经同一关闭路径恢复 opener（删除后回到“新增 Agent”这一仍存在的合理焦点）。纯 `tabTarget` 算法拆到包内外部 `modal-focus.js`，既不引入 inline script，也可由 Node 直接执行四个边界方向；浏览器脚本负责真实 DOM 可见元素过滤、inert 与 focus restore。

首次真实复验进一步证明，在该浏览器控制环境中，Agent modal 的作用域 `keydown` 处理后不能依赖 native Tab 默认推进。最终算法对每一次正向/反向 Tab 都显式返回 next/previous，首尾循环、焦点逃逸时回到首/尾，并始终 `preventDefault + focus`；纯 JS 测试同时覆盖中间元素前进和后退，不再把默认浏览器行为当作契约。

```text
node --check generative_agents/web/static/modal-focus.js
node --check generative_agents/web/static/console-api.js

python -m pytest tests/foundation/test_console_runtime.py tests/architecture/test_final_e2e_regressions.py -k "console_runtime or modal_focus or DEF036 or def_036 or DEF045 or def_045" -q
8 passed, 15 deselected, 1 warning in 24.73s

python -m pytest tests/foundation/test_console_runtime.py tests/architecture/test_final_e2e_regressions.py -k "modal_focus or DEF036 or def_036" -q
3 passed, 20 deselected, 1 warning in 24.69s
```

开发侧仅标记 `READY_FOR_RETEST`；真实 Tab/Shift+Tab/Enter 与焦点恢复由根 Agent 和资深测试 Agent 独立复验。

## 2026-08-09 — DEF-046 实验全局状态同步

1. Web 增加跨实验 RunEvent cursor 与 SSE；每条活动包含 experiment_id、run_id、事件类型和 durable payload，断线使用 Last-Event-ID 续传，每 10 秒 sync 触发事实对账。
2. 控制台建立统一 global reconcile：列表卡片/筛选数量、当前实验状态、顶部动作、概览和 Run 历史使用同一事实入口；页面恢复、网络恢复、SSE 重连和返回列表都会对账。
3. `latestRunId` 与 `selectedRunId` 分离，浏览历史 Run 不再篡改实验最新 Run；未主动选择历史结果时进入结果页默认选中真实 latest Run。
4. 发布、再次运行、暂停/恢复/取消后重新读取 Experiment；发布后旧 Draft 会立即清除并切换只读，不再保留可编辑假状态。
5. ArtifactJob 增加 queued/running/retry 事件，结果操作视图展示持久化任务状态；异步列表、实验打开、概览摘要和结果筛选增加 generation guard。

```text
node --check generative_agents/web/static/console-api.js
python -m pytest tests/foundation/test_web_api.py tests/foundation/test_console_runtime.py tests/foundation/test_artifacts.py -q
14 passed, 1 warning

python -m pytest -q
130 passed, 1 warning in 51.43s
```

真实 Browser 使用独立临时数据库验证：不刷新页面即可同步实验 COMPLETED→QUEUED→COMPLETED、筛选计数、最新 Run、进度、概览顶部动作、Run history 与 artifact QUEUED/0%；测试服务随后停止。开发侧状态 `READY_FOR_RETEST`。

## 2026-08-09 — 运行可观测性与结果生命周期（ROL A～D）

### A+B：日志、模型追踪与 Checkpoint 产品闭环

1. `LogService` 以数据库 `RunAttempt.log_path` / `ArtifactJob.log_path` 为唯一入口，并通过 `RunStorageBoundary` 校验 Run 归属、相对路径、完整 symlink chain 和 area。Attempt/Artifact log 提供 metadata、UTF-8 byte-cursor 分页、SSE tail、受控下载；SSE ID 为不透明 `file_id:cursor`，追加保持身份，轮转/截断返回 409，终态 EOF 才关闭。
2. 文件 byte window 使用 seek 后最多读取 `limit+4`，任意非边界 cursor 返回 422；tail 可以回退到完整 Unicode codepoint。日志结构化行在跨页长行和终态无换行时只产生一次记录，非 EOF backlog 连续追页，不人为 sleep。
3. 模型 trace 列表保留物理 byte cursor，即使当前 EOF 也可继续读取后续 append；trace ID 为 Attempt 归属的不透明编码。详情 payload 使用相同 UTF-8 窗口，无损分页、敏感键脱敏但不再被错误截断为 2 KiB。
4. `CheckpointService` 合并 RunStep marker、物理目录和权威 recoverable boundary。`RECOVERABLE/RETAINED/PRUNED/INVALID` 四态明确：active boundary 缺失为 `CHECKPOINT_AUTHORIZED_BUNDLE_MISSING`，symlink/损坏永不 recoverable，未知 step 为 404。详情包含 Agent 坐标/行动/状态、对话、storage 分组、文件 manifest；JSON 预览按白名单 section 分页，不渲染向量。
5. 原 Run 恢复由 Run 级 recovery lock 串行化“重读状态→完整 checkpoint validate→rewind→事务排队”；无有效恢复点返回 409 且不增加 resume_count、不改变 Experiment/Run。通用 CHECKPOINT_BUNDLE 入口也执行完整校验，并拒绝不一致的 `source_step/checkpoint_step`。
6. 控制台“运行与制品”拆为日志、模型调用、系统事件、检查点、结果产物五个事实视图；Attempt/trace/event/checkpoint 均可继续加载。切 Run 会 abort 旧请求、close 旧流并依 generation/run/attempt 拒绝 stale response。

对应合同：ROL-LOG-001/002、ROL-TRACE-001、ROL-CHK-001/002、ROL-REC-001、ROL-SYNC-001；对应缺陷 DEF-047～050、053、055～059、061～063。开发侧状态为 `READY_FOR_RETEST`，不自行覆盖测试侧状态。

### C：单一 Replay V2 与不可变产物身份

1. `ReplayBundleV2` 是 `compress.py`、Artifact Worker 和 live window 的共同 schema/builder，版本固定为 `schema_version=2` / `ga-replay-v2`；旧 `_derived_replay_frames` 第二协议已删除。Step 保留 observed path、location 数组、action、currently、schedule item、conversation、domain event、memory delta、schedule revision、checkpoint 和真实 Attempt boundary。
2. `ArtifactJob` / `RunArtifact` 增加 `source_step`、`partial`、`generator_version`，迁移 `0005_artifact_source_identity` 更新并发唯一键。任务创建时冻结边界；Replay、Report、Result bundle、记忆和对话导出都只统计/读取 `<= source_step`，排队后 Run 推进不会污染旧任务；partial/final 使用不同 DB identity 与物理路径，READY 文件不会原地覆盖。
3. COMPLETED reconciliation 自动、幂等排队 BUILD_REPLAY + BUILD_REPORT。所有 artifact API 返回 source step、partial/final、generator、时间、大小和 SHA；preview/download 经 RunStorageBoundary、size/SHA 校验，并使用 `artifact_id + DB digest + stat` 安全缓存避免分页时 O(N×文件大小)。
4. `VerifiedRunFrameReader` 是 manifest、window、ArtifactBuilder 共用的 DB-owned frame authority：校验 RunStep path/SHA、symlink/containment、gzip envelope 的 run/step/attempt，并以 DB authority + inode/size/mtime 缓存。任一损坏都不会产生 READY manifest/window/artifact。
5. 旧 compressed 导入按 delta carry-forward 后再采样，不再把稀疏 frame 当完整 snapshot；嵌入的中文对话被解析为结构化 Conversation/Message，时间不伪造，源文件不修改。

对应合同：ROL-ART-001/002、ROL-RPL-001。数据库升级旧 ArtifactJob 的 log_path 保持 NULL，不伪造历史日志。

### D：正式时间探索播放器

1. 新增包内外部 `replay-player.js` 和本地 Phaser 3.90 runtime；生产页无 CDN、无 inline prototype、无 `.map-agent` 圆点 fallback。Phaser 直接加载受控 tilemap/tileset/sprite，sprite 映射失败显示带错误事实的占位符。
2. `GET /api/v1/runs/{run_id}/replay/manifest` 与 `/replay/steps?from_step=&limit<=100` 通过相同 V2 validator。窗口只读 DB 已提交 Frame，返回 result_version/available/source/next；跨窗口首 Step 会读取上一 RunStep 的 Attempt，避免伪造 boundary。
3. 玩家支持播放/暂停、前后 Step、速度、虚拟时间、自由镜头、Agent 跟随、选择、轨迹/名称/动作/对话/事件图层；时间轴记录 checkpoint/对话/事件/Attempt boundary，检查器展示位置、行动、当前状态、对话、记忆变化和日程修订。
4. 每次切 Run 都显式 `AbortController.abort()` + `GAReplayPlayer.destroy()`；播放器最多缓存五个 100-step window，RUNNING 时重新读取 manifest 扩展 available_step，不一次加载 10,000 Step。
5. 新增 `pyproject.toml` wheel package-data，生产 shell、player、Phaser、tilemap、tileset 与 Agent texture 在真实 wheel 安装后仍能由 FastAPI 提供。

### 本阶段验证与剩余风险

```text
node --check generative_agents/web/static/replay-player.js
node --check generative_agents/web/static/console-api.js

python -m pytest tests/architecture/test_run_observability_lifecycle_redlines.py -q
71 passed, 5 skipped, 1 warning in 47.65s
```

五个 skip 均来自当前 Windows 主机不能创建真实 file/directory symlink；absolute、`..`、cross-Run、重压缩篡改和 DB SHA 变化原断言已通过，但不能把 skip 宣称为已验证。正式 wheel build/install/HTTP boot 在 DEF-054 测试内通过。开发子任务没有执行 fresh Browser；需根 Agent 重启 Web 后验证地图加载、双 Run 切换、SSE/请求 teardown 与 console error=0。

当前播放器对完成态仍通过同一 DB-verified window API读取，不会读取未锁定事实，但“优先直接消费 final Replay Artifact”尚未作为独立下载/分窗数据源实现；旧 compressed 已完成语义 carry-forward 与 DB 投影，正式播放器直连 Legacy Artifact Adapter 的浏览器旅程仍需独立验收。历史 checkpoint fork 继续按 ROL-REC-002 不开放伪入口。

### Fresh Browser 修复：显式 Phaser Canvas renderer

首次 in-app Browser 进入时间探索时，Phaser 在 custom environment 拒绝 `AUTO` renderer
探测并抛出 `Must set explicit renderType in custom environment`，导致状态停在 LOADING。
播放器现固定使用包内 `Phaser.CANVAS`；仍由 Phaser 渲染真实 tilemap/tileset/sprite，未引入
DOM 圆点 fallback。基础回归明确拒绝 `PhaserRuntime.AUTO`，等待 fresh reload 独立复验。

Fresh Browser 随后发现切换 Run 时，下拉已重建为空但 Inspector 仍保留旧 Run Agent 事实。
回放选择现独立记录 `agent_key + revision_id`：切 Run 先销毁 Player 并清空 Inspector；新 Run
只有在同 Revision 且该 agent_key 仍存在时才显式恢复下拉和 Player selection，否则三者同时
清空。纯 Node 回归覆盖同 Revision 恢复、跨 Revision 拒绝和 Agent 缺失三种路径。

第三次视觉复验发现 Phaser 的 `parent` 被错误放入 `scale` 子配置，导致既有 canvas 被移动到
`body` 并横跨页面。Game 根配置现显式使用 `parent: resultMapCanvas.parentElement`，同时继续
传入唯一的既有 canvas；`scale` 只保留 RESIZE mode，并以 ResizeObserver 将宿主尺寸变化同步
给 ScaleManager。tilemap 的真实层名 `Interior Furniture L2 ` 含尾空格，播放器按原始 ID 加载，
不再产生 Invalid Tilemap Layer warning。Node DOM mock 验证根 parent、canvas identity 与配置层级。

### Fresh Browser：`interiors_pt3` 纹理规范化

内置 legacy `interiors_pt3.png` 为 `512×10032`，其底部 16 像素不足一个 32 像素 tile，Phaser 因而输出
`Image tile area not tile size multiple`。legacy 原图及 SHA-256 保持不变；正式 Replay 包新增 `512×10016`
规范化副本，只裁掉不构成 tile 的底部 16 像素，保留的 313 行 tile 已逐像素比对一致。Replay V2 world
descriptor 只为 `interiors_pt3` 声明 package-local override URL、尺寸、规范化方式和 SHA-256，播放器不屏蔽
warning，其他纹理仍使用 legacy package URL。FastAPI 静态 HTTP、Node 语法、wheel package-data 和哈希均纳入回归。

Fresh Browser 重启后确认 PNG override 已生效，但 Phaser 仍会基于 legacy `tilemap.json` 中的
`interiors_pt3.imageheight=10032` 再输出一次相同 warning。Replay 因此同时提供 package-local
规范化 tilemap；它与 legacy JSON 的唯一结构差异是该字段改为 `10016`，tilecount 仍为 5008、16 列、
313 行。原 JSON 的 SHA-256 保持不变；Replay V2 的 `tilemap_url` 指向规范化 JSON，并在
`tilemap_asset` 中声明新旧哈希及规范化原因。播放器继续消费 manifest URL，没有增加 warning 过滤。

### Fresh Browser / Live E2E：播放器 canvas 生命周期与子进程日志编码

Run 切换原先调用 `Phaser.Game.destroy(true, false)`，其中 `true` 会把 shell 唯一的
`#resultMapCanvas` 从 DOM 移除；下一 Run 因而在 `_createGame` 得到无 parent 的旧节点并报
`REPLAY_CANVAS_HOST_MISSING`。销毁现在使用 `removeCanvas=false`：renderer、scene、资源和请求仍被清理，
但 shell-owned canvas 留在 `#resultMap`，连续 running→completed→running 均复用同一节点且不创建副本。

真实 Windows worker 重定向日志在 byte 160 暴露 cp936 字节（例如 `乔治` 为 `c7 c7 d6 ce`），不是读取器
误判。Run worker 与 Artifact worker 的 subprocess 边界现显式继承环境副本并强制
`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，使 stdout/stderr 在写入受控文件前即生成 UTF-8；LogService
继续严格解码，不增加替换字符或有损 fallback。现存历史 cp936 日志保留为诊断证据，不在读取时静默改写。

### Service restart / Resume：不可变 Run manifest 复用

主服务重启后恢复 Run `cbbdd28e…` 的失败不是 checkpoint 损坏。对原 manifest 与重建候选逐字段比较，
定义、Revision、模型解析和资源完全一致；只有 `materialized_at` 从首次物化时间变为当前时间，且工作区更新使
`code_build_id` 从 `workspace-3a6476…` 变为 `workspace-dd337d…`，因此候选 hash 必然冲突。恢复 attempt 现在
先验证并复用首次 attempt 的 manifest：Run/Experiment/Revision 归属、definition hash、完整规范化 definition、
algorithm version 与 asset snapshot 必须和当前不可变 Published Revision 一致；首次时间、构建号、依赖版本仍
保留为原 Run provenance，不按新服务环境重写。定义或资源发生实质变化仍抛 `ManifestConflictError`。

### Resume legacy checkpoint：UTC 时间边界与零 trace 投影

Attempt 3 从 checkpoint 94 恢复后，旧向量索引 metadata 的 `create/expire/access` 是无 offset 的
`YYYYmmdd-HH:MM:SS`，而新 `SimulationClock` 为 aware UTC；索引清理直接比较二者触发 Python
naive/aware `TypeError`。旧字符串实际是原 `SimulationClock` 以自身时区格式化的 wall time；恢复时先用注入
clock 的 tzinfo 还原，再统一转换成 UTC instant 比较。Action、Schedule、Concept、索引清理与检索共用这条
边界；只有旧 clock 本身也无 offset 时才明确采用 UTC，绝不读取主机本地时区。旧 checkpoint 文件保持原样。

该异常发生在首次模型调用前，因此 `ModelTraceWriter` 尚未惰性创建 Attempt 3 JSONL；worker finally 的投影
又抛 `FileNotFoundError`，遮蔽了主异常。`ModelTraceProjector` 现在先验证 Run/Attempt 归属：没有 cursor 且文件
从未存在时幂等返回 0；worker finally 也只在 JSONL 已实际创建时调用投影。已经投影过的文件消失仍是完整性
错误，不会静默归零，已有事件的 DEF-032 最终 flush 路径保持不变。

### Agent 结果工作台：以 Agent 为主体的结构化结果

实验结果不再把“Agent 轨迹”作为一条独立时间线，也不再要求用户从日志反推角色行为。顶层结果视图现在是
“仿真总览 / 时间探索 / Agent / 运行诊断 / 结果与导出”；对话和记忆从顶层移入所属 Agent。Agent 列表按最近
活动排列，每个 Agent 可独立展开，并以计划、事件、行动、对话、记忆、状态变化六个分区呈现事实；搜索、活动
类型筛选和分区筛选均保留当前 Run/Agent 所有权，运行事件触发的全局刷新会重新读取并更新展开内容。

`ResultQueryService.agent()` 现在按 Run 边界聚合 Published Revision 的角色定义、日程修订、逐步行动、领域事件
归属、对话参与、记忆和采样状态变化。迁移 `0006_agent_decision_context` 为 `run_agent_steps` 增加结构化决策上下文；
新步骤会保存最多 20 条当步感知、日程摘要、行动、实际路径和分类记忆计数，不复制完整向量索引。旧 Run 通过
空 JSON 默认值保持可读，页面只展示它确实拥有的历史事实。真实 100 步 Run 已验证 25 个 Agent、六类分区、
Agent 切换、事件单类筛选和搜索；Fresh Browser 控制台为 0，仓库全量为 235 passed / 7 native-symlink skipped。

### Agent 社交身份：稳定 Key 与显示名双索引

Web 运行时把 `Game.agents` 改为按稳定 `agent_key` 存储后，旧认知领域仍用 `Event.subject` 的显示名查找人物；
`_reaction` 因此在模型调用前返回，连带使聊天、等待、人物寻路和占位格过滤失效。Run 级持久化、checkpoint 与
结果投影继续以 `agent_key` 为权威；Game 额外建立只读 `agents_by_name` 和 `agent_keys_by_name`，只把前者传入旧
认知领域，并让结果收集复用后者。发布校验阻止启用 Agent 重名，运行时仍有相同防线；Draft 可以保留未发布的
重名编辑状态。旧 checkpoint 的中文 `Event.subject` 无需迁移即可恢复解析，对话结果参与者仍投影为稳定 Key。

回归覆盖显示名反应解析、占位目标排除、Game 入口索引所有权、对话消息与稳定参与者 Key、发布重名校验和旧引擎
隔离。当前仓库全量为 250 passed / 7 native-symlink skipped。

### Windows Checkpoint 淘汰竞争修复

真实 Run `37d7c491-d42e-4b12-a621-af6c6ed0ad39` 在提交 Step 21 前失败：Web 结果页周期性读取
Checkpoint 列表时会完整打开并校验 bundle 成员，worker 同时对旧目录直接执行 `shutil.rmtree`；Windows 不允许
删除仍被其他进程读取的 `index_store.json`，并且逐文件删除会留下半损坏的公开 Checkpoint，异常又位于 Step
提交关键路径，最终将整个 Attempt 标为失败。

修复后，每个 Run 使用独立 `checkpoint.lock` 串行化发布、校验、预览、恢复复制和 Checkpoint ZIP 导出。保留策略
不再直接递归删除 `step-*`：先在同一目录原子重命名为私有 `.prune-*` tombstone，再有限重试删除；发生 Windows
共享冲突或外部索引器占用时延迟到后续 Step 清理。重命名失败会保留完整公开 bundle，重命名成功后的部分删除只会
发生在私有 tombstone；所有 retention `OSError` 都只记录维护告警，不再把已经持久化的新 Step 变成
`WORKER_ERROR`。恢复时的 bundle/state/conversation/storage 复制也保持在同一把锁内，避免 validate 后再读取的
TOCTOU 窗口。

回归覆盖确定性 `WinError 32`、真实主机打开 `resident-013/associate/index_store.json`、跨线程读写互斥、损坏
LATEST 回退、恢复、结构化详情/预览和精确 Checkpoint 导出。最终仓库全量为 253 passed / 7 native-symlink
skipped。
