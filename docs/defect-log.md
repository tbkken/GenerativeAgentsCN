# 缺陷与改进记录

> 文档版本：v0.4
> 首次审计：2026-08-08
> 状态：最终 E2E/旧导入对抗复验进行中

## 1. 字段与流程

每条缺陷使用以下字段：`ID`、`标题`、`严重度`、`状态`、`发现阶段`、`影响`、`证据/复现`、`期望`、`实际`、`根因分析`、`修复要求`、`验收标准`、`修复记录`、`回归记录`。

状态流转：`OPEN → ASSIGNED → FIXING → READY_FOR_RETEST → VERIFIED → CLOSED`。复验失败回到 `FIXING`；设计确认不修使用 `ACCEPTED`，必须记录接受人、影响与替代控制。当前 P0/P1 均不可接受为首期遗留。

当前汇总：P0 11 项，P1 57 项，P2 4 项；`VERIFIED` 70 项，`READY_FOR_RETEST` 0 项，`ASSIGNED` 2 项，`OPEN/FIXING` 0 项。`VERIFIED` 表示资深测试 Agent 已独立通过修复复验，尚未用管理动作折叠为 `CLOSED`。

## 2. 缺陷清单

### DEF-001 — Game/Timer 使用进程全局映射

- 严重度/状态：P0 / VERIFIED
- 影响：同进程执行或测试两个实验时，后创建的 Game/Timer 覆盖前者；领域模块隐式读取错误实验时间和对象。
- 证据/复现：`python -m pytest tests/architecture/test_isolation_redlines.py::test_def_001_runtime_has_no_process_global_game_or_timer_registry -q`。
- 期望：每个 Run 的 `SimulationContext` 显式持有 Game 依赖与 Clock，领域代码不访问进程全局状态。
- 实际：`modules/game.py`、`utils/timer.py`、`utils/log.py`、`agent.py` 使用 `GenerativeAgentsMap`/`get_timer()`。
- 根因：旧 CLI 按单实验单进程假设设计，依赖服务定位器。
- 修复要求：删除 Game/Timer 全局注册与读取，Clock/logger/context 构造注入。
- 验收标准：对应红线通过；A/B 同进程单元夹具交错执行时 clock/RNG/事件不串。

### DEF-002 — LlamaIndex 全局 Settings 污染向量配置

- 严重度/状态：P0 / VERIFIED
- 影响：Embedding model、splitter、context window 是进程级可变状态；测试或未来同进程任务会串模型，无法证明索引与 Revision 匹配。
- 证据/复现：`python -m pytest tests/architecture/test_isolation_redlines.py::test_def_002_vector_indexes_do_not_write_llama_global_settings -q`。
- 期望：index 创建/加载显式传入 embed_model 和 transformations，索引 metadata 保存 resolved model/config hash。
- 实际：`modules/storage/index.py` 写 `Settings.embed_model/node_parser/num_output/context_window`。
- 根因：使用 LlamaIndex 便捷全局配置。
- 修复要求：完全移除 Settings 写入，按 Run 创建显式依赖。
- 验收标准：红线通过；A/B 不同 embedding 交错创建索引，模型与结果各自稳定。

### DEF-003 — 运行目录由用户可见实验名拼接

- 严重度/状态：P0 / VERIFIED
- 影响：同名碰撞、路径穿越、跨 Run 覆盖；checkpoint、storage、compressed 不能以 run_id 隔离。
- 证据/复现：`python -m pytest tests/architecture/test_isolation_redlines.py::test_def_003_run_paths_are_not_derived_from_user_visible_names -q`。
- 期望：所有运行路径只能由受控 var_dir + UUID run_id 生成，API 不接受文件路径。
- 实际：Game/start/replay 使用 `results/.../{name}`。
- 根因：旧 CLI 把 simulation name 同时作为显示名和文件主键。
- 修复要求：实现 `RunPaths`，旧名称只保存在 metadata。
- 验收标准：相同显示名、相同 Agent 名、相同虚拟时间的 A/B 同时运行不产生相同绝对路径。

### DEF-004 — checkpoint 使用虚拟分钟命名并按字典序恢复

- 严重度/状态：P0 / VERIFIED
- 影响：`stride=0` 或分钟不推进时后一步覆盖前一步；跨天/异常文件可使恢复选择错误状态，结果不可恢复。
- 证据/复现：`python -m pytest tests/legacy/test_current_behavior_characterization.py::test_legacy_checkpoint_name_collides_when_virtual_minute_does_not_advance -q` 和对应 architecture redline。
- 期望：`step-NNNNNN` 单调身份，bundle hash 校验，LATEST 无效时按 step 倒序找完整 bundle。
- 实际：`simulate-<virtual-minute>.json`，恢复使用排序后的最后一个 JSON。
- 根因：虚拟时间被误用作存储提交序号。
- 修复要求：实现不可变 checkpoint bundle 与原子 LATEST；禁止 Web stride=0。
- 验收标准：同分钟连续两步、跨天、残缺 bundle、额外 JSON 文件四类场景均选择正确恢复点。

### DEF-005 — Agent 序列化提前原地持久化向量索引

- 严重度/状态：P0 / VERIFIED
- 影响：索引、Agent state、conversation 分三次写；任一点崩溃得到不同 step 的混合 checkpoint。
- 证据/复现：`python -m pytest tests/architecture/test_isolation_redlines.py::test_def_005_snapshot_serialization_has_no_hidden_index_persist -q`。
- 期望：`snapshot()` 无 IO；CheckpointWriter 在临时 bundle 写全量、hash、fsync 后原子 rename。
- 实际：`Associate.to_dict()` 调用 `_index.save()`，之后 start.py 才写 state/conversation。
- 根因：序列化和持久化职责混合。
- 修复要求：移除 to_dict IO 副作用，统一 StepCommitter/CheckpointWriter 所有权。
- 验收标准：在每个 bundle 文件间杀进程，只能加载上一个完整 bundle；临时目录永不被当作可恢复点。

### DEF-006 — 运行与制品读取共享 bootstrap 配置

- 严重度/状态：P1 / VERIFIED
- 影响：修改 `data/config.json`、Prompt、Agent 或地图会改变其他实验的后续调用或历史回放，发布快照失效。
- 证据/复现：`python -m pytest tests/architecture/test_isolation_redlines.py::test_def_006_runtime_does_not_read_shared_bootstrap_configuration -q`。
- 期望：bootstrap 仅在 catalog 初始化/legacy import 使用；worker/artifact 只读 Run manifest、frame、内容寻址资源。
- 实际：start、Scratch、compress 运行时直接读共享文件。
- 根因：配置未成为完整领域快照。
- 修复要求：PromptRepository、ConfigAdapter、manifest materialization、ArtifactService 全部改用 Revision/Run 输入。
- 验收标准：A/B 运行中修改 bootstrap，两个 manifest 和结果不变；旧 A Revision 重跑仍使用旧内容。

### DEF-007 — import 阶段解析命令行参数

- 严重度/状态：P1 / VERIFIED
- 影响：Web/pytest 导入 `start`/`compress` 会消费宿主参数并可能 `SystemExit`；compress/replay 又反向 import start，无法作为服务调用。
- 证据/复现：`python -m pytest tests/architecture/test_isolation_redlines.py::test_def_007_importing_product_modules_does_not_parse_process_arguments -q`。
- 期望：parser 构建和 parse_args 只在 `main(argv=None)` 内。
- 实际：模块顶层执行 `parser.parse_args()`。
- 根因：脚本未按可导入 package 组织。
- 修复要求：增加包入口，旧脚本委托 CLI main，无 import side effect。
- 验收标准：带任意 pytest/uvicorn argv 导入相关模块不解析参数、不启动进程、不访问文件。

### DEF-008 — 模拟循环丢弃完整 Agent/步骤结果

- 严重度/状态：P1 / VERIFIED
- 影响：无法实现真实 timeline、conversation、memory、schedule、domain event 和 model usage；页面只能猜测或扫日志。
- 证据/复现：`python -m pytest tests/architecture/test_isolation_redlines.py::test_def_008_simulation_loop_commits_complete_step_results -q`。
- 期望：每步 freeze 完整不可变 StepResult，frame 是可重建事实源，只有 StepCommitter 推进 available_step。
- 实际：start.py 对 `agent_think()` 只取 `["plan"]`，其余 info 丢弃。
- 根因：旧系统仅为最终 movement 回放保存必要状态。
- 修复要求：实现 result DTO/builder/collector/projector，并在业务发生处采集稳定 ID 记录。
- 验收标准：Fake run 账本逐 ID 对齐 frame、SQLite API、artifact；无二次 LLM 或文本猜测。

### DEF-009 — max_memory 容量少保留一条

- 严重度/状态：P1 / VERIFIED
- 影响：实验设定 N 实际保留 N-1，改变检索与反思结果；不同版本无法按配置复现。
- 证据/复现：`python -m pytest tests/legacy/test_current_behavior_characterization.py::test_legacy_memory_limit_keeps_one_less_than_configured -q`。
- 期望：每类最多且恰好保留配置上限 N，删除 `memory[N:]`，保留 `memory[:N]`。
- 实际：保留 `memory[:max_memory - 1]`。
- 根因：边界 slice off-by-one。
- 修复要求：修正并增加 N=1、2、N+1 输入回归；投影发 EVICTED delta。
- 验收标准：N=1 后恰好 1 条 active；索引与 memory 投影一致。

### DEF-010 — 短对话被估算为 0 分钟

- 严重度/状态：P1 / VERIFIED
- 影响：Action 立即完成、日程修订为零时长、时间分布和关系累计时长错误。
- 证据/复现：`python -m pytest tests/legacy/test_current_behavior_characterization.py::test_legacy_short_chat_duration_is_zero_minutes -q`。
- 期望：`max(1, ceil(chars / 240))`，conversation 标 `duration_source=ESTIMATED`，message 仅 sequence。
- 实际：`int(chars/240)` 向下取整。
- 根因：整数截断且未设置最短边界。
- 修复要求：按 algorithm profile 计算并回归 0/1/239/240/241 字符。
- 验收标准：非空对话最短 1 分钟；不生成虚假逐消息时间。

### DEF-011 — 模型用量只在内存汇总且物理失败不可追溯

- 严重度/状态：P1 / VERIFIED
- 影响：进程退出丢统计；失败请求、callback/schema 失败、fallback、跨 attempt 成本无法准确统计，结果页口径错误。
- 证据/复现：检查 `modules/model/llm_model.py:completion`；只有 `_completion` 成功后才递增 request，总结仅存在 `_summary`。
- 期望：attempt 独占 JSONL 记录每个 PHYSICAL_ATTEMPT 与 LOGICAL_END，cursor 幂等投影全 Run usage。
- 实际：普通日志 + 进程内数组，物理失败无持久事实。
- 根因：统计作为模型 wrapper 调试信息而非 Run 事实。
- 修复要求：实现 ModelCallRecorder/TraceProjector，默认不记录 payload，usage 不可用保持 null。
- 验收标准：2 次失败+1 次成功得到 logical=1、physical=3、retry=2；全失败 fallback 口径正确；重放 cursor 不重计。

### DEF-012 — 运行构造会修改发布配置对象

- 严重度/状态：P1 / VERIFIED
- 影响：同一 Revision 对象被复用、校验或哈希时内容已改变；地图 tile 丢 coord，Action 配置变为对象，破坏不可变假设。
- 证据/复现：`python -m pytest tests/architecture/test_isolation_redlines.py::test_def_012_published_world_and_action_inputs_are_not_mutated -q`。
- 期望：Pydantic Revision 输入只读；runtime state 单独构建，转换先复制。
- 实际：Maze `tile.pop("coord")`、Action.from_dict 和 Agent 构造原地改 config。
- 根因：旧代码把 JSON 同时当定义和可变运行状态。
- 修复要求：深/定向复制并分离 Definition 与 RuntimeState。
- 验收标准：构造前后 canonical definition bytes/hash 相同；重复构造两次结果一致。

### DEF-013 — 使用模块级 random 且 checkpoint 不保存 RNG

- 严重度/状态：P1 / VERIFIED
- 影响：A/B 调用顺序互相改变随机序列；恢复后重新 seed 不能延续原分支，路径/反思不复现。
- 证据/复现：`python -m pytest tests/architecture/test_isolation_redlines.py::test_def_013_simulation_randomness_comes_from_run_context -q`。
- 期望：Run context 持有独立 `random.Random`，所有 choice/sample 注入；state 进入 bundle。
- 实际：Agent/Maze/Scratch import 模块 random。
- 根因：单进程单实验假设。
- 修复要求：RandomSource 全链路注入，恢复 RNG state。
- 验收标准：A/B 交错执行等于各自独立执行；step K 恢复后的序列等于不中断基线。

### DEF-014 — 回放重算路径并混用当前地图

- 严重度/状态：P1 / VERIFIED
- 影响：历史回放路径可能穿过新地图或与真实 plan.path 不同，却被页面当成观测事实。
- 证据/复现：`tests/legacy/test_current_behavior_characterization.py::test_legacy_replay_reads_the_current_shared_catalog`；compress 调用 `maze.find_path`。
- 期望：新运行 frame 保存 OBSERVED path；artifact 只插值该 path，旧数据重算明确标 RECONSTRUCTED。
- 实际：compress 读取当前 maze 并重算最短路。
- 根因：旧 checkpoint 未保存真实路径。
- 修复要求：StepResult 记录 path；ArtifactService 不为新 Run 调 find_path。
- 验收标准：修改 bootstrap 地图后重建新 Run artifact hash/路径不变；旧 Run metadata 标 RECONSTRUCTED。

### DEF-015 — 旧回放接口接受 name 拼任意路径且 debug 启动

- 严重度/状态：P1 / VERIFIED
- 影响：`?name=../...` 进入路径拼接，违反 API 不接受任意路径底线；Flask debug 不应成为默认服务入口。
- 证据/复现：检查 `replay.py` 的 `results/compressed/{name}` 与 `app.run(debug=True)`。
- 期望：按 run UUID 数据库查归属和受控相对路径；旧入口 302 到 FastAPI result timeline。
- 实际：原始 query 直接拼目录并打开文件。
- 根因：本地脚本将名称视为可信。
- 修复要求：移除旧网络入口或只接收验证后的 run_id，由 RunPaths resolve containment。
- 验收标准：绝对路径、`..`、Windows 设备名、URL 编码穿越全部拒绝且不泄露本机路径。

### DEF-016 — logger 名称/handler 与领域异常不可隔离

- 严重度/状态：P2 / VERIFIED
- 影响：同 basename logger 可能共享 handler；重复构造累加输出；`IOLogger.error` 抛通用 Exception 丢失故障分类。
- 证据/复现：检查 `modules/utils/log.py`，logger name 为 basename，error 后直接 raise。
- 期望：`ga.run.<run_id>.<attempt>`，清本 logger handler、propagate false；日志与异常职责分开。
- 实际：路径 basename 决定 logger，error 方法控制流程。
- 修复要求：结构化 LoggerAdapter 与领域异常。
- 验收标准：A/B 相同日志文件 basename 仍写各自路径一次；错误码保留。
- 独立复验：`python -m pytest tests/foundation/test_retry_and_logging.py::test_file_loggers_with_same_basename_do_not_share_handlers -q` 通过；同 basename 的不同绝对路径各自产生且只产生一条记录，handler 不共享。

