# GenerativeAgentsCN 架构与开发约定

本文件统一仓库规则、文件协议和代码导览，适用于开发、诊断、重构与评审。未经用户明确决定，不得改变这些原则；发现需求或实现冲突时，先说明冲突及影响，等待用户决定。允许颠覆性重构，不为旧数据库、迁移、Run 或导入路径保留兼容层。

## 1. 职责与模块边界

内核提供可验证、可恢复、可回放的仿真能力，负责身份、校验、世界提交、事实与监督；自然语言 Brain/子 Skill/对象 Skill 决定调用目的、顺序、条件和停止方式。ReAct、排程、反思只是可选 Brain 模式，不得固化为系统流水线或用可视化编排器替代。

产品源码位于 `src/generative_agents/`，业务数据只通过自包含文件交换：`Studio → 实验目录/.gaexp → Runtime → Run目录/.garun → Replay`。

| 模块 | 职责与主要目录 | 允许的项目依赖 |
| --- | --- | --- |
| `ga_protocol` | `schemas/` 文件合同；`packages/` 安全归档、完整性、原子文件；`spatial/` 几何、索引、碰撞、导航；`skills/` 文档与闭包；`facts/` 事实、恢复与质量格式 | 自身；无数据库、Web、模型客户端 |
| `ga_studio` | `resources/` 公共作者资源；`experiments/` 导入、编辑、预检、封存；`catalog/` 可重建包索引；`storage/` 数据库、素材、凭据；`bundled/` 显式导入素材与可编辑 Skill 种子 | 自身、Protocol；唯一数据库所有者 |
| `ga_runtime` | `lifecycle/` Run/Attempt 装配；`engine/` 世界、Scheduler、ActorState；`capabilities/` MCP；`skills/` 执行；`memory/` 文件记忆；`models/` 网关、重试、审计；`storage/` 提交与恢复；`supervision/` 监督 | 自身、Protocol；只消费包内内容 |
| `ga_replay` | `reader.py` 已提交事实；`projections/` 状态与页面投影；`cache.py` 可丢弃缓存 | 自身、Protocol；不执行 Skill、加载 Brain 或调用模型 |
| `adapters` | `cli/` 参数、输出、退出码；`web/` 应用、路由、静态页面 | 四模块公开 API、Protocol DTO；不直接查询 ORM |

- 模块不得跨界导入 ORM、Repository 或业务 Service；公开操作由 `api.py` 明确导出，不保留兼容壳、重复实现或旧顶层目录。
- Studio 数据库仅保存作者资源、工作区/权限/展示设置、包摘要缓存及 `experiment_id/run_id → package_location` 可重建索引。删库后，已有包仍须可运行、续跑、重跑和回放；包内清单始终优先。
- Runtime 状态、资源槽位、控制请求与日志使用 Run/主机工作目录、原子文件和锁，不使用数据库队列、状态机或 SQLite 恢复投影。Replay 缓存必须可删除并从 StepResult 重建；Web 只做适配，不是事实源。

## 2. 作者资源与实验生命周期

- Map、Spatial Asset、Agent、Crowd、Brain、Skill、Evaluator、Model Preset 均按稳定 ID 直接编辑；不设业务 Revision、发布、派生、跟随最新或引用锁。各类资源、实验和 Run 均须支持删除或归档。
- 不提供默认地图或隐式地图选择；公共 Agent 全由用户创建，编辑/归档/删除规则相同，不保留系统 Agent、只读分组、种子或内置图片回退。
- 资源选入实验时立即递归展开并物理复制完整内容：地图、素材、渲染/碰撞、Agent/Crowd、Brain/子 Skill/对象 Skill、Evaluator、模型配置、脚本和模板。导入后与公共资源断开；双方修改、归档或删除互不影响。
- 实验仅有 `DRAFT → SEALED`。草稿只修改包内副本；封存后不可修改，调整须复制为新的独立实验，不建 Revision、base/fork 或升级关系。草稿替换地图是全量重新导入，不能自动沿用旧坐标含义。
- Skill 仅以包内 `skill_key` 引用；同 key 同内容可合并，同 key 异内容必须阻断并诊断；依赖缺失或非法依赖环必须在封存前失败。不得留下公共资源 ID、Revision、素材外键或运行期数据库查询条件。
- Studio 公共 Skill 保存在数据库；源码 `SKILL.md` 仅用于种子、手写实验、导入导出或开发。实验和 Run 使用各自物理副本。Run 启动时完整嵌入 `.gaexp`，之后不读当前实验目录或公共资源。
- 校验必须覆盖四层层级、坐标/地址、素材、对象交互、Agent 初始位置语义及完整依赖；预检统计与明细一致，保存、上传和预检后立即同步服务端事实。可选 Skill 试运行由 Studio 准备临时闭包、适配层调用 Runtime；需仿真 MCP 的 Skill 必须进入实验。不得强制正式运行前试跑 1～3 步。

