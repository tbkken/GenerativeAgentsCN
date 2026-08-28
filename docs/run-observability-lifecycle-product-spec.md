# 运行可观测性与结果生命周期产品规格

状态：实施中
产品负责人：主 Agent
适用范围：单机 Web 服务、多实验并行、多 Attempt 恢复、运行结果浏览与回放

## 1. 目标

将一次 Run 从排队到归档的完整事实链呈现在应用内：

`运行日志 -> 已提交步骤 -> 检查点 -> 恢复/重试 -> 派生产物 -> 时间回放`

任何页面事实必须绑定 `experiment_id + run_id`；进程级事实额外绑定
`attempt_id`。实验名、Agent 显示名和用户输入不得参与物理路径拼接。

## 2. 信息架构

实验详情保留现有结果一级页签：

- 概览
- 时间探索
- Agent
- 对话
- 记忆
- 运行与制品

“运行与制品”内部使用二级页签，不扩张左侧全局导航：

- 运行日志
- 模型调用
- 系统事件
- 检查点
- 结果产物

Run 选择器是上述所有页面的唯一运行上下文来源。切换 Run 时必须取消旧请求、
关闭旧 SSE，并通过请求代次或 `run_id` 比对丢弃迟到响应。

## 3. 产品需求与验收合同

### ROL-LOG-001：Attempt 日志可达

- 每个 Attempt 显示编号、起止 Step、状态、开始/结束时间和日志大小。
- 用户可切换 Attempt，默认选择当前 Attempt，终态默认选择最后一个 Attempt。
- 日志按 byte cursor 分页，不按“最后 N 行”猜测游标。
- 日志是可追加资源：成功响应始终返回实际消费后的整数 `next_cursor`；`eof=true`
  只表示当前文件尾，只有 `eof=true + terminal=true` 才表示日志生命周期结束。
- Artifact/Checkpoint 等不可追加文本预览可在 EOF 返回 `next_cursor=null`，客户端不得
  混用两类游标语义。
- 支持自动跟随、暂停滚动、搜索、级别过滤、错误定位和下载。
- UTF-8 字符跨块时不得出现重复、丢失或永久替换字符。
- byte 窗口可以切在日志行中间，但界面不得把 chunk 首尾的半行当作独立记录；
  单条超长 UTF-8 日志、从文件中段开始读取、以及终态没有换行符的最后一行，都必须
  保持内容完整且只显示一次。
- 追赶历史 backlog 时连续读取非 EOF 页面；轮询等待只发生在当前 EOF，避免按页人为
  延迟大日志追平速度。

### ROL-LOG-002：日志隔离与安全

- 服务端只从 `RunAttempt.log_path` 解析文件。
- 解析后的文件必须位于该 Run 的 `logs/` 下，拒绝绝对路径、`..`、symlink 和
  跨 Run Attempt。
- 日志不存在、被截断、Attempt 已结束分别返回稳定错误/状态，不泄露服务器路径。
- 跨 Run 资源返回 404；数据库路径越界或 symlink 属于服务端存储完整性错误 500；
  合法路径内容缺失返回 410；截断或轮转返回 409。
- SSE `Last-Event-ID` 必须不透明地绑定文件身份与 byte offset，文件替换后不得把旧
  offset 静默应用到新文件。
- Artifact Worker 日志使用同样的受控读取协议。

### ROL-TRACE-001：模型调用明细

- 聚合统计之外，至少可按 Attempt、purpose、状态浏览模型调用明细。
- 模型请求一进入执行就必须显示为 `RUNNING`，不得等待整个 Step 提交；完成事件增量替换同一次物理请求，终止后未完成的请求显示为 `ABORTED`。
- 明细展示模型、逻辑调用、物理尝试、延迟、重试、成功/失败和发生时间。
- 大字段延迟加载并限制预览大小；密钥和认证头不得进入响应。
- 列表与 payload 预览都必须能继续分页，不能只让前 200 条或首个预览窗口可达。

### ROL-CHK-001：检查点列表

- 列表同时呈现数据库 Step 投影和磁盘保留状态。
- 状态固定为 `RECOVERABLE`、`RETAINED`、`PRUNED`、`INVALID`。
- 只有完整执行 bundle 校验的物理检查点才能标记为可恢复。
- 当前 `recoverable_step` 对应的 bundle 缺失属于 `INVALID`，不是正常 retention
  产生的 `PRUNED`；`PRUNED` 只描述非当前恢复边界的历史检查点。
- 展示 Step、虚拟时间、Attempt、大小、文件数、bundle hash 和恢复能力。

### ROL-CHK-002：检查点内容

- 概览：Run/Attempt/Step/虚拟时间、校验结果、大小与文件数。
- Agent：位置、当前行动、日程和状态摘要。
- 对话：该检查点包含的对话快照。
- 存储：按 Agent 展示索引类型、文件数和大小，不直接渲染向量数组。
- 文件：受控 manifest；原始 JSON 只允许枚举 section、byte cursor 和大小上限。
- 可异步生成并下载仅包含一个已验证检查点的 ZIP。
- Web 详情必须实际呈现 Agent 坐标/行动、对话、存储分组和文件 manifest；仅展示数量
  不算“内容可见”。原始预览超过首个窗口时必须提供继续读取入口。

### ROL-REC-001：原 Run 恢复

- `PAUSED`、`FAILED`、`INTERRUPTED` 且 `recoverable_step > 0` 时允许恢复。
- 原 Run 只能从数据库认可的 `recoverable_step` 恢复。
- 恢复产生新 Attempt，并恢复坐标、虚拟时钟、RNG、对话和 Agent 存储。
- 大于恢复边界的 frame/checkpoint/query projection 必须按现有恢复协议回退或隔离。
- `COMPLETED`、`CANCELLED` 不允许原地恢复。