### DEF-017 — 重试固定 sleep/print，无法取消且缺乏结构化上下文

- 严重度/状态：P2 / VERIFIED
- 影响：一次索引/模型失败可阻塞长时间，暂停/取消反馈慢；stdout 无 run/attempt/agent 关联。
- 证据/复现：LLMModel 和 LlamaIndex 多处 `print` + `time.sleep(5)`，最多 10 次。
- 期望：快照配置退避，结构化日志，重试不持事务/锁，步骤边界控制可解释。
- 实际：固定 5 秒阻塞与普通 print。
- 根因：调试式重试未纳入 runtime control。
- 修复要求：可配置指数退避和 recorder，长请求支持 force cancel supervisor。
- 验收标准：失败 trace 具完整 attempt；期间其他 Run 心跳/查询不受阻。
- 独立复验：`python -m pytest tests/foundation/test_retry_and_logging.py::test_model_retry_wait_stops_on_run_control_request -q` 通过；控制信号可中断退避等待。

### DEF-018 — 无法从已发布 Revision 再启动一个 Run

- 严重度/状态：P1 / VERIFIED
- 发现阶段：技术方案/API 与结果 Run 历史交互一致性审计。
- 影响：完成一次运行后，没有草稿时无法复用完全相同的不可变版本再跑；“一个实验多次 Run”只能展示，不能产生。
- 证据/复现：`python -m pytest tests/architecture/test_design_contract_redlines.py::test_def_018_published_revision_can_be_run_again_without_new_draft -q`。
- 期望：提供从指定 PUBLISHED revision 创建 QUEUED Run 的 API/按钮，仍执行单 open Run 约束和模型可用性检查，不产生新 revision。
- 实际：只有 `publish-and-run`，它要求 draft_revision_id/lock_version。
- 根因：发布与启动被绑定为唯一写路径，遗漏复现实验流程。
- 修复要求：补充事务、API、幂等键和 UX；明确 requested_steps 使用 Revision 固定值。
- 验收标准：同 Revision 连续完成两次 Run，run_id/manifest_hash 不同，revision_id/definition_hash 相同，结果完全分区。
- 独立复验：`tests/architecture/test_final_e2e_regressions.py::test_agent_crud_world_asset_and_published_revision_rerun_http` 通过；取消首个 QUEUED Run 后，经公开 Revision 路由再次创建 Run，run_id 不同且 revision_id 相同。

### DEF-019 — force cancel 的结果/恢复边界定义矛盾

- 严重度/状态：P1 / VERIFIED
- 发现阶段：状态机与 StepResult/Artifact 一致性审计。
- 影响：available_step 可能大于 recoverable_step；方案一处要求“以最后有效 checkpoint 为边界”，另一处允许完整 frame 作为已提交结果。若未固定，会生成混合边界制品或错误回退有效结果。
- 证据/复现：对比技术方案 8.3 的 rewind 规则、10.5 force cancel 和 artifact source boundary。
- 期望：明确 force cancel 后 canonical 结果上界究竟是最后完整 frame 还是 recoverable checkpoint；数据库、artifact、resume 统一同一规则。
- 实际：文档没有定义强杀后是否立即 rewind、何时归档 future frames、何时允许制品读取。
- 根因：恢复状态与可读取事实是两个边界，但控制流程未拆开描述。
- 修复要求：建议 CANCELLED 保留已完整提交 available_step 作为结果事实，不允许恢复；若业务要求 checkpoint 边界则复用完整 rewind 协议后才入终态。二选一并写入状态/事务图。
- 验收标准：在 checkpoint 间 force cancel，reconcile 两次后 DB/frame/artifact 上界一致，无混合分支。
- 独立复验：`tests/foundation/test_run_service.py::test_reconcile_finishes_force_cancel_without_promoting_recovery_boundary` 与 `tests/architecture/test_adversarial_failure_boundaries.py::test_force_cancel_preserves_committed_results_but_not_false_recovery` 通过；结果事实边界与恢复边界保持分离。

### DEF-020 — PAUSED Run 无法直接取消

- 严重度/状态：P1 / VERIFIED
- 影响：PAUSED 是 open Run，会阻止本实验新运行，但 cancel API 排除 PAUSED；用户只能先恢复到队尾/占槽再取消，造成状态机死路与资源浪费。
- 证据/复现：`python -m pytest tests/architecture/test_design_contract_redlines.py::test_def_020_paused_run_can_be_cancelled_without_resuming_a_worker -q`。
- 期望：`PAUSED -> CANCELLED` 无 worker、无 slot 的短事务，保留现有 checkpoint/partial result，结束 Run。
- 实际：状态图只有 PAUSED -> QUEUED；cancel 合法状态不含 PAUSED。
- 根因：取消流程只按活跃进程考虑。
- 修复要求：增加状态边、API 合法来源、幂等行为、事件和实验 status 投影。
- 验收标准：PAUSED cancel 不创建 attempt、不占槽、checkpoint/frame 不改写，同实验随后可创建新 Run。
- 独立复验：`tests/foundation/test_run_service.py::test_paused_run_cancels_without_new_attempt_or_slot` 通过。

### DEF-021 — 实验列表无法按失败/取消状态发现记录

- 严重度/状态：P2 / VERIFIED
- 影响：API 返回 FAILED/CANCELLED counts，但高保真状态 Tab 缺失；实验多时无法有效定位需要处理的异常运行。
- 证据/复现：`python -m pytest tests/architecture/test_design_contract_redlines.py::test_def_021_terminal_failure_states_are_discoverable_from_status_tabs -q`。
- 期望：增加紧凑“异常/已结束”聚合入口或 FAILED/CANCELLED Tab；不恢复已移除的四指标卡。
- 实际：只有全部、运行、排队、草稿、暂停、完成。
- 根因：简化列表时把低频但高处理价值的状态一并隐藏。
- 修复要求：按实际数据量选择两个 Tab 或一个“其他状态”popover，URL status 可恢复。
- 验收标准：1,000 条实验中可在两次交互内定位全部 FAILED/CANCELLED，刷新保持筛选。
- 独立复验：服务端 `ABNORMAL` 聚合严格映射 FAILED/CANCELLED；正式前端将异常 Tab 映射为 `status=ABNORMAL`，设计契约红线通过。真实浏览器刷新/URL 恢复仍列为发布候选环境 E2E，而非本次静态复验替代项。

### DEF-022 — Run 选择器无法访问超过首个 cursor 页的历史

- 严重度/状态：P1 / VERIFIED
- 影响：技术方案 Run API limit=50/cursor，但高保真原生 select 没有加载更多/检索入口；运行次数多后早期结果不可达。
- 证据/复现：`python -m pytest tests/architecture/test_design_contract_redlines.py::test_def_022_result_run_selector_exposes_history_pagination -q`。
- 期望：可搜索/分批加载的 Run picker，或“查看全部运行”抽屉；选中历史 Run 后 URL 固定 run_id。
- 实际：固定 `<select>` 只含三个 option，没有分页状态。
- 根因：结果页增加 Run API 后未把分页交互落到高保真结构。
- 修复要求：补充 loading、empty、load-more、搜索、当前选中项不在首批时的回填，以及键盘交互。
- 验收标准：120 个 Run 中可选择第 120 个；刷新/后退恢复；加载中的慢响应不覆盖当前选择。
- 独立复验：`tests/foundation/test_run_service.py::test_run_history_uses_stable_cursor_and_can_reach_all_pages` 证明全部 cursor 页可达；前端 generation/cursor 防旧响应覆盖并提供加载更多入口，设计契约红线通过。

### DEF-023 — 内容寻址资源没有上传与受控读取 API

- 严重度/状态：P1 / VERIFIED
- 发现阶段：世界/Agent 配置交互与 API 闭环审计。
- 影响：方案要求地图图片、portrait、texture 先登记为 asset hash，timeline 又返回 asset ID，但调用方无法上传二进制、查询登记结果或加载内容；`PUT world` 和 Agent portrait 编辑不能真正实施。
- 证据/复现：`python -m pytest tests/architecture/test_design_contract_redlines.py::test_def_023_world_assets_have_upload_and_content_delivery_apis -q`。
- 期望：定义受限大小/类型的流式上传接口，服务端计算 hash/size/MIME 并幂等登记；定义按 asset ID/hash 的同源读取接口，带 ETag、受控 Content-Type 和路径 containment。
- 实际：文档只有 assets 表和草稿中的引用，没有资源 HTTP 契约。
- 根因：数据模型设计完成后遗漏了配置 UI 到内容寻址库的入口/出口。
- 修复要求：补 API、错误码、大小限制、重复内容响应、原子写入、预览/下载语义和测试；客户端不可提交可信 hash 作为事实。
- 验收标准：上传相同内容两次只产生一个物理对象；不同实验可安全复用；篡改 hash、MIME、绝对路径和 `..` 均不能越界；Revision manifest 可解析全部资源。
- 独立复验：合法上传/ETag/重复内容幂等、非法内容 HTTP 422、世界 Draft 关联 asset hash 与受控读取均通过；命令见 DEF-037 及 `test_agent_crud_world_asset_and_published_revision_rerun_http`。

## 3. 基线执行记录

2026-08-08：

```text
python -m pytest tests/legacy tests/architecture -q
8 passed, 15 failed
```

15 个失败分别覆盖 DEF-001/002/003/004/005/006/007/008/012/013/018/020/021/022/023。DEF-009/010/014 由通过的 legacy characterization 证明当前行为；DEF-011/015/016/017/019 为审计缺陷，待对应实现接口落地后补运行/故障注入测试。

## 4. 第二阶段开发修复交接

> 以下为开发 Agent 的修复证据，状态仅到 READY_FOR_RETEST；是否 VERIFIED 由资深测试 Agent 独立决定。

| DEF | 根因结论 | 修改文件 | 开发验证 | 状态 |
| --- | --- | --- | --- | --- |
| 001 | 单实验脚本把依赖定位器当作领域依赖 | `modules/game.py`、`agent.py`、`utils/timer.py`、`utils/log.py`、`utils/namespace.py`、memory/prompt 模块 | architecture redline；A/B clock/RNG 交错行为测试 | VERIFIED |
| 002 | LlamaIndex 便捷全局 Settings 造成 embedding 污染 | `modules/storage/index.py` | architecture redline；两个不同 embedding 实例显式传入索引测试 | VERIFIED |
| 003 | display name 同时作为存储 identity | `start.py`、`modules/game.py`、`replay.py`，复用 `runtime/context.py::RunPaths` | architecture redline；路径只含受控 data root + run UUID | VERIFIED |
| 004 | 虚拟时间被误当单调提交序号 | `start.py`、`runtime/worker.py`，复用 `runtime/checkpoint.py` | bundle/LATEST 故障、DB recoverable 边界和 worker resume regression | VERIFIED |
| 005 | 序列化与索引 persist 职责混合 | `memory/associate.py`、`modules/game.py`、`start.py`、`runtime/worker.py` | 原子 bundle；恢复前复制到 attempt 独占可写 storage | VERIFIED |
| 006 | bootstrap 目录被当作运行时事实源 | `start.py`、`prompt/scratch.py`、`compress.py`、`modules/config_adapter.py` | architecture redline；ConfigAdapter 纯映射且不改变 definition | VERIFIED |
| 007 | 脚本顶层构建 parser 后立即消费宿主 argv | `start.py`、`compress.py` | AST import-side-effect redline 与 main(argv=None) regression | VERIFIED |
| 008 | `agent_think()` 返回的 info/events 被循环丢弃 | `start.py`、`modules/agent.py`、runtime result collector/types/projector 契约 | Fake Game 完整 StepResult：Agent/path/currently/domain event/commit | VERIFIED |
| 009 | 上限 slice 使用 N-1 | `memory/associate.py`、`modules/agent.py`，结果 collector 契约 | N=1/2/8 均恰好保留 N 条；淘汰项进入 `EVICTED` MemoryDelta | VERIFIED |
| 010 | 字符时长向下取整且没有最短值 | `modules/agent.py` | 0/1/239/240/241 字符边界为 1/1/1/1/2 分钟 | VERIFIED |
| 011 | wrapper 只累计内存 summary，失败尝试无事实记录 | `model/llm_model.py`，接入 `runtime/model_trace.py` | 1 失败+1 成功产生 2 PHYSICAL_ATTEMPT + 1 LOGICAL_END，token/脱敏校验 | VERIFIED |
| 012 | JSON definition 与运行时对象共用可变 dict | `maze.py`、`memory/action.py`、`agent.py`、`config_adapter.py` | Maze/Action 构造和 adapter 前后输入深度相等 | VERIFIED |
| 013 | Agent/Maze/Scratch/Spatial 直接调用模块 random | 相应四个模块、`modules/game.py`、`start.py`、`runtime/worker.py` | 交错隔离；checkpoint RNG JSON 往返；worker 调用 restore | VERIFIED |
| 014 | 旧 checkpoint 不存真实 path，compress 用当前地图补算 | `modules/agent.py`、`start.py`、`compress.py` | frame OBSERVED path 原样进入 artifact；不调用 `Maze.find_path` | VERIFIED |
| 015 | Flask debug query name 直接拼本机路径 | `replay.py`、`compress.py` | 只接受 RunPaths；artifact logical name 白名单和 containment | VERIFIED |

旧 characterization 中对应 DEF-004/005/007/009/010/014 的“已知错误”断言已改为修复后的正向行为测试。变更没有删除场景或放宽红线，而是把断言从“确认错误存在”改为“确认边界行为正确”。

修复记录：DEF-001～015 已由开发 Agent提交独立复验。
回归记录：资深测试 Agent 于 2026-08-08 独立运行静态红线、旧行为兼容、运行时恢复及全量测试；DEF-001～015 均转为 `VERIFIED`。其中 DEF-004/005/013 额外覆盖 checkpoint 精确步选择、attempt 独占 storage、多 tile address 下保留 checkpoint coord、RNG JSON round-trip 与首个续跑 StepResult 的 `from_coord`。

## 5. 第二阶段新增缺陷与独立复验

### DEF-024 — 软取消请求无法升级为强制取消

- 严重度/状态：P1 / VERIFIED
- 发现阶段：supervisor 状态机对抗复验。
- 影响：第一次 `force=false` 后 Run 已是 `CANCEL_REQUESTED`，第二次 `force=true` 被当作空操作；卡死 worker 永远不会进入 supervisor 强杀集合并持续占槽。
- 证据/复现：`python -m pytest tests/architecture/test_adversarial_failure_boundaries.py::test_force_cancel_request_can_escalate_after_soft_cancel -q`。修复前断言得到最新 state event 的 `force=false`，期望为 `true`。
- 根因：`RunService.cancel()` 的 `CANCEL_REQUESTED` 分支无条件 `pass`，没有把后续 force 请求持久化为升级事件。
- 修复要求/验收标准：后续 `force=true` 必须幂等追加或升级 durable state event，`supervisor_action_required=true`；重复调用不降低 force。
- 修复记录：`CANCEL_REQUESTED + force=true` 现在追加升级事件。
- 回归记录：同一测试修复后通过；全量复验通过。

