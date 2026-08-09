# 架构决策记录

> 本文记录实现时已经生效、会约束后续开发的决定。若改变决定，先新增 superseding ADR，再改代码和迁移。

## ADR-001 — 草稿 Schema 严格，发布完整性另行校验

- 状态：Accepted
- 决定：Pydantic 负责类型、未知字段、provider 组合和不依赖外部 IO 的关系约束；允许草稿暂时没有启用 Agent、世界内容或可用模型。`validate_for_publish()` 才强制完整定义。
- 原因：配置 UI 必须能分区编辑。如果每一个中间状态都要求完整可运行，用户将无法先删后加 Agent、逐步配置世界或保存未完成 Prompt。
- 后果：所有创建 Run 的服务都必须调用发布校验，不能把“Pydantic 解析成功”当成“可运行”。

## ADR-002 — 模型 auto 只在草稿测试阶段解析

- 状态：Accepted
- 决定：vLLM chat 和 openai-compatible embedding 允许 `model=auto`；PUBLISHED snapshot 必须有 `resolved_model`。显式 model 在发布事务中复制到 `resolved_model`。
- 原因：worker 重新执行 auto 会使同一 Revision 在服务模型列表变化后得到不同结果。
- 后果：模型测试适配器需要保存解析结果到草稿并增加 lock_version；未解析 auto 发布返回 `CONFIG_VALIDATION_FAILED`。

## ADR-003 — 发布逻辑提供 in-session 事务组合接口

- 状态：Accepted
- 决定：`ExperimentService.publish_draft_in_session()` 接受调用者 Session，不自行 commit。正式 publish-and-run 服务使用同一事务发布 Revision、创建 Run、入 run_queue、更新 Experiment 状态。
- 原因：若发布和入队分两个服务事务，进程崩溃会留下用户无法理解的“已发布但没有运行”。
- 后果：Web 不开放独立发布 API；测试便捷方法 `publish_draft()` 不能被正式运行路由调用。

## ADR-004 — 不可变发布版由 SQLite trigger 兜底

- 状态：Accepted
- 决定：service 规则之外，数据库 trigger 拒绝 OLD.state=PUBLISHED 的 UPDATE/DELETE；DRAFT→PUBLISHED 仍允许。
- 原因：worker、迁移脚本或将来的后台任务可能绕过普通 service。历史复现不变量不能只靠 Python 先查后写。
- 后果：投影、校验或 provenance 若需追加，必须放到其他 append-only 表，不能修改发布行。

## ADR-005 — 初始迁移显式冻结 DDL

- 状态：Accepted
- 决定：`0001_core` 显式 `op.create_table/index/trigger`，不在 migration 中调用实时 `Base.metadata.create_all()`。
- 原因：后续 ORM 改动不能追溯性改变初始迁移，否则旧部署和新空库会得到不同数据库。
- 验证：从空文件 upgrade 两次幂等；`alembic.command.check` 返回 `No new upgrade operations detected`。

## ADR-006 — SQLite 循环 ownership FK 保留且显式打断排序环

- 状态：Accepted
- 决定：Experiment 保留 current draft/current published/latest run 三个 FK；SQLAlchemy 元数据使用具名 `use_alter` 边打断排序环，SQLite 初始 DDL 仍内联 FK。
- 原因：这些指针是列表性能所需的事务投影，去掉 FK 会允许指向其他表外对象；具名边消除 Alembic metadata 排序警告。

## ADR-007 — Web API 错误统一并携带 request ID

- 状态：Accepted
- 决定：ServiceError、请求模型错误和嵌套配置错误都映射为 `{error:{code,message,details,request_id}}`，响应头同时返回 `X-Request-ID`。
- 原因：高保真页面需要区分 409 乐观锁、422 配置错误和 404，不应解析框架默认字符串。

## ADR-008 — Builtin catalog 物化为数据库快照

- 状态：Accepted（已由 Alembic `0003_builtin_catalog` 落地）
- 决定：`bootstrap-catalog --apply` 对当前配置、Prompt、Agent、地图与资源生成源清单和 SHA-256，保存不可变 `builtin_catalog_snapshots`。ExperimentService 优先从数据库最新 snapshot 深复制；只有尚未 bootstrap 的开发环境才使用 package fallback。测试仍可注入 factory。
- 原因：若 service 每次创建实验都读共享文件，就会重新引入 DEF-006；catalog 必须先内容寻址/哈希后再作为深复制来源。
- 后果：生产首次启动必须执行 bootstrap；同源指纹重复执行会跳过。目录文件变化只会形成新 snapshot，不修改已有 Draft/Revision。

## ADR-009 — 旧引擎依赖只允许从 Run context 显式注入