## 3. 文件协议、身份与安全

统一使用 `ga-package` v2：`config` 为任意基础资源集合，`exp` 为资源内容与实验装配，`run` 完整内嵌实验并保存执行事实。目录是工作形态，基础资源 `.zip`、实验 `.gaexp`、运行 `.garun` 是确定性 ZIP 交换形态。清单中的 UUID 是唯一业务身份，目录或文件可改名；Replay 直接使用 `run_id`，不创建独立业务 ID。不读取旧 v1 包，不自动改写已有证据。

- 地图、智能体、人群、技能、大脑、模型均可独立导入导出；基础配置入口可从 config、exp、run 中仅选所需资源。实验导出始终包含完整资源闭包。资源自身附件必须齐全；config 可保留显式未绑定的跨资源依赖，进入实验前必须绑定。同 key 异内容阻断整次导入，不静默覆盖或按名字绑定。
- 公共 `ResourceSet` 统一资源内容、包内引用、附件和内容摘要。Agent 核心不含坐标与空间；出生位置属于实验装配。Skill 固有类型为 atomic/pack/brain，对象根与子技能是装配后的使用角色。依赖内容摘要用于交换时辨识内容，草稿显式编辑时更新，不构成资源版本或引用锁。

| 操作 | 身份与输入 |
| --- | --- |
| 新跑 | 从实验创建新 `run_id` 和首个 `attempt_id` |
| 续跑 | 保持 `run_id`，从最近完整检查点新建 `attempt_id` |
| 重跑 | 读取 Run 内嵌实验，创建新 `run_id`；可记录 `origin_run_id` |

推荐包结构：

```text
基础资源 / .zip 或实验 / .gaexp     Run 目录 / .garun
manifest.json                     run.json
integrity/sha256.json              status.json / control.json
resources/index.json              experiment/（完整内嵌实验）
runtime/assembly.json（仅实验）    attempts/<attempt_id>/attempt.json
skills/items/<key>/SKILL.md        attempts/<attempt_id>/storage/
skills/items/<key>/scripts/...     attempts/<attempt_id>/runtime-storage/
skills/items/<key>/templates/...   frames/step-000001.json.gz
assets/...                        commits/step-000001.json
                                  checkpoints/LATEST
                                  checkpoints/step-000001/...
                                  recovery/step-000001/...
                                  traces/ / artifacts/
                                  projection.json（可丢弃）
```