### DEF-025 — 较新的孤儿 checkpoint 会永久阻断按数据库恢复点续跑

- 严重度/状态：P1 / VERIFIED
- 发现阶段：checkpoint 与 SQLite 非原子提交故障注入。
- 影响：step 2 bundle 已落盘、SQLite 仍只提交 step 1 时，worker 以 `recoverable_step=1` 启动却读取 LATEST step 2，随后因 step mismatch 失败；每次恢复都重复失败，Run 无法前进。
- 证据/复现：`python -m pytest tests/architecture/test_adversarial_failure_boundaries.py::test_resume_selects_checkpoint_at_database_recoverable_boundary -q`。修复前抛出 `RuntimeError: checkpoint step 2 does not match 1`。
- 根因：恢复只读取物理 latest，没有按数据库授权的 recoverable boundary 选择并处置 future bundles。
- 修复要求/验收标准：精确选择 `step == recoverable_step` 的已验证 bundle；更大 step 的 bundle 进入当前 Run 的 `orphaned/`，不得删除其他 Run 文件；恢复出的 Agent/conversation/RNG/storage 必须来自精确边界。
- 修复记录：新增精确恢复选择与 future checkpoint 原子隔离。
- 回归记录：同一故障注入修复后通过；attempt storage 后续污染测试同时通过。

### DEF-026 — checkpoint 扫描接受目录步号与 bundle 语义不一致的伪有效数据

- 严重度/状态：P1 / VERIFIED
- 发现阶段：checkpoint 元数据篡改与 LATEST 损坏回退。
- 影响：攻击、磁盘损坏或错误写入可让 `step-000002/bundle.json` 声称 `step_no=999`；扫描仍把它当作最新有效恢复点，造成错误分支或恢复失败。
- 证据/复现：`python -m pytest tests/architecture/test_adversarial_failure_boundaries.py::test_checkpoint_scan_rejects_semantically_tampered_bundle -q`。修复前实际返回 `step-000002`，期望降序回退到 `step-000001`。
- 根因：validator 只校验声明过的文件 hash，没有校验 required/undeclared members、目录步号、frame hash 以及 run/attempt/step/time 交叉语义。
- 修复要求/验收标准：上述语义任一不一致时拒绝 bundle，并继续降序搜索完整候选；不得信任自声明但不完整的 `files=[]`。
- 修复记录：validator 已补 required/undeclared files、目录/bundle/frame/state 交叉校验。
- 回归记录：同一篡改用例修复后通过；原 state 内容损坏回退与 retention 用例仍通过。

### DEF-027 — 同一 var_dir 可启动两个调度领导者

- 严重度/状态：P1 / VERIFIED
- 发现阶段：supervisor 静态红线与双实例启动复验。
- 影响：两个 Web 实例可同时认领队列、reconcile 和强杀；即使 SQLite 降低重复认领概率，也无法保证唯一调度领导者及故障处置顺序。
- 证据/复现：初次源码审计 `LocalProcessSupervisor` 未持有 `scheduler.lock`；验收命令为 `python -m pytest tests/architecture/test_adversarial_failure_boundaries.py::test_second_supervisor_cannot_start_for_same_var_dir -q`。
- 期望/实际：期望第二实例启动因排他锁失败；初次实现两实例均可进入调度循环。
- 修复要求/验收标准：start 在 reconcile 前获得 `<var_dir>/scheduler.lock`，stop 释放；同实例重复 start 幂等，另一实例立即失败。
- 修复记录：supervisor 生命周期已接入 `FileLock`。
- 回归记录：静态锁所有权红线与双 supervisor 动态竞争均通过。

### DEF-028 — 活 PID 的过期心跳不会被 reconcile 中断

- 严重度/状态：P1 / VERIFIED
- 发现阶段：进程存活但运行线程/心跳失效故障注入。
- 影响：worker 进程仍存在但已挂死时，Run 永远保持 RUNNING 并占用 slot；排队实验无法获得容量。
- 证据/复现：`python -m pytest tests/architecture/test_adversarial_failure_boundaries.py::test_reconcile_interrupts_live_process_with_stale_heartbeat -q`。修复前 `interrupted_run_ids=()`，期望包含该 Run。
- 根因：reconcile 仅检查 PID/create_time，不检查 `heartbeat_at` 超时。
- 修复要求/验收标准：PID 匹配但心跳显著超过阈值时结束当前 attempt、清空槽并转 `INTERRUPTED`；新鲜心跳不得误杀。
- 修复记录：scheduler 增加 heartbeat timeout 对账。
- 回归记录：10 分钟陈旧心跳用例修复后通过。

### DEF-029 — Run 详情始终返回空 available_step

- 严重度/状态：P1 / VERIFIED
- 发现阶段：跨 Run 结果事实与 Run selector API 复验。
- 影响：结果已经投影，六个结果视图可读取，但 Run 选择器仍得到 `available_step=null`；页面会错误显示等待首步、禁用播放或覆盖正确 result 状态。
- 证据/复现：`python -m pytest tests/architecture/test_adversarial_failure_boundaries.py::test_run_detail_exposes_projected_available_step -q`。修复前实际为 `None`，SQLite `run_result_summaries.available_step=1`。
- 根因：`RunService._run_detail()` 返回硬编码 `None`，未读取结果摘要。
- 修复要求/验收标准：Run 详情/历史列表从同 Run 的结果摘要读取 available_step；无投影时为 0 或契约定义的空值，禁止使用 `completed_steps` 冒充。
- 修复记录：RunService 已按 run_id 读取结果摘要。
- 回归记录：专用 API 测试与跨 Run EVICTED 事实测试通过。

### 5.1 第二阶段命令证据

首次新增故障边界执行结果：`3 failed, 3 passed`，失败对应 DEF-024/025/026。继续补充心跳与结果边界后，DEF-028/029 也在修复前稳定失败。开发修复后，资深测试 Agent 独立执行：

```text
python -m pytest tests/architecture/test_adversarial_failure_boundaries.py -q
11 passed

python -m pytest -q
91 passed, 1 warning
```

warning 是当前 Python 3.13 环境中 Starlette `TestClient` 对 httpx 的弃用提示，不影响断言，但依赖升级前必须保留跟踪。

## 6. 最终 E2E 与旧导入独立复验

### DEF-030 — Worker 重型 import 早于心跳续租导致 0 步中断

- 严重度/状态：P1 / VERIFIED
- 影响：Windows 上模型/引擎依赖导入超过 heartbeat lease 时，健康 worker 被 reconcile 为 `INTERRUPTED`，实验 0 步结束。
- 证据/复现：`python -m pytest tests/architecture/test_final_e2e_regressions.py::test_def_030_worker_renews_ownership_before_heavy_engine_import -q`。
- 期望/实际：期望先校验 attempt ownership、启动 control/heartbeat monitor，再导入引擎和模型 SDK；旧实现顺序相反。
- 根因：把高成本 import 放在进程初始化和首次 heartbeat 之前。
- 修复记录：引擎与 LLM factory 改为 heartbeat 启动后的延迟 import。
- 独立回归：静态顺序红线通过；真实 Windows 进程冷启动长耗时压力仍需发布候选环境保留。

### DEF-031 — Agent deepcopy 运行时 thread lock 崩溃

- 严重度/状态：P1 / VERIFIED
- 影响：`RunControl`/logger 等运行时协作者含 `_thread.lock`，随 embedding config 深复制会在 Agent 构造期崩溃，0 步结束。
- 证据/复现：`python -m pytest tests/architecture/test_final_e2e_regressions.py::test_def_031_runtime_thread_lock_is_not_deepcopied_into_agent -q`。
- 期望：只深复制可序列化定义，运行时引用剥离后按 identity 注回。
- 修复记录/回归：Agent 已采用剥离再注回；动态构造真实 `RunControl` 并断言 Associate 收到同一对象，测试通过。

### DEF-032 — trace 未完整投影到 model_usage/summary

- 严重度/状态：P1 / VERIFIED
- 影响：结果页模型调用数、重试数和成本可为空或少计；特别是首步提交前失败，JSONL 已有完整事实但没有 StepResult callback。
- 证据/复现：已提交步的投影测试 `test_def_032_worker_projects_trace_after_each_committed_step` 通过；`test_def_032_zero_step_failure_still_projects_complete_model_traces` 当前失败。
- 期望：每个已提交步后增量投影，并在 worker `finally` 对当前 attempt 做一次幂等 complete-record flush，然后才结束 attempt。
- 实际：当前只在 `StepAndTraceProjection.commit_step()` 调用 projector；0 步/步内异常无退出 flush。
- 验收标准：首步模型记录完整后注入异常，Run 0 步终止但 `RunModelUsage` 与 summary logical/physical/retry 仍与 trace 一致；尾部半行不消费，重复 flush 不重计。
- 修复记录/回归：worker `finally` 在 finish_worker 前追加当前 attempt 的幂等 projector flush；已提交步与零步退出两个原断言均独立通过。

### DEF-033 — Artifact replay 读取不存在的 RunAgentStep.path_json

- 严重度/状态：P1 / VERIFIED
- 影响：真实一旦有可用步，BUILD_REPLAY 因 ORM 字段不存在而失败；0 步测试无法暴露。
- 证据/复现：`test_def_033_artifact_replay_closes_over_observed_frame_path` 写入一步真实 frame/SQLite 投影并执行 artifact job。
- 期望：replay 只从 Run 的 committed frame 读取 OBSERVED path，不从当前地图重算。
- 修复记录/回归：ArtifactBuilder 已改读 FrameStore；产物 path 精确等于 `[[0,0],[1,0]]` 且 `path_source=OBSERVED`，闭环通过。

### DEF-034 — 0 步 timeline 响应缺少 agent_steps/requested_steps

- 严重度/状态：P1 / VERIFIED
- 影响：同一 API 因 available_step 是否为 0 返回不同 schema；前端 timeline filter/slider 在首步前或 0 步终态崩溃或必须猜默认值。
- 证据/复现：`python -m pytest tests/architecture/test_final_e2e_regressions.py::test_def_034_zero_step_timeline_has_the_same_collection_schema -q`；实际缺 `agent_steps`、`requested_steps`。
- 根因：`ResultQueryService.timeline()` 的空范围早退字典少于正常返回字段。
- 修复要求/验收标准：所有路径返回同一完整 schema，集合为空而不是缺字段；前端容错可保留但不能替代 API 契约。
- 修复记录/回归：空范围早退已补 `requested_steps`、`agent_steps=[]`；使用原失败断言独立复跑通过。

### DEF-035 — SSE 历史状态覆盖当前终态

- 严重度/状态：P1 / VERIFIED
- 影响：结果页先得到当前 `INTERRUPTED/CANCELLED`，随后重放的历史 `RUNNING` 又覆盖 badge，可能暴露错误控制动作。
- 证据/复现：`test_def_035_sse_opens_after_current_state_and_history_cursor` 对 500 条内 backlog 通过；`test_def_035_sse_cursor_skips_an_event_backlog_larger_than_one_page` 当前失败。
- 根因：前端从升序 `/events?limit=500` 的第一页末尾开流；超过一页时它不是最新 cursor。
- 修复要求/验收标准：使用服务端 latest/tail cursor 或完整翻页到尾部后再开 SSE；构造 1,001 个历史事件，连接期间 badge 不得从数据库当前终态回退。
- 修复记录/回归：前端打开 SSE 前循环翻到事件尾 cursor；当前状态优先与超过单页 backlog 两个原断言均独立通过。

### DEF-036 — Agent 长弹窗在实际视口不可滚动及键盘焦点逃逸

- 严重度/状态：P1 / VERIFIED
- 影响：小屏/缩放视口中 footer 不可达，或 modal 打开后键盘焦点继续遍历背景 Agent 菜单，都会令仅键盘用户无法可靠完成 Agent CRUD，并可能误触背景操作。
- 布局复验：真实 in-app Browser 1280×720、devicePixelRatio=1 下 modal top=20/bottom=700、高 680；body clientHeight=536/scrollHeight=826/`overflow-y:auto`；footer bottom=699，Save bottom=686。长内容滚动与保存按钮可达已经真实通过。CUA 尝试 `Ctrl+=` 后 inner/visual viewport 未变化，不能声称 125% zoom 已真实通过；缩放目前只有 CSS/DOM 约束证据。
- 键盘失败证据：打开 Agent modal 后连续 18 次真实 Tab 仍遍历背景 Agent“⋯”按钮；弹窗没有初始 focus、背景不 inert、无 focus trap，键盘无法到达并保存当前 Agent。
- 静态复现：`python -m pytest tests/architecture/test_final_e2e_regressions.py::test_def_036_agent_modal_traps_focus_and_restores_the_trigger -q`；当前失败于 `openAgentEditor` 未记录 `document.activeElement`，后续 inert/Tab/Escape/restore 契约同样缺失。
- 根因：通用 modal 只修复了几何布局和滚动，没有实现对话框的焦点生命周期；背景 DOM 仍在正常 tab order。
- 修复要求/验收标准：打开时保存触发控件并聚焦首个可编辑字段；背景主界面 inert；Tab/Shift+Tab 只在 modal 可聚焦项间循环；Esc、保存、取消、删除均关闭 modal、解除 inert，并把焦点恢复到原触发控件（若已删除则恢复到新增 Agent 或 Agent 列表的稳定控制）；真实 1280×720 与键盘路径、静态焦点红线同时通过，125% zoom 在工具可验证 viewport 变化时补测；环境无法改变 zoom 必须明确记录，不得伪造通过。
- 初修浏览器复验：fresh Browser 打开后 `activeElement=agentEditName`、`.app-shell.inert=true`、modal 内 15 个 focusable 可见；Esc 后 modal 关闭、inert 解除、焦点恢复到 aria-label=`编辑 乔治` 的原触发按钮。此时 CUA Tab 与 locator `press('Tab')` 均停在首字段，根因是 helper 对中间项返回 `undefined` 并依赖当前环境未执行的 native default。
- 二次修复/静态回归：通用 `tabTarget` 已改为每次 Tab/Shift+Tab 显式返回 next/previous（含首尾 wrap 与 outside fallback），调用方始终 preventDefault 后 focus 目标。`test_def_036_agent_modal_traps_focus_and_restores_the_trigger` 与 `test_def_036_modal_focus_explicitly_advances_middle_items` 均通过。
- 最终 Browser 回归：1280×720 下打开后首焦点为 `agentEditName` 且背景 inert；真实 Tab 依次经过 age→portrait→x→y→currently→innate→learned→lifestyle→dailyPlan→spatial→delete→cancel→save→close→name，16 次均 `inside=true`；真实 Shift+Tab 为 age→name→close，反向 wrap 正确。Esc 关闭、解除 inert 并恢复“编辑 乔治”触发按钮；键盘 Tab 到 Save 后用真实 click 保存，modal 关闭、toast=`Agent已保存`、焦点恢复原编辑按钮、console errors=[]。
- 自动化环境限制：Browser 插件的 CUA/DOM_CUA/locator `press('Enter')` 只发 key event，未触发浏览器对原生 button 的默认 click，因此不能声称 Enter 激活已被真实自动化验证。产品使用原生 button，本轮不为测试适配器增加重复 Enter handler；保留浏览器/辅助技术兼容矩阵后续验证。