### ROL-REC-002：历史检查点分支

- 从非权威旧检查点探索时创建新 Run，禁止覆盖原 Run。
- 新 Run 记录 `parent_run_id`、`parent_checkpoint_step` 和来源类型。
- 未完整实现血缘、复制、投影与 UI 前，不显示可点击的伪入口。

### ROL-ART-001：统一产物

- Replay 只有一个正式 V2 schema 和一个正式 builder。
- `compress.py`、Web Artifact Worker 和自动完成任务复用同一 builder。
- 旧 `movement.json` 只通过 Legacy Adapter 读取，不能冒充 V2。
- Artifact 展示类型、source step、partial/final、生成器版本、时间、大小和 SHA-256。

### ROL-ART-002：产物调度

- Run 进入 `COMPLETED` 后自动、幂等排队 Replay 和 Report。
- Result Bundle 按需创建；Checkpoint Bundle 按选中检查点创建。
- PAUSED/FAILED/INTERRUPTED 可以按需构建明确标记的 partial 产物。
- 幂等身份包含 Run、job type、规范化参数、source step 和 generator version。
- Step 10 的 partial 产物不得阻止 Step 100 的 final 产物生成。

### ROL-RPL-001：Replay Bundle V2

正式结构至少包含：

- Run、Revision、Definition hash、world asset、source step、stride、起始时间；
- Agent key、显示名、sprite asset、初始坐标；
- 每 Step 的虚拟时间、observed path、位置、行动、emoji、地址；
- 对话、领域事件、checkpoint 标记和 Attempt 边界；
- 明确的 schema version、generator version、partial/final。

### ROL-RPL-002：时间探索播放器

- 使用真实 tilemap、tileset 与 Agent sprite，不以绝对定位圆点作为正式实现。
- 支持播放/暂停、前后 Step、速度、虚拟时间、自由镜头和跟随 Agent。
- 时间轴显示 checkpoint、对话、领域事件和 Attempt 边界。
- 右侧检查器展示选中 Agent 当前地点、行动、日程、对话和新记忆。
- 支持 Agent 名称、行动气泡、轨迹、对话和关键事件图层。

### ROL-RPL-003：运行中与长结果

- RUNNING 时播放器上限为最新 `available_step`，新增提交步骤自动扩展。
- 数据按窗口加载；模拟 10,000 Step 时不得一次传输或渲染全量步骤。
- COMPLETED 时优先使用锁定 source step 的 final Replay Artifact。
- Legacy compressed fixture 能经 Adapter 得到语义等价的坐标、时间和对话。

### ROL-SYNC-001：全局同步

- Run state/progress、Attempt、日志、检查点和 Artifact 事件无需刷新即可同步。
- 任一 SSE 断线后先分页追到尾游标再重连，不能让旧事件覆盖新快照。
- 切换实验或 Run 后旧流不得修改当前页面。
- 系统事件、模型调用和检查点历史必须有游标或“加载更多”，不得因固定条数上限永久
  隐藏较早或较新的事实。

## 4. 恢复动作矩阵

| Run 状态 | 原 Run 继续 | 从历史检查点创建新 Run | 结果可浏览 |
|---|---:|---:|---:|
| QUEUED / STARTING / RUNNING | 否 | 否 | 已提交部分 |
| PAUSED | 是，权威恢复点 | 是 | 是 |
| FAILED | 是，有有效恢复点时 | 是 | 是 |
| INTERRUPTED | 是，有有效恢复点时 | 是 | 是 |
| CANCELLED | 否 | 是 | 是 |
| COMPLETED | 否 | 是 | 是 |

## 5. Replay V2 与 UI 数据边界

播放器只消费正式 Replay DTO，不直接读取数据库 ORM、checkpoint `state.json` 或旧版
Flask 模板变量。运行中的 DTO 可由窗口 API 从已提交 frame 投影；完成态 DTO 可由
Artifact 文件提供。两条路径必须通过相同 schema validator。

建议窗口接口：

```text
GET /api/v1/runs/{run_id}/replay/manifest
GET /api/v1/runs/{run_id}/replay/steps?from_step=1&limit=100
```

响应返回 `next_from_step`、`available_step`、`source_step` 与 `result_version`。页面只缓存
有限窗口；拖动到窗口外时请求相邻块。

追加型日志/trace 与静态预览的 cursor 不能混用：Attempt/Artifact log 和运行中的模型 trace
即使到达当前 EOF，`next_cursor` 仍返回“实际已消费的原始字节数”，`eof=true` 只表示当前
文件尾；静态 Artifact/Checkpoint/trace payload 预览在最终 EOF 返回 `next_cursor=null`。
SSE 的 `Last-Event-ID` 是不透明 `file_id:cursor`，客户端不得自行拆解或跨文件复用。

## 6. 完成证据

功能完成必须同时提供：

1. 服务/API 单元与集成测试；
2. 双实验并行隔离测试；
3. 中断恢复与 checkpoint 语义对抗测试；
4. Replay V2 schema/legacy adapter/10k Step 窗口测试；
5. fresh Browser 的日志、检查点、产物、播放器真实旅程；
6. 浏览器 console error 为 0；
7. 仓库全量回归；
8. 当前 Windows 环境未覆盖的容量、缩放或系统 kill 边界必须如实记录。

任何“文件存在”“接口返回 200”或“窄测试通过”都不能单独证明本功能完成。