- 实验 `manifest.json` 保存协议身份、`experiment_id`、展示元数据和安全的包内相对 POSIX 入口路径。`resources/index.json` 保存唯一一份资源内容及附件引用，地图内含语义索引；`runtime/assembly.json` 指定唯一 Brain、地图、模型用途、Agent 出生位置、人群与运行参数。执行视图按需装配，不重复持久化 Skill registry 或旧分散 entrypoints。
- 运行配置禁止 `map_id`、`map_snapshot_hash`、`revision_id`、`brain_revision_id`、`skill_revision_id`、`secret_ref` 等外部活引用。模型密钥不进包，只声明环境变量名（如 `GA_CHAT_API_KEY`），由目标机器注入。
- `integrity/sha256.json` 覆盖清单及所有内容文件，排序计算 Bundle Hash，包含地图、Agent、Skill 文本/脚本、模型参数和素材等全部行为内容。内容哈希决定行为身份，不使用资源版本字段。
- `run.json` 创建后不可变，含 `run_id`、请求步数、内嵌 `experiment_id`/根哈希和可选来源 Run；原子更新的 `status.json` 保存状态、当前 Attempt、最后提交 Step；`attempt.json` 保存启动边界与结局。
- ZIP 拒绝绝对路径、`..`、重复成员、符号链接、超量文件/展开体积；目录包也拒绝符号链接。JSON 使用 UTF-8、排序键、规范分隔符；归档使用排序成员与固定时间戳，确保同内容同字节。
- 清单、状态、控制、记忆等文件以同目录临时文件、`fsync`、原子替换提交。先落盘帧及独立的 `commits/` 身份/哈希记录，再发布检查点，最后推进可见投影和 `status.committed_step`；须跨平台可靠。帧校验不依赖可删除的 `projection.json`。
- 活动 Run 以目录为唯一可写包，Runtime 单写者，文件锁串行化执行与封存。暂停、取消、完成或显式导出时，从一致性暂存副本生成完整性清单与 `.garun`；不得原地修改 ZIP。
- 续跑 `.garun`：安全解压至新可写目录 → 校验 Run/内嵌实验 → 获取 `worker.lock` → 核对最新完整检查点与已提交帧边界 → 新建 Attempt → 从下一 Step 继续。状态、路径、对话、记忆、对象变化必须幂等，不产生零进展 Attempt 或重复副作用。

## 4. 空间、时间与上下文

- 地图固定 `World → Sector → Arena → Game Object` 四层，均可定义语义；仅 Game Object 可绑定一个根 Skill，并通过子 Skill 组合行为。Tile 仅用于渲染、碰撞、寻路，不是公共感知合同。
- 封存时生成包内空间语义索引。Runtime/Replay 使用同一索引；感知按相交节点返回紧凑、唯一的层级语义及附近 Agent、Event、对象，不逐 Tile 重建语义树。
- `vision_radius` 是硬上限，模型只能缩小；`attention_bandwidth` 必须限制候选输出，同时保留当前位置的层级语义锚点。节点/对象按稳定 ID 去重，Event 按完整事实身份处理，不按名称或文本合并。
- Scheduler 从 1 到 `steps` 确定性推进时间，Skill/Agent 不能自行推进。每轮 Brain/子 Skill 共享 `IterationContext`：Run、Attempt、Agent、Step、总步数、带时区的具体虚拟时间、步长、坐标、四层地址、当前空间语义、公共运行变量。
- Skill 输出可成为后续 Skill 的输入，调用链由 Brain SOP 决定。对象响应只进入目标 Agent 下一轮上下文，必须恰好投递一次，不丢失或泄漏。

## 5. MCP、记忆与对象 Skill

- 系统提供公共感知、导航、记忆与动作能力；MCP 注入当前参与者身份并校验权限，禁止伪造身份、跨参与者读私有记忆或绕过校验改世界。
- `world-perceive` 只读；`memory-stream-search/append/supersede/invalidate` 提供隔离持久记忆；`world-act` 是唯一世界动作入口。每名 Agent/对象每 Step 可多次读取，最多成功提交一次动作，第二次必须拒绝；无动作时可回退 `WAIT` 并记录原因。
- 记忆以自然语言为主，可附 SPO、来源、证据、重要性；刚写入的有效记忆须可检索，替代/失效内容留历史但默认不当有效事实。已探索空间归参与者记忆，不靠每轮发送全图，也不强制活动编码字典。
- Brain 需要循环安全上限和无进展检测，重复调用必须留下诊断。Thought、计划、反思及中间文本只供过程审计，不要求 Skill 额外输出回放机器合同。
- 对象绑定 Skill 后默认用大模型按 SOP 自主运行、感知和响应交互，不要求脚本、主动/被动开关或触发器；不加入公共 Agent 目录或人物回放列表。对象有独立身份/记忆，只能修改自身状态；Agent 通过交互请求访问绑定 Skill 的对象，不能改其内部状态。
- 对象感知复用四层语义、位置、活动、视野和注意力合同；只暴露范围内已执行运动轨迹，不暴露视野外坐标或以计划路径替代事实。状态的 `state` 外观标签公开，其余字段和私有记忆不自动公开。
- 固定设施支持 `ACT`、`WAIT`、`SET_OBJECT_STATE`，可在同次 `world-act.responses` 中回复真实请求 ID。对象每 Step 在 Agent 动作后执行一次，再统一提交 StepResult；读取本步可见运动事实，状态/回复供下一轮使用。
- 对象动作与回复均须校验并进入 World Commit；检查点保存待处理请求、最后提交动作、连续失败计数和幂等活动键，故障进入可定位的质量结果。