### DEF-037 — 非法资源内容上传返回未处理 500

- 严重度/状态：P1 / VERIFIED
- 影响：格式错误或伪造 MIME 的常见用户输入变成服务异常，错误 envelope 与 UI 提示丢失。
- 证据/复现：`test_def_037_invalid_asset_upload_is_a_422_error_envelope` 上传声明 JSON 的非法字节。
- 修复记录/回归：AssetValidationError 映射为 `ServiceError(INVALID_ASSET, 422)`；HTTP 422、统一 error code 与 request id 均通过。

### DEF-038 — 初始结果骨架展示原型假数据

- 严重度/状态：P1 / VERIFIED
- 影响：真实 API 返回前页面展示不存在的 Run、配置 hash、记忆总数和实验事实；慢网/接口失败时假数据会长期保留。
- 证据/复现：`test_def_038_production_result_shell_contains_no_real_looking_demo_facts`；空库 `/` 仍含 `run_0109`、`cfg_8f3a2c1`、`共 3,842 条` 等。
- 根因：生产入口直接读取完整高保真 prototype HTML。
- 修复要求/验收标准：生产 shell 初始只允许中性 loading/empty，占位不得像真实事实；空库、慢 API、API 500 三场景均无假 Run/Agent/结果。
- 修复记录/回归：生产入口改读包内 fact-free shell；API 接管的结果容器初始只有中性 loading/empty，空库响应不再包含真实感假事实，原失败断言通过。

### DEF-039 — 实验运行 badge 与列表沿用原型假计数

- 严重度/状态：P1 / VERIFIED
- 影响：空库仍显示运行数 3、固定实验卡和 Agent/模型数字，直接破坏“实验为中心”的真实性。
- 证据/复现：`test_def_039_production_experiment_badges_are_not_hardcoded`；空库 `/` 含 `navRunCount=3` 与 13 个硬编码 `.experiment-card`。
- 修复要求/验收标准：所有 badge/list 由 API 投影；初始只显示空/加载态，空库严格为 0 且无实验卡。
- 修复记录/回归：包内生产 shell 物理移除硬编码实验卡并清空 nav count，交由正式 API 脚本渲染；原失败断言通过。

### DEF-040 — 生产页同时执行原型与正式双重监听器

- 严重度/状态：P1 / VERIFIED
- 影响：内联 prototype state/listeners 与 `console-api.js` 同时处理点击、run selector、artifact 和保存动作，可能产生双提交、假 toast 与状态回写覆盖。
- 证据/复现：`test_def_040_production_shell_does_not_ship_prototype_event_listeners`；生产 `/` 返回大段 inline script 并再次加载正式外部脚本。
- 修复要求/验收标准：生产文档不得执行原型内联脚本，只加载一次正式 bundle；关键写动作每次交互只产生一个 HTTP 请求/一个状态转换。
- 修复记录/回归：包内生产 shell 不含任何内联 script，只加载一次 `console-api.js`；原失败断言通过。

### DEF-041 — legacy 同名源文件违反 artifact 唯一键

- 严重度/状态：P1 / VERIFIED
- 影响：checkpoint/compressed 均含 `conversation.json` 时用 basename 作为 logical_name，触发 `uq_run_artifact_identity`，整个源导入失败。
- 证据/复现：`test_def_041_to_043_legacy_artifacts_log_and_counts_are_consistent` 同时创建两个同名源文件。
- 修复记录/回归：logical name 加 `checkpoints/`、`compressed/` 前缀；四个制品均位于受控 Run 目录、内容不变且源树未修改。

### DEF-042 — legacy RunAttempt.log_path 指向不存在文件

- 严重度/状态：P2 / VERIFIED
- 影响：运维/结果页宣称有 attempt 日志但下载/诊断失败，来源审计链断裂。
- 证据/复现：同 DEF-041 测试读取数据库 log_path 并解析真实一行结构化 JSON。
- 修复记录/回归：导入事务建立 `logs/legacy-import.log`，记录 schema/event/source fingerprint/snapshot completeness/time；文件存在且可解析。

### DEF-043 — legacy conversation 已持久化但统计恒为 0

- 严重度/状态：P1 / VERIFIED
- 影响：conversation/message 明细存在，summary、RunStep 与 AgentSummary 却为 0，六结果视图相互矛盾。
- 证据/复现：同 DEF-041 测试导入 1 个 conversation/2 条 message，断言 step 与参与 Agent summary 均为 1/2。
- 修复记录/回归：导入时按 step 和 participant 回填；capability 同时为 conversations AVAILABLE。

### DEF-044 — 生产首页依赖仓库 docs 文件

- 严重度/状态：P1 / VERIFIED
- 影响：wheel、容器或最小生产包通常不携带仓库 `docs/`；`GET /` 运行时读取 `docs/experiment-console.html` 会直接 500，即使 API 与静态资源均安装正确。
- 证据/复现：`python -m pytest tests/architecture/test_final_e2e_regressions.py::test_def_044_homepage_shell_and_images_are_packaged_runtime_assets -q`；当前 homepage 从 `Path(__file__).parents[2] / "docs"` 读取，原型又引用相对 `resources/snapshot.png`。
- 根因：把设计原型及其 docs/resources 当作运行时模板资产，既导致 DEF-038～040 的假数据/双监听，也破坏 Python 包自包含。
- 修复要求/验收标准：中性生产 shell 与 snapshot 全部放入 `generative_agents/web/static` 或包内 templates，并进入 wheel package-data；HTML 只引用 `/static/console/snapshot.png`；在不含仓库 docs 的临时安装/构建 wheel 后，`GET /` 和图片 GET 均为 200、图片 MIME/PNG signature 正确。
- 修复记录/回归：中性 shell、正式脚本和 `snapshot.png` 均迁入 `generative_agents/web/static`；Web 不再读取或 mount `docs`，首页和 PNG 静态 GET 的原断言通过。
- 修复记录/回归：homepage 只读取 `generative_agents/web/static/experiment-console.html`，snapshot 同目录打包并由 `/static/console` 提供；扩展原断言验证首页 200、图片 200、`image/png` 与 PNG signature，独立通过。

### DEF-045 — 正式 console bundle 隐式依赖已删除的原型全局变量

- 严重度/状态：P1 / VERIFIED
- 发现阶段：真实 in-app Browser 动态实验卡交互。
- 影响：实验列表能由 API 动态渲染，但点击卡片立即抛 `ReferenceError`，无法进入实验详情；错误处理器自身再次抛错，用户只看到无响应，整个实验中心核心路径阻断。
- 浏览器证据：第一断点为点击动态实验卡时 `currentExperiment is not defined`，Promise catch 又在 `reportError` 报 `showToast is not defined`；初修后第二断点为 `fillDefinitionOverview` 先访问已被中性 shell 清空的 `overviewEnabledAgents/overviewAgentMeta`，再在后续 innerHTML 重建，顺序导致 null dereference。
- 静态证据/复现：`python -m pytest tests/architecture/test_final_e2e_regressions.py -q -k def_045`。三项红线分别覆盖十个原型符号所有权、基础 listener/state 所有权，以及“包内中性 shell 全部 id”与正式脚本全部 `$('<id>')` 选择器的通用 DOM contract；后者会审计所有被清空容器，不只写死本次两个 ID。
- 根因：生产 shell 为消除假数据/双监听删除了 inline prototype，但 `console-api.js` 原本只是增量 API adapter，仍借用 prototype 的状态变量和 UI 基础函数；两个脚本从未形成明确所有权边界。
- 修复要求：所有运行状态、UI primitive 与基础 listeners 必须由正式 bundle 自己定义/导入并使用单一 `state`；审计全部未定义符号和因删 prototype 消失的事件绑定，而不是只补浏览器首次遇到的两个；禁止恢复任何 inline prototype 脚本或演示监听器。
- 验收标准：两个 DEF-045 静态所有权测试与 DEF-040 no-inline 同时通过；空库创建实验及非空库动态实验卡均可进入详情；左侧导航、返回列表、结果 tabs、create/publish modal、wizard/template、Agent 编辑均可操作；整段旅程 console error/unhandled rejection 为 0，关键点击只触发一个正式监听器。
- 修复记录/独立回归：正式 bundle 已建立自身 state、toast/navigation/dirty/status/modal/wizard primitive，并迁移基础 listeners；变量红线按真实 `state.*` 所有权、按钮按包内 shell 的 `createExperimentBtn` 契约校准。renderer 已移除对被清空 overview 子 ID 的前置访问，统一在 parent innerHTML 中创建。`-k def_045` 三项静态测试通过。fresh Browser 新 tab `errors=[]`；动态 Draft Copy 卡进入详情、topbar/Agent 导航正确；真实 Run 结果工作台依次切换时间探索/Agent/对话/记忆/运行与制品，aria-selected 均正确且累计 console errors=[]；返回实验列表成功，新建实验 modal 可打开/关闭。静态 no-inline 仍通过，未恢复 prototype 脚本。

### DEF-046 — Experiment/Run 状态只在结果页局部同步

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-SYNC-001。
- 影响：Run 已从 QUEUED 进入 RUNNING/COMPLETED 时，实验列表、筛选计数、顶部状态、操作按钮、概览与 Run 历史仍保留旧快照；用户必须刷新页面，旧按钮分支还可能执行不适用的动作。
- 现场证据：同一未刷新页面显示旧 Run `fc6c233e-286…`、`已完成 1/运行中 0`，同时 API 已返回新 Run `00d96cbd-192…` 为 RUNNING，且 RunEvent 已持久化 QUEUED→STARTING→RUNNING。
- 根因：前端只在进入结果页后订阅单 Run SSE，handler 仅刷新结果 DOM；全局没有活动流，返回列表只切换页面，发布/控制动作也没有重新读取 Experiment。`latestRunId` 又与历史 Run selector 混为同一变量。
- 修复记录：新增全局 RunEvent cursor/SSE（携带 experiment_id/run_id），保留单 Run 高频流；前端增加统一 debounce reconcile、最新 Run/所选 Run 分离、列表与详情 generation guard、页面 focus/visible/online/pageshow 对账。发布、再次运行、暂停/恢复/取消与草稿修改均触发同一同步入口。制品补充 queued/running/retry 事件，结果页展示持久化 artifact job 状态；`result_rewound` 也进入刷新集合。
- 自动化回归：全局 cursor、前端活动流、Run 身份分离、发布后对账和制品生命周期测试通过；仓库全量 `130 passed, 1 warning in 51.43s`。
- 早期 Browser 回归：独立临时数据库与无 supervisor Web 上，页面不刷新时依次观察到 COMPLETED→QUEUED→COMPLETED 的列表状态、筛选计数、最新 Run 和 0/10→10/10 进度同步；实验概览在外部新建 Run 后自动更新为排队中、0/10，并同步“查看排队/取消排队”动作；Run 数为 2、默认选择真实 latest Run，外部创建 RESULT_BUNDLE 后“运行与制品”自动出现 QUEUED/0%。
- 最新独立自动化：`test_def_046_one_runtime_reconciles_external_run_lifecycle_everywhere` 直接抽取生产 `console-api.js` 的全局同步函数，在同一 JS runtime 内推进 DRAFT→QUEUED→RUNNING→COMPLETED，验证列表、筛选计数、顶部、概览、Run selector/history、动作与两个制品任务状态；关闭旧 EventSource 后迟到事件被 generation guard 丢弃，focus/visibility 重连仍保持终态。相邻动态服务测试写入 207 条全局事件，以 31 条 HTTP 页无缝重组，并在 SSE 消费 73 条后用 `Last-Event-ID` 精确续出余下 134 条；tail 后新增 COMPLETED 只收到该终态，不重放历史 RUNNING。两项定向均已通过。
- 最终 fresh Browser：在同一 tab 全程不刷新，初始列表为“全部 1/排队中 1”、卡片 QUEUED 0/10，概览顶部、最近运行和“查看排队/取消排队”一致。外部 POST RUNNING 后 2.2 秒内概览收敛为运行中 5/10；结果 Run selector 为“运行中 · 5/10”，暂停/取消可见、再次运行隐藏。外部 POST COMPLETED 后 2.6 秒内 selector 收敛为“已完成 · 10/10”，暂停/取消隐藏、再次运行可见；运行与制品展示 BUILD_REPORT、BUILD_REPLAY 均 QUEUED 0%。返回列表后“全部 1/运行中 0/排队中 0/已完成 1”，卡片已完成 10/10；全过程 `warn/warning/error=[]`。据自动化与真实同文档旅程转 VERIFIED。

### DEF-047 — Attempt/Artifact 日志与模型调用明细不可达

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-LOG-001、ROL-LOG-002、ROL-TRACE-001、ROL-SYNC-001。
- 影响：用户只能看到 Attempt 和模型调用聚合，无法定位某次进程的真实日志或单次模型失败；Artifact Worker 日志同样不可审计，运行故障无法在应用内闭环。
- 证据/复现：`python -m pytest tests/architecture/test_run_observability_lifecycle_redlines.py -q -k def_047`。OpenAPI 缺 Attempt list/log/read/SSE/download、Artifact log/read/SSE/download 和 model trace list/detail 全部九条合同；真实 UTF-8 byte-cursor 请求返回 404。
- 期望：日志严格绑定 Run+Attempt/ArtifactJob，以原始 byte cursor 无重复无丢失分页；支持断点续流、结束终止/心跳、下载；模型 trace 可按 Attempt/purpose/status 浏览明细并限制大字段。
- 实际：没有上述服务与路由；当前跨 Run 请求只得到 FastAPI 无路由的普通 404，不是经过所有权校验的 `ATTEMPT_NOT_FOUND`，不能视为隔离通过。
- 开发中组件/协议审计：底层 `LogService.model_traces/model_trace_payload` 已用真实 ModelTraceWriter+cursor 验证 Attempt/purpose/status 过滤、重试/延迟明细、payload 延迟分页及 Authorization/api_key 嵌套脱敏通过；合法日志路径的 terminal/truncated/rotated/missing 状态也已直接通过。新增真实 HTTP/SSE 复验已证明：`Last-Event-ID=<file_id>:<byte_offset>` 从追加点只续出新增 UTF-8 字节；替换文件后使用旧事件 ID 返回 409 `ATTEMPT_LOG_ROTATED` 与 reset_cursor=0，不泄漏新文件正文；空闲流发出 keepalive 后在 Attempt 终态 EOF 主动关闭；Artifact job stream/download 与跨 Run 404 使用同一所有权边界。以上仍不替代 UI auto-follow、切 Run 关闭旧流和 fresh Browser 闭环。本机不允许创建真实 file symlink，该项保持环境未覆盖并由确定性路径组件测试兜底。
- 修复要求：只从数据库 `log_path` 解析，resolved path 必须留在所属 Run 的 `logs/`；拒绝绝对路径、`..`、symlink、跨 Run ID；缺失、截断和终态采用稳定 envelope，响应不得泄露服务器路径。裁决后的错误合同固定为：跨 Run attempt/job 返回 404 域 `NOT_FOUND`；DB-owned 路径不变量损坏返回 500 `RUN_STORAGE_INTEGRITY_ERROR`；合法路径文件缺失返回 410 `<RESOURCE>_LOG_MISSING`；同 file_id cursor 越界或 file_id 轮转返回 409。Artifact log 使用相同 reader/protocol。
- 验收标准：UTF-8 跨块测试中每个 `next_cursor` 等于累计实际消费的源字节数，最终累计字节和拼接文本分别严格等于原文件；路径、symlink、跨 Run、截断、SSE cursor/终止与切 Run 测试全部通过。路由存在或返回占位 200 不能单独关闭本缺陷。
- 最新独立复验：`-k "def_047"` 的普通 GET、append-stable file_id、替换/截断、SSE Last-Event-ID、heartbeat/terminal、Artifact log 与跨 Run 共用协议均通过。fact-rich fresh Browser 在“运行与制品”实际选择 Attempt，读取三行真实中文 UTF-8 日志；搜索、级别过滤、自动跟随、暂停和下载控件均可达。切换 completed Run 后展示其独立 READY Replay/Report 与 job log，未出现旧 Run 日志回写，累计 `console errors=[]`。路径组件/跨 Run/轮转完整性合同保持通过，真实 symlink 的剩余未覆盖项只保留在 DEF-061/063，故本项转 VERIFIED。