- 状态：Accepted
- 决定：clock、random、logger、prompt repository、model bundle、trace recorder 和 RunPaths 都由 `SimulationContext` 装配后传入领域对象；禁止恢复 service locator、模块单例、共享 Timer 或根据显示名查找当前对象。
- 原因：同进程多个 Run 交错时，任何可变全局都会产生时间、随机数、模型、日志或存储串线；进程隔离只能缓解，不能构成领域正确性。
- 后果：旧接口若缺少依赖应立即失败，不能静默回退到共享默认值。checkpoint 必须保存并恢复 Run 自有 RNG 状态。

## ADR-010 — 正式结果只投影已观察事实

- 状态：Accepted
- 决定：模拟步骤在领域行为发生处收集真实 path、conversation、memory delta、schedule revision 和 domain event，冻结为不可变 `StepResult` 后提交；制品只消费已提交 frame。地图重算、插值或 legacy 推导必须分别标记 `RECONSTRUCTED`、`DERIVED`，不能冒充 `OBSERVED`。
- 原因：结果页和后续分析必须能区分真实发生、确定性展示派生和旧数据补算，否则回放可能呈现从未发生的路径与事件。
- 后果：collector 漏字段属于数据丢失缺陷；artifact job 不得绕过 frame 重新调用领域逻辑或当前配置。

## ADR-011 — Worker 先建立租约，再加载模型栈

- 状态：Accepted
- 决定：Worker 模块顶层不得导入 LlamaIndex/OpenAI/旧引擎重依赖。子进程先验证 attempt ownership、同步写一次心跳并启动 heartbeat monitor，之后才延迟导入 runner 与 model factory。
- 原因：Windows 冷启动实测重型 import 超过 40 秒，大于 30 秒租约；若先 import，健康进程会被误判为失联。
- 后果：新增模型 provider 不能重新引入顶层重型 import；启动期异常也由已建立的 attempt 记录。

## ADR-012 — 可读结果边界与可恢复边界分离

- 状态：Accepted
- 决定：`available_step` 只由已提交 Frame/结果投影推进，`recoverable_step` 只由校验通过的完整 checkpoint 推进。强制取消可以保留更高的可读结果，但不能抬高恢复边界。
- 原因：最后一个可读 Frame 并不必然包含可恢复的 Agent、conversation、RNG 和索引状态。
- 后果：恢复按数据库 `recoverable_step` 精确选择 bundle；磁盘上更晚的 future bundle 隔离为 orphan，不作为恢复依据。

## ADR-013 — 模型 trace 使用独立游标投影

- 状态：Accepted
- 决定：模型逻辑调用/物理尝试先追加 attempt 级 JSONL；每步事实提交后，TraceProjector 按游标幂等投影 `run_model_usage` 与 summary 计数。
- 原因：模型调用发生在 StepResult 冻结之前且允许重试，不能靠步骤行动数推断；真实 E2E 证明“有 trace 文件但未调用 projector”会静默显示 0。
- 后果：重放游标不得重复计数；失败 attempt 的物理调用保留在当前 Run 运维结果中。

## ADR-014 — 恢复使用 attempt 独占可写工作区

- 状态：Accepted
- 决定：Checkpoint bundle 校验目录 step、manifest、Frame hash 和 bundle hash 后，把 state、conversation、storage 复制到新 attempt 工作区；旧 bundle 保持不可变，runner 只读 overlay 后构造领域对象。
- 原因：直接把 active storage 指向旧 checkpoint 会使后续写入污染恢复证据，且可能携带 future mutation。
- 后果：恢复需要额外磁盘复制；任意 agent_key 不匹配立即拒绝，不做按显示名猜测。

## ADR-015 — 旧数据能力显式降级

- 状态：Accepted
- 决定：legacy import 为每个源目录记录规范化绝对路径、树指纹和唯一登记；Revision 固定 `snapshot_complete=false`，结果固定 `projection_version=legacy-v1`，每个视图在 capabilities 中声明 AVAILABLE/PARTIAL/UNAVAILABLE 及原因。
- 原因：旧运行没有完整 Prompt、资源锁、模型解析结果、模型 trace 或内存增删历史，伪装成完整可复现实验比明确降级更危险。
- 后果：原目录只读保留；重复 apply 跳过；movement 插值采样标记 `RECONSTRUCTED`，不冒充 `OBSERVED`。

## ADR-016 — SSE 从当前游标衔接，错误时回读事实 API

- 状态：Accepted
- 决定：结果页先读取 Run/结果事实，再查询最新事件游标，随后以 `after_id` 建立 EventSource。事件只作为刷新信号；断线触发节流后的事实 API 回读。
- 原因：从 0 重放历史会短暂用旧 RUNNING 事件覆盖已经 INTERRUPTED 的终态；SSE 本身也不是最终事实存储。
- 后果：页面重连不会倒退状态，所有终态仍以 Run 表和投影表为准。