## 6. 动作、提交与回放事实

- 通用原语仅 `MOVE`、`ACT`、`WAIT`、`SPEAK`、`INTERACT`、`SET_OBJECT_STATE`。普通活动用 `ACT`，由 Skill 填写 `predicate`、`object`、可选 `description`，页面直接展示语义，不维护活动编码/显示字典或设施业务逻辑。
- `WAIT` 只表示真实等待，可含原因和截止 Step/时间；不兜底普通活动。`SPEAK` 使用稳定、可复用且参与者隔离的 `conversation_id`，同一会话回复不新建线程。
- 每条已提交世界变化都必须有 `Event(subject, predicate, object)` 和非空、足以确定性重建的 `structured_payload`。动作、对话、对象变化均由 MCP → World Commit → StepResult 形成事实。
- MOVE 分别保存内核位移事实与移动活动：内核保证 `Event(subject, "移动到", 实际地址)` 和实际路径；经提交的活动 predicate 成为 `movement_activity`，供状态、感知和回放共用。活动文字不能证明抵达。
- StepResult 是帧、检查点、参与者状态、投影、质量和回放的共同事实源。事实类型只在 Protocol 定义，Runtime 构建/提交，Replay 读取/归约；LLM 原始文本、记忆或矛盾的临时状态不得驱动画面。
- StepResult、世界事实、Checkpoint、Effect Ledger、Trace、日志和运行记忆必须保存在 Run 文件中。Replay 校验身份、内嵌实验哈希、帧连续性/哈希，只读已提交边界及包内素材，归约 Agent、Event、Effect、对话与对象状态，按 `L1 World → L2 Sector → L3 Arena → L4 Game Object` 绘制。

## 7. 监督、质量与页面状态

- 系统 Supervisor 负责卡死检测、暂停、取消、恢复、资源槽位和健康诊断；有原因、有边界的计划等待不得误报为卡死。
- 模型基础设施处理超时、瞬时网络错误和可修复格式错误，按类型重试，受预算约束且可中断；禁止超长超时叠加盲重试或无限增长的修复上下文。取消须中断模型等待/退避，不只在 Step 结束生效。
- Run 区分 `CREATED/QUEUED/RUNNING/FINALIZING/PAUSED/CANCELLED/COMPLETED/FAILED`；恢复阶段须可见。最后一步后进入 `FINALIZING`，完成报告和固化后才 `COMPLETED`。
- 执行完成与实验质量分开。业务成功条件仅来自用户配置的指标、Evaluator 或显式断言，不写死在内核。空记忆、Brain 偏离、循环回退、对象不可交互等须有可展开、可定位到 Agent/Step 的质量明细，数量与报告一致。
- 统计区分逻辑调用和物理尝试，实时显示执行中的调用；估算按 Agent 数、调用链、重试、上下文规模、本地模型吞吐校准。大 Payload 去重/引用保存，不在 Trace 各阶段重复复制。
- 页面请求、状态和操作反馈按 Experiment/Run/Attempt/草稿隔离，旧响应不能覆盖新选择或强制跳转；迟到的复制结果留在操作历史。运行状态不能用操作成功提示替代。
- 时间保存保留时区偏移与精度；仿真摘要按 Run 时区显示，标注“北京时间”时必须转为 `Asia/Shanghai`。虚拟时间、模型耗时、含暂停的墙钟耗时分别记录，不能改写历史日志掩盖旧显示问题。
- 实验中心固定每页 5 条，保留负责人/标签元数据、归档/恢复和分页；不恢复搜索/组合筛选、状态计数整行、比较、批量标签/负责人、页容量选择、紧凑表格、保存视图。不得新增本地 `.bat` 模型服务管理界面。