### DEF-048 — “运行与制品”只有聚合卡，没有运行可观测性交互

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-LOG-001、ROL-TRACE-001、ROL-CHK-001、ROL-CHK-002、ROL-SYNC-001。
- 影响：用户无法切 Attempt、自动跟随/暂停日志、搜索和级别过滤，也无法浏览检查点、Trace 明细或错误态；当前高保真交互不足以指导真实实施。
- 证据/复现：`test_def_048_operations_ui_has_real_logs_checkpoints_traces_and_stale_guards` 首个失败为 `operationsSubtabs/checkpointDetail` 等二级工作台合同缺失；正式脚本没有日志 SSE 生命周期、日志/检查点请求代次和 AbortController。
- 期望：保持现有结果一级页签，在“运行与制品”内提供运行日志、模型调用、系统事件、检查点、结果产物二级页签；所有数据来自当前 Run selector。
- 实际：只渲染模型聚合、Attempt 摘要和 Artifact 卡片。
- 追加对抗证据：二级工作台骨架与基础 Attempt/SSE 交互出现后，轮转/截断 `error` handler 仍不识别 reset 语义、不关闭旧 EventSource，也不清理旧 file_id/cursor；浏览器会以旧 Last-Event-ID 反复自动重连。该失败由 `test_def_048_log_stream_error_cannot_enter_an_automatic_reconnect_loop` 固定，仍属本缺陷的错误态与 stale-stream 范围。
- 验收标准：静态 DOM/正式脚本合同、可执行 JS 的 Attempt 切换/auto-follow/过滤/搜索/下载/错误态、迟到响应丢弃与 fresh Browser 旅程共同通过；不接受只增加说明文字或假数据占位。
- 最新独立复验：operations DOM、轮转错误停止自动重连、Attempt renderer 唯一拥有 `data-attempt-id`、周期发现新 Attempt 且不强切用户选择等自动化已通过。fresh Browser 已实际完成 Attempt selector→中文日志、搜索/级别/auto-follow/暂停/下载、模型调用明细、Checkpoint 详情与制品/job log 旅程；切 Run 无 stale 回写且 `console errors=[]`，故转 VERIFIED。

### DEF-049 — 无有效恢复点的 PAUSED/FAILED/INTERRUPTED 仍可入队恢复

- 严重度/状态：P0 / VERIFIED
- ROL 映射：ROL-REC-001。
- 影响：`recoverable_step=0` 的失败或中断 Run 会作为“恢复”重新排队，但 worker 没有权威 checkpoint；这会把 fresh start 冒充连续恢复，破坏结果、RNG、对话和存储连续性。
- 证据/复现：`test_def_049_resume_requires_a_verified_authorized_checkpoint` 对 PAUSED、FAILED、INTERRUPTED 三种状态均预期 `RUN_NOT_RECOVERABLE`，实际全部未抛异常并转为 QUEUED。
- 根因：`RunService.resume_paused()` 只校验状态集合，没有先校验 `recoverable_step > 0` 和权威 bundle 可用性。
- 修复要求/验收标准：三种状态只有数据库授权 step 大于 0 且对应 bundle 完整校验通过才允许排队；失败必须保持原状态和历史错误，不得先 rewind 或创建队列行。COMPLETED/CANCELLED 继续禁止原地恢复。
- 修复记录/独立回归：恢复入口现先验证数据库授权 step 与精确物理 bundle，再进行任何 rewind/queue 变更；PAUSED/FAILED/INTERRUPTED 的零恢复点、损坏 bundle 与 projection 不先变更原状态。`-k "def_049"` 原断言独立通过。

### DEF-050 — 检查点不可观测，选中旧检查点却导出 LATEST

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-CHK-001、ROL-CHK-002、ROL-ART-002。
- 影响：用户无法分辨 retained/pruned/invalid/recoverable；请求导出某个历史检查点时得到另一个步骤，下载内容与选择不一致，可能用于错误审计或错误分支。
- 证据/复现：`test_def_050_checkpoint_api_distinguishes_verified_invalid_and_recoverable` 基线列表请求实际 404；`test_def_050_checkpoint_zip_uses_the_selected_verified_step` 基线指定 `checkpoint_step=1`，ZIP 唯一根目录实际为 `step-000002`。开发中服务出现后继续对抗：PAUSED Run 的 DB `recoverable_step=1` 但物理 bundle 缺失时，列表实际标为 `PRUNED`；权威恢复边界缺失属于完整性异常，必须为 `INVALID/CHECKPOINT_AUTHORIZED_BUNDLE_MISSING`，不能伪装成正常 retention。
- 根因：没有 DB+磁盘合并的 Checkpoint 查询服务；`ArtifactBuilder._checkpoint_bundle()` 忽略 job parameters，无条件 `read_latest()`。
- 修复要求：列表必须以完整 bundle 校验后才给 RECOVERABLE；详情提供摘要、Agent/对话/storage manifest 和有限 raw section preview，不允许任意 storage 文件路径。ZIP 必须解析并验证所选 step，只包含一个 bundle，拒绝 symlink/绝对路径/`..`。
- 验收标准：有效 step1+损坏 step2、DB 投影缺物理目录、retention pruned、future orphan、跨 Run checkpoint 和选步 ZIP 对抗均通过；响应不泄露物理路径。
- 开发中组件审计：选中旧 step 的单 bundle ZIP 已用原断言通过；Checkpoint detail 的 Agent/对话/storage 文件计数、受控 manifest、embedding 不直出、state raw byte preview 白名单与跨 Run 不可见直接服务测试通过；HTTP 列表路由也已用有效/损坏 bundle 通过。active recoverable boundary 缺物理 bundle 已修为 `INVALID/CHECKPOINT_AUTHORIZED_BUNDLE_MISSING` 并通过原失败断言。UI detail、HTTP preview 错误 envelope、future orphan 列表语义和 Browser 尚未完成，状态保持 ASSIGNED。
- 最新独立复验：DEF-050/057 的列表、detail、preview 分页、tamper envelope、retained/pruned/recoverable、选中单 ZIP 与静态 UI 字段/继续加载合同均通过，通用 ArtifactJob 的 checkpoint/source 双权威问题也已由 DEF-062 验证修复。fresh Browser 实际打开 Step 2 `RECOVERABLE/VALID`：列表显示 Attempt/hash/size/files，详情显示 coord/action/current/schedule、conversation、storage docstore+index、文件 manifest；state preview 显示 coord/action/schedule/RNG/virtual_time JSON，并提供继续加载和所选 Step ZIP 入口，故转 VERIFIED。

### DEF-051 — Replay 有两个不兼容的 V1，产物幂等身份遗漏 source step

- 严重度/状态：P0 / VERIFIED
- ROL 映射：ROL-ART-001、ROL-ART-002、ROL-RPL-001。
- 影响：`compress.py` 与 Web Artifact Worker 都声称 `schema_version=1`，字段语义却不同；播放器或导出消费者无法可靠解析。Step 10 partial 成功后，Step 100 final 会被旧 SUCCEEDED job 命中，或覆盖同一 Artifact identity。
- 证据/复现：`test_def_051_replay_has_one_v2_schema_and_source_locked_identity` 实际 schema 为 1 且缺 revision/world/agents/partial 等合同；`test_def_051_artifact_dedup_identity_changes_with_source_step` 在 available step 从 10 增至 100 后仍返回相同 job id，parameters 中也没有 source_step/generator_version。
- 根因：存在两套 Replay builder；job `parameters_hash` 只哈希用户参数，Artifact 唯一键只有 Run/type/name/generator version，builder 又读取构建时“当前 available step”。
- 修复要求：建立一个 Replay V2 DTO/validator/builder；compress、Web job 和自动完成复用它，Legacy movement 只经 adapter。创建 job 时冻结 source_step、partial/final、generator_version，并进入 job/Artifact 身份；builder 只读冻结边界。
- 验收标准：V2 必需元数据、step path/行动/对话/事件/checkpoint/attempt 边界完整；10→100 生成不同不可变制品，并发相同 source step 仍幂等；两个 Run 不共享制品。
- 修复记录/独立回归：Web Artifact、live window 与 legacy/compress 已统一 Replay V2 合同；严格 validator 同时检查 revision/definition/world、Agent sprite、OBSERVED path/action、conversation/domain event、`memory_deltas`、`schedule_revisions`、checkpoint/Attempt boundary。创建时冻结 source_step/partial/generator identity；延迟构建 partial10 后 final100 得到两个不同物理文件，旧文件 hash/字节不变。`python -m pytest ... -k "def_051 or def_052"` 独立结果 `7 passed`。后续发现的 DB frame 完整性绕过单列 DEF-063，不回退本缺陷已验证的 schema/identity 范围。

### DEF-052 — COMPLETED 不自动排队 Replay 与 Report

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-ART-002。
- 影响：Run 已完成但结果页没有最终锁定 Replay/Report，必须依赖用户猜测并手动触发；自动归档生命周期断裂。
- 证据/复现：`test_def_052_completed_run_automatically_queues_replay_and_report` 让真实 scheduler attempt 达到 requested step 并 `finish_worker(exit_code=0)`，数据库 ArtifactJob 类型实际为空，期望至少 BUILD_REPLAY 与 BUILD_REPORT。
- 修复要求/验收标准：COMPLETED 状态事务之后幂等创建冻结 final source step 的 Replay/Report；调度失败可重试且不回滚 Run 终态；重复 finish/reconcile 不重复活动任务。RESULT_BUNDLE 继续只按需创建。
- 修复记录/独立回归：顺序 finish 与八线程并发 finish 均只产生一对 final BUILD_REPLAY/BUILD_REPORT，source_step/partial/generator_version 已冻结；同上组合定向 `7 passed`。

### DEF-053 — 文本 Artifact 预览在 UTF-8 byte 边界永久损坏字符

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-LOG-001（统一 byte cursor 语义）、ROL-CHK-002（raw preview）。
- 影响：中文、emoji 等多字节字符跨预览块时被替换为 `U+FFFD`；cursor 又前进到原始字节之后，后续页无法恢复原字符。
- 证据/复现：`test_def_053_artifact_preview_preserves_utf8_at_byte_boundaries` 用 14-byte 边界切在“中”的编码内部，首块实际为 `{"message":"�`。
- 根因：API 固定读取 `limit_bytes` 后直接 `decode(errors="replace")`，没有把不完整 UTF-8 尾部留给下一页或调整实际消费 cursor。
- 修复要求/验收标准：按最大字节预算读取，但只消费完整 code point，`next_cursor` 是实际消费的源 byte 数；逐页无 `�`，拼接文本和总消费字节严格等于原文件。非法 UTF-8 应返回明确 encoding 状态，不得静默改写事实。
- 修复记录/独立回归：统一 UTF-8 byte window 仅消费完整 code point，14-byte 多页拼接与原文严格相等；`-k def_053` 独立通过。

### DEF-054 — 时间探索仍是 DOM 圆点伪地图且没有 10k Step 窗口协议

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-RPL-002、ROL-RPL-003、ROL-SYNC-001。
- 影响：当前 canvas 未承担地图/Agent 渲染，正式脚本仍向 `#resultMap` 追加绝对定位圆形按钮；timeline 首次只取有限数据，却把 slider 上限设为全部 available step，长 Run 后段显示旧位置。没有真正 tilemap、sprite、镜头、跟随、图层或增量窗口。
- 证据/复现：`test_def_054_replay_player_is_packaged_external_and_not_dom_dot_fallback` 实际没有包内 `replay-player.js` 且仍存在 `.map-agent`/DOM button renderer；`test_def_054_replay_steps_window_10k_never_returns_the_whole_run` 向 SQLite 批量写入真实 10,000 个 RunStep 后，请求标准 window route 返回 404。
- 修复要求：正式外部播放器模块消费 Replay V2；tilemap/tileset/sprite 包内可达，不恢复 inline prototype。manifest+step window 返回 run/source/result_version/next cursor；切 Run 取消旧请求并拒绝迟到 response，运行中新 available step 增量扩展。
- 验收标准：可执行 JS 覆盖加载、播放/暂停、前后 step、速度、上限、stale Run；10k 数据每次最多 100 step 且响应受限；fresh Browser 真测地图、移动、镜头/跟随、checkpoint/对话/event/Attempt 标记、Inspector、切 Run 和增量步骤，`console errors=[]`。
- 最新独立复验：Replay V2 artifact/live window 统一 validator、真实 10k frame 抽样、limit/next cursor、运行中 available/result_version 扩展，以及“窗口首 step 不是伪 Attempt boundary、真实新 Attempt 首 step 才是 boundary”均已通过。正式 `replay-player.js`/包内 Phaser、完整控制器/图层/Inspector DOM、切 Run destroy+abort、Node 可执行外部模块也已补齐；部署红线真实 `pip wheel`、检查 wheel members、`pip install --target` 并在脱离源码 cwd 后启动 Web GET Phaser/player/tilemap/PNG，不是搜索 metadata 字符串。`-k def_054` 独立为 `9 passed`。最终 fresh Browser 从 running Run（READY/Step 2/乔治/follow）切到同 Revision completed Run（READY/Step 3），再切回 running；真实 tilemap/sprite 均在卡片内，选择、坐标、对话、记忆、日程与对应 Run/Step 一致，三段累计 console warn/warning/error 均为空，故转 VERIFIED。

