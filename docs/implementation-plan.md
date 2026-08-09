# 实验隔离 Web 服务实施与验收计划

更新日期：2026-08-08
基准方案：`docs/experiment-web-service-technical-design.md` v1.2

## 1. 实施原则

1. 一个 Run 绑定一个已发布且不可变的 Revision；worker 运行期间不读取共享 `data/config.json`、Prompt 目录、Agent 目录或环境中的业务参数。
2. 同机并发采用“一 Run 一子进程”，Web 进程固定单 worker；调度槽位由 SQLite 事务唯一分配。
3. 每步事实先写完整不可变 frame，再写 checkpoint，最后用短事务推进查询投影。页面只能读取 `available_step` 以内的数据。
4. 路径只由受控 `var_dir` 与 UUID 构造。实验名、Agent 显示名、上传文件名不能决定物理目录。
5. 测试以发现问题为目标。每个缺陷必须有编号、证据、修复提交范围和回归结论，不允许只在聊天中口头关闭。

## 2. 开发切片

| 切片 | 交付能力 | 主要验收证据 | 状态 |
| --- | --- | --- | --- |
| S0 基线 | 旧行为测试、风险清单、测试策略、缺陷台账 | characterization tests、`defect-log.md` | 已完成 |
| S1 配置与持久化 | Pydantic Schema、SQLite/Alembic、草稿、校验、发布 | Schema/事务/不可变性测试 | 已完成 |
| S2 运行边界 | `SimulationContext`、算法 profile、Run 路径、完整 StepResult、原子 frame | 并发路径隔离与崩溃边界测试 | 已完成 |
| S3 调度与 Worker | FIFO、槽位认领、独立子进程、心跳、暂停/取消/恢复 | 双 Run 并行、第三 Run 排队、PID 对账 | 已完成 |
| S4 结果投影 | Summary/Timeline/Agent/Conversation/Memory/Operations 六视图事实表与 API | frame 重放一致性、分页与 run_id 隔离 | 已完成 |
| S5 制品与迁移 | 持久 ArtifactJob、旧目录幂等导入、能力降级 | 重启恢复、重复导入、旧数据 capability audit | 已完成 |
| S6 Web 工作台 | 高保真页面接真实 API、状态机、分页、SSE、错误态 | 浏览器 E2E 与无伪数据检查 | 已完成 |
| S7 加固切换 | 故障注入、性能、备份恢复、运行手册 | 全量验收矩阵 | 已完成（环境矩阵见限制） |

“已完成”表示代码与当前 Windows/Python 3.13 环境验收完成，不代表尚未执行的 Python 3.12 CI、长时容量压测和系统级 kill 矩阵已经被替代。最终独立复验状态以 `defect-log.md` 为准。

## 3. Agent 协作闭环

- 资深开发 Agent：实现配置、数据库、服务与 API 基础；收到 `DEF-*` 后给出根因与修复。
- 资深测试 Agent：独立建立测试模型，优先攻击隔离、并发、事务、恢复和结果口径，不直接修改产品代码。
- 主 Agent：维护总体架构边界，集成运行器/调度/结果链路，复核双方交付并执行最终验收。

缺陷状态固定为 `OPEN → ASSIGNED → FIXED → VERIFIED`。没有复现测试或测试 Agent 的复验，不得标记 `VERIFIED`。

## 4. 合并门槛

- 不触碰用户已有未提交修改，确需改造时先证明差异并做兼容改动。
- 所有新增 Python 模块可以在仓库根目录通过包导入，不依赖当前工作目录。
- 单元/集成测试不得访问真实模型服务；外部模型连接使用显式测试接口和可控 fake server。
- SQLite 必须启用 WAL、外键、busy timeout，并由数据库约束兜住单实验单开放运行与槽位唯一性。
- 任一 API 列表具备稳定排序和游标或页码边界；任一结果查询首先约束 `run_id`。
- 任一发布 Revision 可通过规范化 JSON 得到稳定 hash，发布后数据库和服务层都不可修改。
- 双实验并发的 Prompt、模型、Clock、随机源、索引目录、日志、frame、checkpoint 与结果投影均不得串扰。

## 5. 文档交付物

- `experiment-web-service-technical-design.md`：目标技术方案和契约。
- `implementation-plan.md`：实施顺序、角色和门槛。
- `development-log.md`：实际改动、命令、限制和技术债。
- `decision-log.md`：实现中新增或校准的架构决策。
- `test-strategy.md`：风险模型、用例分层和故障注入方案。
- `defect-log.md`：缺陷、根因、修复和复验结果。
- `operations-runbook.md`：安装、启动、迁移、备份、恢复和排障（S7 形成）。