## 8. 代码入口、命令与验证

以下路径相对 `src/generative_agents/`：

| 任务 | 入口 |
| --- | --- |
| CLI / Web | `adapters/cli/main.py` / `adapters/web/app.py` |
| 作者资源 / 实验 | `ga_studio/api.py`、`resources/` / `experiments/workspace.py`、`builder.py`、`editor.py` |
| Run 装配与控制 | `ga_runtime/api.py`、`lifecycle/assembly.py`、`executor.py`、`control.py` |
| Step 与提交 | `ga_runtime/engine/scheduler.py`、`world.py`、`storage/commit.py` |
| Brain / 对象 / 执行器 | `ga_runtime/skills/brain.py`、`objects.py`、`executor.py` |
| MCP / 记忆 | `ga_runtime/capabilities/server.py` / `ga_runtime/memory/stream.py` |
| 事实类型 / Replay | `ga_protocol/schemas/facts.py` / `ga_replay/api.py`、`reader.py`、`projections/` |

ActorState 只保存身份、人物信息、位置、当前动作和剩余路径，不承载固定业务流程。作者地图/Skill 元数据在 `ga_studio/resources/map_document.py`、`skill_document.py`，导入时转换为 Protocol 合同并去除数据库定位信息。源码资源仅由 Studio `bundled/` 与 Web `static/` 持有；`static/shell` 协调页面/请求，`resources` 为编辑器，`replay` 为播放器，`vendor` 为前端依赖。`var/` 是用户数据，不是源码；重构不得改写案例证据或另建历史备份目录。

维护一个 wheel，依赖按 `runtime/studio/web/dev` 声明；CLI/Web 使用同一包与协议，不依赖仓库当前目录。常用命令（路径为占位值）：

```text
ga studio serve
ga experiment validate <目录或.gaexp>
ga experiment seal <目录> <输出.gaexp>
ga run create|start <实验包> <Run目录> [--steps N]
ga run status|pause|cancel <Run目录>
ga run resume <Run目录>
ga run resume <输入.garun> --destination <可写目录>
ga run rerun <Run目录或.garun> <新Run目录> [--steps N]
ga run seal <Run目录> <输出.garun>
ga replay summary|timeline <Run目录或.garun>
ga replay state <Run目录或.garun> <Step>
```

开发按改动运行相关检查；发布门禁覆盖完整 Python/Node 回归、跨平台原生文件系统安全、仓库外 wheel 安装：

```text
python tools/check_source_boundaries.py
python -m pytest tests -q -p no:cacheprovider
node --test tests/frontend/*.test.cjs
python tools/run_symlink_release_gate.py
python -m pip wheel . --no-deps --no-build-isolation -w dist
python tools/verify_wheel.py <生成的.whl>
```

边界扫描包含直接/传递依赖、函数内导入和种子脚本；架构测试防止旧目录/通配导出回流。安装验收覆盖 CLI、Studio、文件协议、MCP、暂停恢复、对象响应、Replay，外部模型用确定性 HTTP 服务替代；前端资源搬迁另做浏览器检查。CI 见 [.github/workflows/native-symlink-release-gate.yml](.github/workflows/native-symlink-release-gate.yml)。

## 9. 教材案例操作与验收

案例入口与证据见 [docs/book/sample/README.md](docs/book/sample/README.md)。案例坐标、显示比例、模型、步数和提示词不是系统默认值。

### 操作边界