### DEF-055 — 日志 byte window 每页物理读取整个文件

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-LOG-001。
- 影响：新 reader 虽对外返回有限 byte cursor，但内部每次调用 `handle.read()` 把完整日志载入内存；长日志翻页产生 O(file_size × page_count) I/O，多实验并发查看时可拖垮 Web 进程。
- 证据/复现：`test_def_055_log_byte_window_never_reads_the_entire_file` 创建约 4 MB 日志并用 guarded file object 拒绝无界 read；`read_utf8_window()` 首次物理读取即以 `size=-1` 失败。
- 期望：使用 seek(cursor) 后只读取 `limit_bytes + UTF-8 边界余量`；tail 先由 stat 计算起点，同样只读有限窗口。哈希/轮转 identity 不得要求扫描文件内容。
- 实际：`with path.open("rb") as handle: value = handle.read()`，随后才在内存 bytes 上切片。
- 验收标准：任何页的底层 read 总量不超过请求限制加 4 字节 UTF-8 余量；任意中间 cursor、tail 和 EOF 的文本/byte cursor 语义仍与 DEF-047 一致。
- 修复记录/独立回归：reader 已改为 seek+有界读取，4 MiB guarded file 不再出现 `read(-1)`；与 DEF-047 byte cursor 组合定向通过。

### DEF-056 — byte page 切分导致长日志行永久缺失或重复为多条 record

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-LOG-001。
- 影响：单条中文/emoji 日志跨多个 byte page 时，用户要么看到若干伪记录，要么整条记录永久消失；终态无换行的最后一行同样可能不展示。错误搜索与级别过滤据此产生错误结论。
- 证据/复现：`test_def_056_one_utf8_log_line_spanning_pages_is_one_record` 的 raw `content` 逐页拼接严格等于源文件，但所有 `records` 合并后实际为空；`test_def_056_terminal_line_without_newline_is_emitted_exactly_once` 同样实际为空。`test_def_056_tail_starting_mid_line_discards_only_the_leading_fragment` 已通过，证明 mid-line 丢弃策略只覆盖较短尾部场景，不能替代跨多页缓冲。
- 根因：服务为每页标记 `starts_mid_line/ends_mid_line` 并丢弃不完整片段，正式 UI 却只执行 `state.logRecords.push(...page.records)`，没有跨页 line buffer；最后一页从 mid-line 开始时前缀再次被丢弃，永远无法组成记录。
- 修复要求：byte cursor/content 继续保持原始无损；在服务或正式客户端建立有上限的跨页 record assembler，首屏 tail 从 mid-line 开始时只丢弃首个前缀片段，后续尾片段必须缓存；`terminal=true+eof=true` 时无结尾换行的 pending line 只 flush 一次。超长单行必须有明确大小上限/错误，不得无界读。
- 验收标准：长 UTF-8 单行、首屏 mid-line 后完整下一行、terminal 无换行三种 fixture 的用户可见 record 分别严格为 1/1/1，raw byte 拼接仍等于源文件；SSE 与普通 GET 使用同一组装语义，重连不得重复 flush。
- 修复记录/独立回归：跨多 byte page 的 record assembler、mid-line 首段舍弃与 terminal pending line 单次 flush 均用原三项对抗断言通过；raw content/byte cursor 仍无损。

### DEF-057 — Checkpoint UI 只显示计数，内容与校验事实不可审计

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-CHK-001、ROL-CHK-002、ROL-SYNC-001。
- 影响：API 已能返回 Agent、conversation、storage 和 manifest，但高保真详情只显示 Agent/对话/存储数量；用户看不到坐标、动作、日程、文件 hash、validation reason，也无法继续读取超过 32 KiB 的 preview。
- 证据/复现：`test_def_057_checkpoint_ui_exposes_full_detail_and_preview_pagination` 首个失败为列表不渲染 `attempt_id`；同一断言继续要求 bundle hash、status/error、Agent coord/action/schedule、conversation items、storage groups、manifest/validation 和 `next_cursor` 继续加载。服务侧 `test_def_050_checkpoint_http_detail_preview_and_tamper_envelopes` 与 PRUNED/RETAINED/RECOVERABLE 三态测试已通过，证明缺口位于 UI 事实呈现而非数据缺失。
- 修复要求/验收标准：列表与详情完整覆盖上述字段；raw preview 明确显示当前 byte range、总大小和“继续加载”，切 Run/刷新时取消旧请求并拒绝迟到 detail/preview；INVALID/PRUNED/成员篡改使用可操作错误态，不渲染成空数据。静态合同、可执行 JS 与 fresh Browser 同时通过后才可关闭。
- 最新独立复验：静态 DOM/JS 的 Attempt、hash/status/error、Agent 坐标/动作/日程、conversation、storage groups、manifest/validation 与 32 KiB preview 继续加载合同已通过。fresh Browser 的 Step 2 真实详情完整呈现上述字段、对话文本、docstore/index 与 state JSON，继续加载/ZIP 可达；切 Run 无 stale、console errors 为空，故转 VERIFIED。

### DEF-058 — Model trace 与系统事件固定截断在首 200 条且 trace payload 不可打开

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-TRACE-001、ROL-SYNC-001。
- 影响：高频 Run 的第 201 条以后模型调用/系统事件不可达；模型行不能点击打开受限 payload，用户仍无法从聚合或列表追到具体失败输入/输出。
- 证据/复现：`test_def_058_trace_detail_and_operation_collections_are_pageable` 实际没有 `modelTraceRows` 点击 listener/detail route；`loadModelTraces(...limit=200)` 不消费 `next_cursor`，系统事件固定 `/events?limit=200` 也不翻页。
- 修复要求：trace 行点击以当前 run_id+trace_id 请求 detail，支持 payload byte cursor/继续加载和脱敏；trace 列表消费 cursor，系统事件消费 `next_after_id`，提供加载更多或窗口化滚动。任一迟到页必须同时校验 generation、Run、Attempt 和 AbortSignal。
- 验收标准：构造 201+ trace 与 201+ event，末项可从 UI 到达；点击 trace 展示真实 detail/payload 且密钥不出现；切 Run 后旧列表页/detail 不回写；fresh Browser console errors=[]。
- 最新独立复验：五项 DEF-058 自动化已全过：>16 KiB payload 逐 byte window 无损拼接且密钥脱敏、EOF 后 append 从旧 byte cursor 只增量读取、筛选才归零、旧 Attempt response 丢弃、250 条 event 周期刷新不回退以及 trace/detail/load-more 静态合同。fresh Browser 实际显示两条 `PHYSICAL_ATTEMPT/LOGICAL_END`，点击行后明细呈现 `purpose=plan`、Qwen、420 ms、token 统计与已脱敏 payload；切 Run 无 stale、console errors 为空，故转 VERIFIED。

### DEF-059 — SSE 对已有 backlog 每 64 KiB 固定休眠 500ms

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-LOG-001、ROL-SYNC-001。
- 影响：12 MiB 既有日志约需额外 96 秒才能追到 EOF；刷新或重连时用户长期停留在旧日志，多个并行 Run 又会积累大量无意义定时器。
- 证据/复现：`test_def_059_sse_drains_non_eof_backlog_without_per_page_sleep` 创建 12,000 行终态日志，服务发送至少三个 `log` page 后一个 `eof`，实际在三个非 EOF page 间调用 `sleep(0.5)` 三次。
- 根因：`_tail_log` 无论刚消费了 backlog 还是已追到当前 EOF，都会执行固定 poll sleep。
- 修复要求/验收标准：只在 `eof=true && terminal=false` 的 appendable caught-up 状态进入 poll/heartbeat；`eof=false` 时立即读取下一页，同时通过响应背压避免无界生产。定向测试 sleep 次数为 0，并对大 backlog 记录追尾耗时与内存上限。
- 修复记录/独立回归：非 EOF backlog 连续追赶，只有已追到 appendable EOF 才 poll；12,000 行终态 fixture 的非 EOF sleep 次数为 0，原断言独立通过。

### DEF-060 — Legacy compressed movement 采样丢失 Agent 状态与结构化对话

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-ART-001、ROL-RPL-003。
- 影响：真实旧运行导入后结果表声称已有多个 step，但多数采样 step 没有 Agent 行；位置、行动、虚拟时间及 conversation/message 语义与源 movement.json 不等价，Legacy Replay 会展示空城或错误状态。
- 证据/复现：`test_def_060_real_legacy_movement_adapter_preserves_state_time_and_conversation` 直接导入仓库 5.8 MiB `generative_agents/results/compressed/example/movement.json`；源 frame 是 delta，step2 对应累计到 frame60 后应有 25 个 Agent，实际 `RunAgentStep` 为 0。源文件还在 `all_movement.conversation` 保存结构化对话，但 importer 只从 checkpoint 目录的 `conversation.json` 读取。
- 根因：`_legacy_definition_and_samples()` 对 `numeric[::frame_stride]` 的单帧字典直接取样，没有逐 frame carry-forward；`_legacy_conversations()` 完全忽略 compressed movement 的 conversation 字段。
- 修复要求：adapter 先按 frame 顺序应用 delta，采样时输出当时完整 Agent state；解析 compressed conversation 并以 source virtual time/participant/message sequence 投影。必须保留 `path_source=RECONSTRUCTED` 和 `snapshot_complete=false`，不得伪称原生观测。
- 验收标准：真实 fixture step2 精确得到 25 个 Agent，坐标/address/action 与累计 frame60 一致、时间为 06:10；首条“早上好”对话参与者/时间/消息顺序一致；源 movement hash 前后不变。
- 修复记录/独立回归：compressed adapter 已逐 frame carry-forward 后采样，并解析 `all_movement.conversation`；真实 5.8 MiB shipped fixture 的 25 Agent、06:10 时间、坐标/address/action、首条“早上好”对话与源 hash 原断言通过。与 DEF-062 组合定向结果 `3 passed`。

### DEF-061 — RunArtifact 持久化路径与内容摘要未作为读取前置完整性边界

- 严重度/状态：P1 / ASSIGNED
- ROL 映射：ROL-ART-002、ROL-LOG-001、ROL-SYNC-001。
- 影响：数据库若出现 absolute/`..`/symlink 路径或 READY 文件被替换，preview/download 可能读取不应授权的文件或把已篡改内容当作 immutable artifact；跨 Run 隔离与 ETag 均不可信。
- 首次证据/复现：`test_def_061_artifact_preview_and_download_enforce_persisted_storage_integrity` 中 absolute、含 `..`、size 改变、同 size SHA 改变均实际返回 200 和文件正文，期望 409/500 完整性错误；跨 Run 同 artifact id 已正确 404。
- 修复要求：所有 preview/download 先以 RunStorageBoundary 校验相对路径、area 与完整 symlink chain，再流式核对持久化 size+sha256；不变量损坏返回稳定 `RUN_STORAGE_INTEGRITY_ERROR` 或 `ARTIFACT_CONTENT_INTEGRITY_ERROR`，不得泄露物理路径。
- 最新独立复验：absolute/`..`/size/SHA 四项已按原断言通过，跨 Run 保持 404；原 final-file 与 intermediate-directory symlink 加新增“所属 Run 内的 directory symlink 指向另一 Run artifacts”共三项，在当前 Windows 主机均因权限诚实 skip，故不能标记 VERIFIED。新增跨 Run 测试同时覆盖 preview+download、错误不泄 `var_dir`/target，并在 unlink 后断言目标 bytes 未变。能力审计确认 Developer Mode 注册项未启用、当前 token 无 `SeCreateSymbolicLinkPrivilege`，file/directory symlink 均以 `UnauthorizedAccessException: Administrator privilege required` 失败；WSL 无已安装发行版且 Docker executable 不存在。无需提权的 NTFS Junction 已由 DEF-072 验证，但不能冒充原生 symlink。
- 验收标准：三项真实 Artifact symlink、DEF-047 日志 symlink 与 DEF-063 三项 Replay symlink 必须在严格 runner 中精确收集为 7 tests、0 failures/errors/skips；本缺陷三项均拒绝，cross Run 仍 404，响应不含 var_dir/target。`GA_REQUIRE_NATIVE_SYMLINK_TESTS=1` 下能力不足必须 fail；只有 Linux 与 Windows 两个 Required CI check 均绿后才能复验状态。

### DEF-062 — 通用 Checkpoint ArtifactJob 接受两个不一致的 source step

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-CHK-002、ROL-ART-002。
- 影响：调用通用 `/artifact-jobs` 可令 job 身份/去重字段声明 `source_step=2`，实际 ZIP 却由 `checkpoint_step=1` 构建；审计记录、logical identity 与下载内容互相矛盾。
- 证据/复现：`test_def_062_generic_checkpoint_job_rejects_source_step_mismatch` 对已验证 step1 提交 `{checkpoint_step:1, source_step:2}`，实际 202 并持久化 QUEUED job；期望 422 `CHECKPOINT_SOURCE_STEP_MISMATCH`。
- 根因：generic create 已调用 Checkpoint full validation，但随后优先取并 pop 用户 `source_step`，没有与 `checkpoint_step` 比较。
- 修复要求/验收标准：CHECKPOINT_BUNDLE 只允许一个权威 step；显式两值必须严格相等，否则在创建任何 job/event 前返回 422。损坏 bundle 经 generic endpoint 返回 409 `CHECKPOINT_INVALID` 的相邻原断言已经通过，必须保持。
- 修复记录/独立回归：generic service 现于 full validation 与 job/event 持久化前比较两 step；mismatch 返回 422 `CHECKPOINT_SOURCE_STEP_MISMATCH`，损坏 bundle 仍为 409 `CHECKPOINT_INVALID`。两项原红线与 DEF-060 组合 `3 passed`。

### DEF-063 — Replay manifest 与 ArtifactBuilder 绕过 DB-owned frame 完整性

- 严重度/状态：P0 / ASSIGNED
- ROL 映射：ROL-ART-002、ROL-RPL-001、ROL-RPL-003。
- 影响：live window 能发现损坏，但同一 Run 的 manifest 仍 200 宣称 Replay 可用，ArtifactBuilder 又按 canonical 文件名绕过 RunStep.frame_path/frame_sha256 生成 READY Replay。用户可能下载已篡改、跨 Run 或与数据库提交事实不同的最终产物。
- 证据/复现：`test_def_063_replay_frame_integrity_blocks_manifest_window_and_artifact` 分别把 frame_path 改为 absolute、含 `..`、指向另一 Run，重压缩一个 envelope 内部自洽但内容已改的 frame，或仅修改 DB SHA。五种情况下 `/replay/steps` 均正确返回 500，但 `/replay/manifest` 实际 200，ArtifactBuilder `build_error=None` 且产生一个 READY REPLAY；定向结果 `5 failed, 2 skipped`，skip 仅为本机 symlink 权限。
- 根因：ReplayService.steps 使用 DB row+RunStorageBoundary+SHA reader；manifest 不验证 source 范围 frame；ArtifactBuilder._replay_document 直接按 `FrameStore.path_for(step_no)` 读取，三生产者没有共享权威 reader。
- 修复要求：manifest、window、artifact 复用同一 DB-owned verified frame reader，校验 relative path/symlink、压缩字节 SHA、envelope run/attempt/step/time 语义；可按 DB sha+stat+result_version 做安全缓存以控制 10k 成本，但缓存键变化或文件 stat 变化必须重新验证。
- 验收标准：七种 mutation 均不生成 manifest/window/READY artifact，使用稳定 409/500 完整性 envelope且不泄物理路径；合法 10k window 仍满足 100-step 上限与性能预算；两个 Run 绝不混用 frame。
- 修复记录/独立回归：manifest、window 与 ArtifactBuilder 已切到共享 verified reader；absolute、`..`、直接跨 Run、内容重压缩、DB SHA 五项原失败断言已通过。原 final-file/intermediate-directory symlink 加新增“所属 Run frame path 的 file symlink 指向另一 Run frame”共三项，在当前主机均因权限诚实 skip；新增项对 manifest/window/builder 三消费者同时断言 409/500、无 READY、无路径泄漏，并在 unlink 后确认目标 frame bytes 未变。严格环境变量模式下同测试已从 skip 变为明确 failure；本 P0 在 Linux/Windows 原生 symlink gate 实际 0 skip 前保持 ASSIGNED。

### DEF-064 — Replay 在自定义 Browser 环境使用 Phaser.AUTO，播放器无法启动

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-RPL-002、ROL-SYNC-001。
- 影响：用户进入“时间探索”后播放器停在 `LOADING`，Agent 选择器为空，真实 tilemap、sprite、移动、时间轴与 Inspector 全部不可用；静态资源存在和 Node 合同通过无法覆盖此运行时断点。
- 证据/复现：在 fact-rich production app（同一实验含 completed 3/3 与 running 2/3 两条 Run、内置 `the-ville` 资源、真实 frame/projection）用 fresh in-app Browser 进入时间探索，Toast 为 `Must set explicit renderType in custom environment`；修复前 `replay-player.js` 的 Phaser config 使用 `type: PhaserRuntime.AUTO`。可执行红线：`python -m pytest tests/architecture/test_run_observability_lifecycle_redlines.py::test_def_064_replay_player_uses_an_explicit_renderer_in_custom_browser_environment -q`。
- 期望：正式播放器显式选择 `PhaserRuntime.CANVAS` 或 `PhaserRuntime.WEBGL`，不得回退 `AUTO` 或旧 DOM 圆点伪地图；加载后 Agent 下拉、地图、sprite 和事实窗口均可用。
- 实际：修复前 custom environment 拒绝 AUTO renderer，播放器初始化失败。
- 根因：播放器沿用普通浏览器的 Phaser 自动 renderer 探测假设，未把 in-app Browser 的 custom environment 纳入运行时合同。
- 修复要求/验收标准：源码红线拒绝 `PhaserRuntime.AUTO`；fresh Browser 重启后完成真实 tilemap/sprite、Agent 移动、play/pause/step/speed、camera/follow/layers、timeline markers、Inspector、切 Run teardown/stale guard，累计 `console errors=[]`。不得通过恢复 inline prototype 或 DOM fallback 绕过。
- 修复记录/独立回归：当前共享树已改为 `PhaserRuntime.CANVAS`，新增静态红线独立通过。最终 fresh Browser 全新 tab 首屏直接进入 READY，显示 Step 2、乔治与跟随状态，地图/Agent/事实窗口正常，console warn/warning/error 均为空；继续完成两次 Run 切换仍保持 READY，故转 VERIFIED。

### DEF-065 — 切换 Run 后 Replay Agent 选择器与 Inspector 事实所有权分裂

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-RPL-002、ROL-SYNC-001。
- 影响：同一 Revision 的两条 Run 间切换时，Agent 下拉回到“选择 Agent”，Inspector 却继续显示上一 Run 中已选择 Agent 的新 step 事实；用户无法判断 Inspector 当前属于哪个选择与哪个 Run。若新 Run 不含该 Agent，旧 Inspector 还可能直接泄入新的结果上下文。
- 证据/复现：fresh Browser 在 running Run 选择乔治后切换 completed Run，canvas `READY` 且 3/3 正确，但 `replayAgentSelect.value === ''`，Inspector 仍显示乔治 Step 3。可执行红线 `test_def_065_switch_run_reconciles_replay_selection_and_inspector` 模拟真实 select 在重建 options 后清空 value，分别验证同 Revision/Agent 存在时恢复选择，以及 Agent 不存在时 selection/player/Inspector 同时清空；修复前稳定失败。
- 期望：同 Revision 且目标 Run manifest 仍含 selected agent 时，select value、player selected key 与 Inspector 原子恢复；Revision 不同或 Agent 不存在时，三者原子清空。所有异步回调继续校验 run_id、generation 与 abort。
- 实际：修复前重建 `<option>` 只清空 DOM value，没有协调 player selected key、持久选择身份与 Inspector。
- 根因：Run teardown、manifest options 重建与 Inspector 更新分属三个回调，缺少 `(revision_id, agent_key)` 所有权身份和统一 reconcile。
- 修复要求/验收标准：选择身份至少绑定 revision+agent；目标 manifest 验证后才能恢复；不存在时清空 select、`selectAgent(null)` 与六项 Inspector；切 Run 的迟到 onStep/onAgent 不得回写。fresh Browser 两个方向切换后选择与事实一致且 `console errors=[]`。
- 修复记录/独立回归：共享树已加入 `resolveAgentSelection`、选择 revision 身份和 `clearReplayInspector`；独立可执行 DOM 红线为 `1 passed`。最终 fresh Browser 在 running→completed→running 的同 Revision 旅程中始终保持 `resident-001`：completed Inspector 为 `[89,18]`/第 3 步，切回后恢复 `[88,18]` 及对应对话、记忆、日程，选择器与事实所有权一致且 console 为空，故转 VERIFIED。

### DEF-066 — Phaser Canvas 脱离结果地图容器并使用错误的 Tiled layer 名

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-RPL-002、ROL-SYNC-001。
- 影响：tilemap 虽实际绘制，但唯一 canvas 被挂到 `body.readonly-mode`，尺寸约 1264×720，`#resultMap` 卡片内部仍是空绿；地图在 Inspector 下方横跨页面并破坏结果工作台布局。同时 `Interior Furniture L2` 少了源 tilemap 中有意义的尾空格，产生 `Invalid Layer` console warning。
- 证据/复现：fresh Browser 检查 DOM/几何得到 canvas parent 为 body、卡片无 renderer；控制台记录 layer warning。可执行红线：`test_def_066_phaser_canvas_is_owned_and_clipped_by_the_result_map` 要求 top-level Phaser config 的 `parent` 与显式 `canvas` 同时归属 `resultMapCanvas.parentElement`，禁止只把 parent 放进 scale 子配置，并冻结真实 layer 名 `Interior Furniture L2 ` 与容器 overflow。
- 期望：唯一 renderer canvas 是 `#resultMap` 受控后代，offset/client 尺寸不超过容器且不越过卡片裁剪边界；tilemap 与 sprite 在卡片内可见；所有真实 layer 创建无 warning。
- 实际：修复前 Phaser custom runtime 未消费 `scale.parent` 作为 DOM owner，自行把 canvas 附到 body；layer 名精确不匹配。
- 根因：误把 Phaser GameConfig 的 top-level `parent` 写入 `scale` 配置，并在手写 layer 常量时 trim 了 Tiled JSON 的名称。
- 修复要求/验收标准：GameConfig 必须显式 `parent: canvas.parentElement` 与 `canvas`；scale 只管理 resize；`#resultMap` 保持 overflow containment；使用源 tilemap 精确 layer 名。对于 legacy `interiors_pt3.png` 的 16px 非 tile footer，只允许新增 Replay 专用、hash 固定且宽高均为 32 倍数的包内规范化纹理；不得修改 legacy 源文件、吞掉全部 Phaser warning 或从外部路径临时读取。规范化资源须进入 wheel、由受控静态路由 200 返回且 manifest/player 实际消费。fresh Browser 截图/DOM 几何证明卡内真实 tilemap+sprite，唯一 canvas parent/尺寸正确且 `console errors/warnings=[]`。
- 修复记录/独立回归：共享树已加入 top-level host parent、移除 scale.parent 并恢复 layer 尾空格；随后以 Replay 专用 512×10016 crop 和规范化 tilemap 修正 legacy 10032px 尾部及 `imageheight`，HTTP/local/manifest SHA 一致，恢复唯一规范化字段后与 legacy JSON 深等。规范化 PNG+tilemap 定向 `1 passed`，真实 wheel build/install/isolated HTTP 动态 manifest 资源 `1 passed`。最终 fresh Browser 三段 Run 旅程均满足唯一 canvas count=1、`#resultMap > #resultMapCanvas`=1、`body canvas`=0、parent=`resultMap`；规范化 tilemap/PNG 可见且 console warn/warning/error 均为空，故转 VERIFIED。

### DEF-067 — Phaser teardown 删除 shell-owned Canvas，第二条 Run 无法加载 Replay

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-RPL-002、ROL-SYNC-001。
- 影响：初始 Run 可 READY 并正确显示地图/Agent；切换到同 Revision 的 completed Run 时，旧 player teardown 把 HTML 声明的唯一 `#resultMapCanvas` 从 DOM 删除，随后新 player 因 `REPLAY_CANVAS_HOST_MISSING` 停在 LOADING，Agent options 为空，导致多 Run 结果对比阻断。
- 证据/复现：fresh Browser 初始页面 console clean，切 completed Run 后出现上述错误；`GAReplayPlayer.destroy()` 实际调用 `game.destroy(true, false)`。新增可执行 Node 红线用会遵守 Phaser `removeCanvas` 参数的 mock game 调用真实 player `destroy()`，修复前 shell-owned canvas 从 host children 消失并稳定失败。
- 期望：Run teardown 只销毁 game/scene/timer/request，不删除由 shell 管理的 canvas；任意次数切 Run 后 `#resultMap > #resultMapCanvas` 恰好一个，`body > canvas` 为零；same-Revision Agent selection/Inspector 按 DEF-065 恢复。
- 实际：`removeCanvas=true` 把外部注入 canvas 当成 Phaser 私有资源删除，而 console 继续持有已 detached 的元素引用。
- 根因：播放器没有区分“Phaser 创建的 canvas”和“高保真 shell 显式传入且拥有生命周期的 canvas”。
- 修复要求/验收标准：对外部 canvas 使用不移除 canvas 的 Phaser destroy 语义（或在加载前原子重建唯一受控 canvas，禁止重复/body canvas）；可执行 lifecycle 红线、DEF-065 owner 红线与 fresh Browser running→completed→running 连续切换均通过，地图/Agent/Inspector可见且 `console errors/warnings=[]`。
- 修复记录/独立回归：共享树把 Phaser destroy 改为保留外部 canvas；原可执行 Player lifecycle 红线与 DEF-068 组合独立结果 `2 passed`。最终 fresh Browser 完成 running→completed→running：每次均 READY，始终恰好一个 `#resultMap > #resultMapCanvas`、无 body canvas，乔治选择与对应 Inspector 恢复，地图可见且 console 全空，故转 VERIFIED。

### DEF-068 — Windows Worker 继承 cp936 输出，Attempt 日志违反 UTF-8 合同

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-LOG-001、ROL-SYNC-001。
- 影响：同机并发真实 Runs 虽可完成、生成 RECOVERABLE checkpoint 与 READY Replay/Report，但任一包含中文 stdout/stderr 的 Attempt log 在 byte cursor 处返回 500 `ATTEMPT_LOG_ENCODING_INVALID`；运行诊断、SSE tail、筛选与下载均不可用。
- 证据/复现：live-model E2E 两条 Run 均在 cursor 160 失败。新增 `test_def_068_supervisor_child_chinese_stdout_is_explicit_utf8_and_byte_exact` 不写手工 UTF-8 fixture，而是让 `LocalProcessSupervisor` 实际 spawn 子 Python，将一条中文 stdout 与一条中文 stderr 合并到 scheduler-owned Attempt log。当前独立失败同时报告：未传显式 child env、严格 UTF-8 在 byte 5 解码失败、LogService 返回 `ATTEMPT_LOG_ENCODING_INVALID`、cursor 只消费 0/77 bytes。
- 期望：supervisor 对 worker 进程显式冻结 UTF-8 I/O，不依赖父进程 locale/控制台；原始 child stdout/stderr bytes 是合法 UTF-8，7-byte 分页在多字节字符中间仍保持 file_id、next_cursor 与无损拼接。
- 实际：`subprocess.Popen` 未传 `env`，Windows 重定向后的 Python stdout/stderr 继承 cp936；服务端坚持 UTF-8 的正确读合同因此拒绝生产者写出的字节。
- 根因：日志 consumer 已定义 UTF-8，但 child process producer 的编码没有在进程边界冻结。
- 修复要求/验收标准：worker spawn 使用父 env 的受控副本，并显式 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`（保留必要环境变量，不全量替换）；stdout/stderr 继续合并且禁止 decode/re-encode 隐性容错。原红线须证明 raw strict decode、两条 record、最终 cursor==raw byte size、所有窗口拼接==raw decode；live-model 旧 Run 可明确标历史不可读，但新 Run 日志 API/SSE须200无损。
- 修复记录/独立回归：supervisor 现从父环境受控复制并显式覆盖 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`。同一真实 child-spawn 红线重新执行后，raw strict UTF-8、stdout/stderr 两条 record、7-byte 分页 file_id/cursor 与无损拼接全部通过；与 DEF-067 组合独立结果 `2 passed`。修复后另从已发布 Revision 启动真实 qwen chat+embedding Run `5a28e47f…` 并完成 1 步，Attempt `05c4dd88…` 日志为 4857 bytes：raw strict UTF-8 解码成功且包含乔治，经正式 HTTP `limit_bytes=7` 共 629 页重组，file_id 全程稳定，`final_cursor=4857=raw bytes`、`terminal/eof=true`，API 拼接与 raw 严格解码文本完全相等；同 Run 另有 VALID/RECOVERABLE Step 1 checkpoint（SHA `62ec…`）及 READY final Report/Replay V2，故保持 VERIFIED。

### DEF-069 — Web 重启后恢复 Run 会重新物化 immutable manifest