- 系统配置和运行验收由主 Agent 通过浏览器完成，不用脚本、直接接口或改数据库/实验目录绕过 UI。素材生成、保存、教学文档编辑属于资料准备，不能代替浏览器上传、绑定和保存。
- 发现当前案例的系统 bug 或体验问题即停止构建/运行验收，报告页面入口、复现动作、预期/实际、Experiment/Run/Attempt/Step、影响与证据。仅在用户针对该问题明确同意后交工程子 Agent 修复；授权不跨问题，不静默绕过或边等决定边推进。预期权限拒绝、不可达按合同判断；不扩查其他实验，用户确认的正常等待继续原任务。
- 修复并完成相关测试后，主 Agent 必须重启 Web，再回原浏览器路径验收；前端静态修改也如此，刷新、单元测试或另一个 Run 不替代复验。已授权使用 `restart-web.bat`；重启前检查运行状态，必要时 UI 安全暂停，后台启动隐藏窗口，重启后核对新进程、健康检查和实际页面/接口行为。
- bug、体验建议、待复现、已修复分别记录证据、影响、建议和验收条件，待定不写成解决。每案独立保存 `map/`、`agents/`、`skill/`、`settings/`；新图先落入素材目录，再上传绑定，未经同意不换地图。UI 修改同步中文说明、精确 Skill、参数、概览和检查表；历史 JSON 标为参考，不作当前执行依据。

### 空间与行为

- 先验画面和空间，再组织行为。住宅优先完整背景叠加语义，只为需换图的对象独立配图；联合检查人物/地图风格、尺寸、网格、比例、脚底锚点、遮挡，头像与行走图均上传应用并确认实验副本。
- 状态图保持视角、位置、切片边界和尺寸一致，核对默认状态/键/图片；编辑器切换后还须用真实 `SET_OBJECT_STATE` 验证回放，ACT 描述不能证明换图。
- 分别核验语义边界、碰撞、出生点、对象旁站位和跨房间路线，预检不能替代。空地出生用实际三层地址，不虚构对象；落在对象内保留完整四层地址。保存后重开核对坐标、地址、已知空间，再预检封存。
- 导航等真实感知返回后再构造参数，不猜门洞/地址或扩大视野；`next_coord` 只是路径首格。按步长和速度核对已走/剩余路径，抵达后才 ACT；对象可选多个可走终点，不要求路长等于人工选点。核对请求目标、每步终点与抵达后零距离导航。
- 边界对照记录真实 MCP 输入/输出、请求/生效半径、硬上限、各类候选/返回数及 CURRENT 锚点；不以 ACT 摘要、模型转述或未发出的输入冒充通过，也不把正确拒绝错误目标报为系统缺陷。
- 连续行为用 SOP 和真实进度驱动，分开 MOVE、抵达 ACT、对象状态动作，不按 Step 编排。跨步进度用私有持久记忆，区分待确认/已完成；成功 `world-act` 结束本轮，必要便签在动作前写，下轮按真实坐标、活动、对象状态核验，失败不记完成。
- 文字不能证明移动或状态变化，事件/坐标/地址/payload 不一致须登记问题。保留无效 Run，不反复重跑挑成功结果；改案例通过 UI 建独立副本。公共资源修改后须明确重新导入，SEALED 先复制；不改参考 Run 的精确 Skill 后仍引用旧结果证明新内容。

### 运行与交付

- 分开实验生命周期、Run 状态、操作反馈和质量；徽标/选择器/进度冲突先核对来源。残留反馈修复须重走复制、删除、资源中心、新建路径，同时验证正常提示。
- 恢复核对同一 `run_id`、新 `attempt_id`、完整检查点及下一提交 Step，无重复动作/对象变化；区分旧检查点清理错误与新提交失败。时间验收核对绝对时刻/持续时间、跨日期及重复恢复。
- 完成后核对所有 Attempt 质量明细、UI 数量、报告、ZIP 一致；空记忆、可恢复 MCP 拒绝、物理重试分别计数。估算分析区分调用链、重试、暂停，调试墙钟总时长不当模型速度。
- UI 导出保留原始文件，记录取材提交边界/状态、生成时间、大小、SHA256，原样归档并核对嵌入素材/Skill/参数、完整帧与报告；来源未知就标未知，历史导出不冒充最终结果。固定 Step 包不得夹入下一 Step 可变记忆，未提交日志仅作审计。
- 普通结果 ZIP、正式 `.gaexp/.garun`、异机复现分别验收；未做协议封存与独立环境验证，不承诺一键导入/恢复。回放确认不代替出版近景、缩放清晰度和遮挡检查。