- 严重度/状态：P0 / VERIFIED
- ROL 映射：ROL-REC-001、ROL-SYNC-001。
- 影响：已有有效恢复点的 `PAUSED`/`FAILED`/`INTERRUPTED` Run 在 Web 服务重启后可成功创建新 Attempt，却在 worker spawn 前失败；真实 Run `cbbdd…` 从权威 Step 94 恢复时 Attempt 2 应从 Step 95 开始，实际被置为 FAILED 且无法继续，尽管 checkpoint 未损坏。
- 证据/复现：`test_def_069_cross_web_restart_resume_reuses_the_original_run_manifest` 使用两个 `LocalProcessSupervisor` 与两个受控 Web 时钟，真实完成 Attempt 1→Step 1 frame/checkpoint→PAUSED→resume→Attempt 2（`start_step=2`）。第二个 supervisor 在 `_materialize_manifest` 抛 `ManifestConflictError: run manifest is immutable and already differs`；定向结果 `1 failed`，堆栈为 `supervisor.py:163 → manifest.py:122`。
- 期望：manifest 是 Run 级发布快照；后续 Attempt 与重启后的 Web 进程必须 load+verify 原始 bytes，不得以当前时间重新生成。worker 继续从原 manifest 读取 definition/code/assets；恢复 Attempt 的 `start_step` 仍严格等于权威 `recoverable_step+1`。
- 实际：supervisor 对每次 claim 都调用 `build_manifest_document(... materialized_at=datetime.now(...))` 再 `materialize()`；第二次文档即使 Revision、definition、asset、code build 与依赖完全相同也发生 immutable conflict。
- 根因：manifest 创建生命周期错误地绑定到 Attempt claim；独立字段对比证明两次文档只有 `materialized_at` 与其派生 `manifest_hash` 不同。
- 修复要求/验收标准：仅首个 Attempt 创建 manifest；已有文件时执行完整 load/ownership/hash/definition/algorithm 校验并原样复用，不能更新 `materialized_at`、hash 或 bytes。跨 Web 重启的真实恢复必须成功启动新 Attempt 并从授权下一步继续。负控必须保留：同一 Run 的真正不同 code build/definition 或被篡改 manifest 仍由 immutable store/verified load 硬拒绝，不能通过覆盖文件或吞掉所有 conflict 修复。
- 修复记录/独立回归：supervisor 对已存在 manifest 改为 load+verify 原始快照，只在文件不存在时首次物化。原正反红线独立转为 `1 passed`：跨 Web 时钟的 Attempt 2 原 bytes/hash/materialized_at 不变，而真正不同 code build 文档仍触发 `ManifestConflictError`。主服务再次重启后，真实 Run `cbbdd…` 从 FAILED/recoverable 94 经 POST resume 入队，5 秒后 Attempt 3 `a8323474…` 进入 RUNNING/slot 1、`start_step=95`、error null，completed/recoverable/available 均保持 94，证明未虚抬提交边界，故转 VERIFIED。

### DEF-070 — 旧 Checkpoint 的无时区记忆时间与 aware SimulationClock 不兼容

- 严重度/状态：P0 / VERIFIED
- ROL 映射：ROL-REC-001。
- 影响：恢复 worker 已通过 manifest 和权威 Step 选择，却在加载旧 memory index 后的 cleanup 阶段退出，无法从已有 checkpoint 继续。若只把无 offset 字符串一律标成 UTC，异常会消失但 active memory 会被误判为 future/expired 并静默删除，导致恢复后记忆事实与不中断基线不一致。
- 证据/复现：真实 Run Attempt 3 从 Step 95 启动后在 `storage/index.py cleanup` 抛 `TypeError: can't compare offset-naive and offset-aware datetimes`。`test_def_070_legacy_checkpoint_memory_dates_resume_with_an_aware_clock` 还冻结语义：旧 checkpoint 的 create/expire 是 aware SimulationClock 通过 `strftime` 写出的 wall-clock 字符串；当前时间 09:00+08 时，08:00~10:00 的 active memory 必须保留，只删除 08:30 已过期和 10:00 未创建节点。共享树的阶段修复把 legacy 字符串解释为 UTC，实际把三者全部删除；定向失败显示额外误删 `active`。
- 根因：旧索引 metadata 丢失 UTC offset，但其语义来自当时 simulation clock 的本地 offset；新 runtime 恢复了 aware clock，cleanup 未在同一时区语义下解析两侧。阶段修复错误地把“缺 offset”当成“原值就是 UTC”。
- 修复要求/验收标准：恢复旧 checkpoint 时，以权威 checkpoint/SimulationClock 的 tzinfo 解释 legacy wall-clock create/expire/access，再统一比较；新写 metadata 应携带明确 offset 或冻结可逆 schema。对 active/expired/future 三节点的删除集合必须精确，不能仅验证“不再 TypeError”；真实 checkpoint 94 恢复后 Agent memory/RNG/coord/conversation 仍需与不中断基线连续。
- 修复记录/独立回归：legacy create/expire 现以 `clock_now.tzinfo` 解释后再统一比较；active/expired/future 精确删除集合与 DEF-069/071 组合独立 `3 passed`。主服务重启后的 Attempt 4 `3e1859c3…` 从 Step 95 启动，连续 RUNNING 超过原 Attempt 3 的 22 秒崩溃点，恢复多 Agent memory 并进入乔治 `retrieved 32 concepts → retrieve_plan`；日志无 Traceback，completed/recoverable/available 在新提交前均安全保持 94，故转 VERIFIED。

### DEF-071 — 零模型调用 Attempt 在 finally 投影不存在的 trace 文件

- 严重度/状态：P1 / VERIFIED
- ROL 映射：ROL-TRACE-001、ROL-REC-001。
- 影响：worker 在首次模型调用前因恢复/初始化错误退出时，`ModelTraceWriter` 尚未创建 JSONL；finally 无条件投影不存在文件，追加第二个 `FileNotFoundError` 和 `final model trace projection failed`，污染主故障诊断，并可能把清理失败误当成新的退出原因。
- 证据/复现：真实 Attempt 3 的主异常为 DEF-070，随后 finally 又出现 trace projection `FileNotFoundError`。`test_def_071_zero_model_call_failure_does_not_project_a_missing_trace_file` 以可执行 worker main 构造 recorder 已创建但零 append/路径不存在，并在首个模型调用前注入主异常；实际仍调用 forbidden projector、记录第二条异常，定向 `1 failed`。
- 根因：worker 用 `recorder is not None` 代表“存在可投影 trace”，但 writer 构造与首次 append/物理建文件是两个不同事实。
- 修复要求/验收标准：零调用且 trace path 从未创建时跳过 projector并保留原 exit code/主异常；只要已有完整 JSONL 记录，零 Step 失败仍必须按 DEF-032 在 finally 投影，不能通过全局移除最终投影修复。已存在但丢失/被替换的 trace 仍按存储完整性合同报错，不得将任意 missing 都静默吞掉。
- 修复记录/独立回归：worker finally 现仅在 `recorder.path.is_file()` 时投影；零调用可执行 main 保留主异常且不调用 projector，已有 JSONL 的 DEF-032 最终投影合同未删除。与 DEF-069/070 组合独立 `3 passed`；真实 Attempt 4 日志已增长至约 188 KiB、strict HTTP UTF-8 可读、terminal=false 且无 Traceback/二次 projection 异常，故转 VERIFIED。

### DEF-072 — Windows Junction 绕过 Run storage reparse ownership

- 严重度/状态：P0 / VERIFIED
- ROL 映射：ROL-ART-002、ROL-RPL-001、ROL-RPL-003。
- 影响：Windows 普通用户即使不能创建 symbolic link，仍可无需提权创建 NTFS Junction。若边界只调用 `Path.is_symlink()`，数据库持久化路径可经 Junction 读取或下载目标目录内容，Replay manifest/window/ArtifactBuilder 也会把链接后的 frame 当成所属 Run 事实；跨 Run target 可因此破坏所有权隔离。
- 首次证据/复现：`tests/architecture/test_windows_reparse_storage_boundaries.py` 真实创建 `LinkType=Junction` 且 `Path.is_junction()==True`、`Path.is_symlink()==False` 的中间目录。旧实现下 Artifact preview 实际 200 返回完整内容；Replay manifest/window 均 200，builder 无异常并生成 READY Replay。测试期望稳定 409/500 完整性错误、无 READY artifact、响应不泄 `var_dir`/物理 target。
- 根因：Windows reparse point 不限于 symbolic link；仅检查 `is_symlink()` 不能覆盖 Junction、mount/reparse chain，也不能绑定最终打开句柄的真实目标。
- 修复要求：Run root 与 DB-owned path 从 `var_dir` 到叶节点逐组件拒绝所有 reparse point（`is_junction`/Windows file attributes），打开后继续用 handle identity/final path 约束 TOCTOU；preview/download/manifest/window/builder 必须共用边界。跨 Run target 无论内容语义是否自洽都拒绝，错误不得泄物理路径。
- 修复记录/独立回归：实现增加 `is_junction`、`FILE_ATTRIBUTE_REPARSE_POINT`、逐组件检查及 Windows opened-handle final path/fstat 身份复核；Artifact preview/download、Replay manifest/window/builder 均消费同一已验证 descriptor，download 不再二次按路径打开。冻结后矩阵在父 `run_root`、中间目录、叶目录和指向另一 Run 的目标分别创建真实 Junction，8 项全部通过；每项 `finally` 只对 Junction 执行非递归 `Path.rmdir()`，随后断言物理 target 文件仍存在且 bytes/hash 未变，实证清理没有遍历删除目标。DEF-072 已据此 VERIFIED；后续扩大后的 7 个原生 symlink gate 节点（DEF-047 一项、DEF-061/063 各三项）仍是不同门禁，不能由 Junction 代替。

### 6.2 运行可观测性与结果生命周期首次红线

当前冻结实现的独立自动化复验为：

```text
python -m pytest tests/architecture/test_run_observability_lifecycle_redlines.py -q
79 passed, 7 skipped, 1 warning in 96.86s

python -m pytest -q
233 passed, 7 skipped, 1 warning in 136.71s
```

七项 `skipped` 只代表当前 Windows 主机无创建真实 symlink 的权限：DEF-047 日志一项、DEF-061 Artifact 三项、DEF-063 Replay 三项；不能作为 symlink 边界通过证据。warning 为 Python 3.13 环境既有 Starlette TestClient/httpx 弃用提示。新增跨 Run file/directory symlink 补齐目标位于另一 Run 的攻击形态；普通模式完整 ROL 诚实报告 7 skipped，直接以 `GA_REQUIRE_NATIVE_SYMLINK_TESTS=1` 执行冻结七节点得到 `tests=7, failures=7, errors=0, skipped=0`，证明能力不足不会被门禁记成 skip/pass。严格 runner 已精确收集七个 node，并以 JUnit 强制 tests=7/skipped=0；本机尚不具备执行成功条件。

严格交付判定：除原生 symlink 外，本轮实现、ROL 与 Browser 门禁均通过，未发现新的生产缺陷；但 DEF-063（P0）与 DEF-061（P1）仍为 `ASSIGNED`，按“P0/P1 未关闭数必须为 0”的既定门槛，当前不能给出无条件发布结论。必须由具备真实 symlink 能力的 Linux/Windows CI 各自运行严格七节点 runner，两个 Required check 均达到 7 tests/0 skipped 后再独立转 VERIFIED；`--allow-unavailable`、Junction、monkeypatch 或普通全量中的 skip 均不是发布证据。

```text
python -m pytest tests/architecture/test_run_observability_lifecycle_redlines.py -q
15 failed, 1 warning in 6.91s
```

十五项失败均为稳定生产合同失败；ActivityKind 测试夹具错误已在本次计数前修正，不计入缺陷。随后对开发中的日志 reader 静态/动态审计又新增 DEF-055，其独立失败不回写首次基线数字。warning 仍为当前 Python 3.13 环境的 Starlette TestClient/httpx 弃用提示。当前尚未执行本轮全量，因为新增红线设计目的就是先保持失败并冻结验收；开发标记 READY 后必须先定向复验，再确认旧 130 项无回归。

开发中第二次独立阶段复验（包含新增深层协议断言）为 `23 passed, 6 failed, 1 skipped`；随后补入四项 Attempt/Artifact SSE 重连、轮转、心跳/终态与所有权断言并定向得到 `4 passed`。六项保留失败分别是 DEF-048、DEF-051（两项）、DEF-052、DEF-054（两项）。`skipped` 仅为当前 Windows 主机无创建 symlink 权限，不代表该边界通过；尚未在开发冻结后执行仓库全量。

本轮继续以原合同复验后的阶段结果：

```text
python -m pytest tests/architecture/test_run_observability_lifecycle_redlines.py -q -k "def_051 or def_052"
7 passed, 68 deselected, 1 warning

python -m pytest tests/architecture/test_run_observability_lifecycle_redlines.py -q -k "def_053 or def_056 or def_057 or def_059 or def_048"
9 passed, 66 deselected, 1 warning

python -m pytest tests/architecture/test_run_observability_lifecycle_redlines.py -q -k "def_047 or def_049 or def_050 or def_055"
28 passed, 1 skipped, 46 deselected, 1 warning

python -m pytest tests/architecture/test_run_observability_lifecycle_redlines.py -q -k "def_051 or def_052 or def_054 or def_058 or def_060 or def_061 or def_062"
8 failed, 21 passed, 2 skipped, 37 deselected, 1 warning

python -m pytest tests/architecture/test_run_observability_lifecycle_redlines.py -q -k def_063
5 failed, 2 skipped, 68 deselected, 1 warning
```

其中已独立转绿的 schema/source identity/自动制品任务、UTF-8、bounded I/O、恢复状态机按各自 DEF 转 VERIFIED；浏览器或真实 symlink 仍是验收组成的缺陷保持 ASSIGNED。最后两组失败只指向 DEF-054/060/062/063；symlink skip 不计通过。开发尚未冻结，未运行本轮仓库全量，旧 126/130 基线不能替代新增 ROL 红线。

### 6.1 最终轮阶段性命令证据

首次独立全量（新增最终对抗测试前）：

```text
python -m pytest -q
101 passed, 1 warning
```

新增最终对抗测试首次执行为 `8 failed, 5 passed`；其中 4 个失败是测试夹具错误并已更正，4 个稳定生产失败对应 DEF-034/038/039/040。继续扩大零步 trace 与 SSE backlog 后，额外确认 DEF-032/035 的边界修复不完整；部署审计再发现 DEF-044。修复完成后全部使用原失败断言独立复跑：

```text
python -m pytest tests/architecture/test_final_e2e_regressions.py tests/foundation/test_legacy_import.py -q
18 passed, 1 warning

python -m pytest -q
118 passed, 1 warning in 46.43s
```

warning 仍为 Python 3.13 下 Starlette TestClient/httpx 的弃用提示。该数字是发现 DEF-045 前的历史全绿基线，不能作为当前发布结论；真实浏览器随后证明正式 bundle 与中性 shell 的组合仍不可进入实验。DEF-036/045 必须按各自新验收重新给出最终证据。

DEF-036/045 修复后的最终独立复验：

```text
python -m pytest -q
126 passed, 1 warning in 46.49s
```

DEF-045 的正式 bundle 所有权、DOM contract 与 no-inline 红线均通过；fresh in-app Browser 完成动态实验卡进入详情、Agent 导航、五个结果 Tab、返回列表和新建弹窗旅程，累计 `console errors=[]`。DEF-036 在 1280×720、devicePixelRatio=1 的真实浏览器中完成正向/反向焦点循环、Esc 关闭、背景 inert、保存与触发焦点恢复；125% zoom 因工具未改变 viewport 未宣称实测通过，Enter 因插件只发送 key event、未触发原生 button 默认 click 也未宣称自动化通过。当前 DEF-001～045 全部为 `VERIFIED`，无未解决缺陷。
