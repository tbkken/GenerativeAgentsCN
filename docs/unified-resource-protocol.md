# config、exp、run 统一资源协议

协议版本：`ga-package / 2`。

`ga_protocol` 为基础资源、实验和 Run 提供同一套资源内容、包内引用、附件和完整性规则。`config`、`exp`、`run` 是包的三种用途，由 `package_kind` 区分。资源进入实验或随实验嵌入 Run 时，不再转换成另一套持久化资源结构。

## 1. 公共内容与专属信息

| 用途 | 公共内容 | 专属信息 |
| --- | --- | --- |
| config | 所选资源、资源自身附件、关联声明 | `config_id`、名称、导出资源根集合 |
| exp | 完整资源集合与全部依赖 | 实验 UUID、地图与 Brain 选择、Agent 出生位置、模型用途、仿真参数 |
| run | 完整内嵌 exp 的资源集合 | Run／Attempt 身份、执行状态、已提交事实、检查点与恢复数据 |

公共资源通过 `ResourceRecord` 表示，字段包括 `kind`、`key`、`name`、`description`、`definition`、`dependencies` 和 `attachments`。`ResourceRef` 使用类型和稳定 key 引用资源，可声明依赖内容的 `expected_sha256`。公共数据库 ID、素材表外键和本机凭据定位不进入公共合同。

资源类型为 `map / agent / crowd / skill / model / spatial_asset / evaluator`。大脑是 `skill` 的一种，`definition.skill_kind` 为 `brain`；技能页面显示 `atomic / pack`。是否作为实验 Brain、对象根技能或子技能使用，由实验装配和引用关系决定。

- 地图资源保存几何、四层语义、碰撞、图层、素材切片、对象初态和状态图。空间语义索引作为地图中的派生内容，由同一套 Protocol 逻辑生成。
- Agent 资源保存人物内容、感知限制、头像与行走图等。`coord`、初始地址和已知空间属于实验的 `placements`。
- 人群资源保存成员资源引用；每名成员的具体人物内容由 Agent 资源保存。
- Skill 保存精确 `SKILL.md`、脚本及模板等私有附件，依赖通过资源引用声明。
- 模型资源可以只提供聊天或向量配置，实验装配分别选择对应用途。

## 2. 物理结构

目录是工作形态，ZIP 是交换形态。config 下载文件使用 `*-config.zip`，读取器也接受 `.gaconfig`；实验、Run 继续使用 `.gaexp`、`.garun`。实际类型以包头为准，目录名和文件名不决定业务身份。

config 和 exp 共享以下内容入口：

```text
manifest.json
resources/index.json
skills/items/<skill_key>/SKILL.md
skills/items/<skill_key>/scripts/...
skills/items/<skill_key>/<其他私有附件>
assets/...
integrity/sha256.json
```

exp 另有：

```text
runtime/assembly.json
```

`manifest.json` 的 `entrypoints.resources` 指向资源索引，exp 的 `entrypoints.assembly` 指向装配。消费者按清单入口读取，不依赖上述默认目录名。`resources/index.json` 同时包含资源定义、依赖和附件路径清单，不再另外持久化一套 `world/world.json`、`agents/index.json` 或 `skills/registry.json`。

实验装配使用 `ExperimentAssembly`：

```text
map          所选地图引用
brain        唯一 Brain 引用
placements   Agent 引用、出生坐标与空间信息
crowds       所选人群引用
models       chat / embedding 用途对应的模型引用
simulation   虚拟时间、步长、步数等
engine       引擎参数
results      结果配置
evaluators   Evaluator 引用
```

Run 结构为：

```text
run.json                         # ga-package / 2 / run，创建后不可变
experiment/                      # 完整的 exp，内容不可变
  manifest.json
  resources/index.json
  runtime/assembly.json
  skills/...、assets/...
  integrity/sha256.json
status.json
control.json
attempts/<attempt_id>/...
frames/step-000001.json.gz
commits/step-000001.json
checkpoints/...
recovery/...
projection.json
traces/...、logs/...、artifacts/...
integrity/sha256.json             # 正式封存 Run 时生成
```

`run.json` 记录内嵌实验 UUID、相对路径和根摘要。Run 直接使用内嵌实验中的公共资源，不再复制第二套资源索引到 Run 根目录。帧的预期摘要存放在持久 `commits/` 中，删除可重建投影不能取消帧完整性检查。

## 3. 组合、提取和转换

统一读取器 `read_resource_set` 接受目录及归档，识别 config、exp、run；run 的读取自动进入内嵌实验。`select_resources` 选择资源及其自身附件，并按需展开关联资源；`write_config_package` 写出相同资源内容合同的 config。

| 转换 | 处理方式 |
| --- | --- |
| config → exp | 将公共资源完整复制到实验，补齐装配并验证依赖闭包 |
| exp → config | 选择全部或部分资源，携带所选资源的自身附件 |
| exp → run | 创建新 Run，完整嵌入原实验 |
| run → exp | 读取内嵌实验；要修改时创建独立实验副本 |
| run → config | 从内嵌实验提取所选资源 |
| config → run | 先形成完整实验，再使用同一 Run 创建流程 |

从 Run 提取的是当时使用的原始实验资源。普通资源导入不会把某个 Step 的坐标、对象状态或私有记忆写回基础配置。执行事实由实际执行产生，不能通过把 config 改名为 run 得到。

## 4. 基础配置菜单与资源交换

基础配置菜单为地图、智能体、技能、模型。“智能体”包含智能体／人群两个 Tab，“技能”包含技能／大脑两个 Tab；各 Tab 分别保留搜索和分页。实验工作区使用相同的分组，仍编辑实验自己的物理副本，已封存实验只读。

基础配置顶部提供“导入资源包”和“导出资源包”。导出对象随当前 Tab 变化；导入按整个菜单识别同组资源，任一 Tab 均可导入人群及成员，或大脑及依赖技能。实验工作区不提供公共资源交换按钮。

导出步骤：

1. 先保存希望分享的编辑内容。
2. 点击“导出资源包”，选择当前类型的资源。
3. 人群自动打包全部成员及图片；大脑和 Skill 自动递归打包子技能、脚本、模板。单个智能体或原子 Skill 也可单独导出。共享依赖只保存一次；缺失、归档或未绑定的依赖阻止完整导出，并显示具体关联。地图等资源仍可按需勾选关联资源。
4. 核对打包清单与数量，下载生成的 config ZIP。模型密钥不会进入文件。

导入步骤：

1. 在所需基础配置页面点击“导入资源包”。
2. 选择 config ZIP、`.gaexp` 或 `.garun`。
3. 预览资源名称、key、附件数量、复用／冲突信息。当前 Tab 优先展示，同菜单的另一类资源直接显示；包内其他资源可以展开选择。
4. 默认选择包内本组根资源，自动加入人群成员和递归技能依赖，标注关联原因与总数。取消上层人群／大脑后，可只选择某个智能体／Skill 提取；子 Skill 自身的依赖仍会自动加入。地图等入口保留关联资源选项。
5. 确认导入，查看新增、复用和待绑定明细。操作历史保存结果，迟到响应不会切换当前页面。

例如，在地图页面上传完整 `.gaexp`，仅勾选地图且不勾选关联资源，即可只导入地图及地图素材。实验包中的 Agent、Skill、大脑和模型不会被强制导入。同一个 `.garun` 也能用于这一操作，读取范围是它的内嵌实验。

导出当前已保存的服务端内容。导入后更新相关列表与成员缓存，切换到同组另一 Tab 也能看到新资源；操作完成不强制跳转。模型连接测试和 Skill 执行均须通过各自操作入口主动发起。

## 5. 待绑定关系、冲突与密钥

config 协议仍允许显式缺少跨资源关联，所选资源自身的附件必须完整。导入这类 v2 包时显示待绑定的成员／技能 key 与可用的预期摘要；不会按显示名称静默绑定到不同内容。正常人群、大脑及 Skill 导出执行上述完整性要求。

后续单独导入依赖时，有预期摘要且实际内容一致的关系可以自动补齐。人群编辑页显示仍待绑定的成员 key，可以到智能体页面补充导入，再通过“管理智能体”明确调整成员。保存成员选择表示采用当前选择，未选成员的旧待绑定关系会被清除。地图对象的 Skill 选择器可重新绑定；技能和大脑的依赖面板显示缺失引用，可在正文中修正后保存。

`expected_sha256` 用于验证导入内容与匹配待绑定关系，不是资源版本锁。显式修改实验草稿后，包内依赖摘要随当前内容重新计算。

同类型、同 key 且内容一致时复用本机资源。同 key 但内容不同或本机资源已归档时阻断所选资源的导入，不自动覆盖或恢复；用户可以取消该项，或先在基础配置中处理冲突再重新预览。资源内容比较覆盖定义、附件和依赖的内容摘要；本机 ID 和归档内的附件搬迁路径不构成不同内容。

基础资源含待绑定依赖时，导入本身可以完成；选入实验及正式封存仍需完整展开与校验，不能带着缺失闭包进入 Runtime。已经创建的实验继续使用自己的副本，导入公共资源不会改写已有实验或 Run。

模型分享只保留非秘密参数与凭据环境变量声明。密钥值、凭据记录、认证请求头及 URL 中的认证数据不导出。接收方需要核对 `localhost` 等机器相关地址，并在本机配置认证；导入不会主动联系模型服务。

## 6. 完整性、提交与校验

- 所有清单入口及附件使用安全的包内相对 POSIX 路径。
- 使用规范 JSON、逐文件 SHA256、排序成员和固定 ZIP 时间戳，复用现有确定性封装。
- 安全解包拒绝越界路径、重复成员、符号链接和超过数量／展开体积限制的归档；目录包同样拒绝符号链接。
- Studio 导入先检查完整性、资源结构、附件与所选资源冲突，再在数据库事务内建立资源、素材和本机引用。失败不留下半套作者资源。
- Web 上传预览限制为 256 MiB，预览有效期为 30 分钟。预览 token 绑定上传文件摘要，同一 token 的相同确认请求返回同一结果；已完成导入后改选资源须重新预览，避免网络重试造成重复创建。关闭或替换预览后主动释放暂存；已经开始的导入由事务完成后释放，关闭弹窗不取消已确认的写入。
- 实验编辑在暂存副本中完成校验后发布；未绑定的新上传素材可先落盘，后续保存资源引用时纳入对应资源附件。
- Runtime 继续按 StepResult 提交帧、检查点／恢复快照、投影和可见提交边界。Replay 只读取已提交事实，资源内容的执行规则变化不能触发 Skill 执行或模型调用。

文件完整性校验、资源自身校验、实验依赖／装配校验和 Run 事实／恢复校验各自明确。供回放读取的历史资源不要求通过当前 Skill 执行规则；新建、续跑与重跑仍检查执行条件。

续跑验证每个已提交帧的持久提交摘要，并要求 `committed_step` 对应的完整恢复快照。不能退回较早检查点重放已经提交的 Step；缺少精确边界时报告恢复错误。

## 7. v1 文件与已有数据库

本次将协议版本提升至 2，不提供 v1 包的运行、提取或自动迁移兼容层。v1 `.gaexp/.garun` 和历史证据文件保持原样，不自动改写。

实验列表逐条隔离无法读取的包，显示“协议版本不支持”等原因；一个旧包不会使整个基础配置页面或实验列表失败。原有作者数据库保留，新增资源交换辅助数据通过建表处理，不清空公共资源。

已启动的旧版本 Web／Runtime 进程需要按运行边界停止后再切换新版本。对正在运行的旧 Run，不能用新版本代码直接续跑；升级验收应使用独立工作目录和新建 v2 包。

## 8. 代码入口与验证

| 领域 | 代码入口 |
| --- | --- |
| 包用途、身份与入口 | `ga_protocol/schemas/manifests.py` |
| 公共资源与实验装配类型 | `ga_protocol/schemas/resources.py` |
| 统一读取、选择、摘要与 config 输出 | `ga_protocol/packages/resources.py` |
| 实验装配视图与写入 | `ga_protocol/packages/definition.py` |
| 包归档和校验 | `ga_protocol/packages/io.py`、`validation.py` |
| 独立帧提交校验 | `ga_protocol/facts/commits.py` |
| Studio 资源交换、待绑定与本机映射 | `ga_studio/resources/exchange.py` |
| 实验创建与编辑事务 | `ga_studio/experiments/builder.py`、`workspace.py`、`editor.py`、`transaction.py` |
| Web 交换 API | `adapters/web/routes/resource_exchange.py` |
| 合并菜单与 Tab | `adapters/web/static/resources/resource-tabs.js`、`adapters/web/static/shell/console-api.js` |
| 六类资源的交换交互 | `adapters/web/static/resources/resource-exchange.js` |

上述路径相对 `src/generative_agents/`。业务服务从对应模块的 `api.py` 导出，Web 不直接查询 ORM。

主要回归覆盖 Agent 图片往返、人群单独导入与后续成员补齐、地图几何／状态图／空间素材、Skill 脚本与模板、模型凭据排除、冲突与失败事务回滚、三种来源提取、跨页迟到响应、预览幂等以及旧包列表隔离。仓库外 wheel 验收还会通过真实 HTTP 入口完成 config 导出及接收机导入、exp 预览和从 run 只导入地图。

```text
python tools/check_source_boundaries.py
python -m pytest tests -q -p no:cacheprovider
node --test tests/frontend/*.test.cjs
python tools/run_symlink_release_gate.py
python -m pip wheel . --no-deps --no-build-isolation -w dist
python tools/verify_wheel.py <生成的.whl>
```

浏览器验收应覆盖四个基础配置菜单及其六类资源入口、完整实验包只选一张地图、人物图片重开确认、待绑定关系展示、重复导入复用和模型凭据提示。运行状态、恢复和回放继续使用独立 Run 验收，不用模型文字代替提交事实。

## 9. 本次实现验收（2026-09-22）

- Python 最终全量回归：546 项通过，7 项原生符号链接测试因 Windows 权限跳过；覆盖地图缺省单位往返和质量缓存对提交摘要变化的失效。
- 前端 Node 回归：88 项通过；源码模块边界检查通过。
- 独立工作目录的浏览器验收：六个基础配置入口均完成导入和实际 ZIP 下载。实验包只导入地图、Run 包只导入人物及两张图片、人群缺少成员时显示待绑定、Brain 连同子技能导入、模型配置导入均通过。下载文件再次读取确认类型与附件数量；再次导入同内容人物只复用，不新增；同 key 不同内容预览显示冲突且禁止提交。
- 最终 wheel 的仓库外安装验收通过，覆盖 config 导出／接收机导入、exp 预览、Run 中单独导入地图，以及创建、暂停、续跑、重跑、对象响应和 Replay。模型执行使用确定性本机 HTTP 测试服务。
- 原生符号链接发布门禁未通过环境能力检查：本机返回 `WinError 1314`，没有更改系统权限。此项需在具备原生符号链接权限的环境补验。

浏览器验收使用独立数据库及临时目录，服务端口为 8001。原 8000 服务页面仍显示一个旧协议 Run 运行中，因此没有停止它或改写旧包；新代码需要在明确处理旧运行边界后再切换原服务。

## 10. 合并菜单增量验收（2026-09-22）

本节记录后续菜单合并的增量验收；第 9 节保留原协议改造时的检查结果。

- 相关 Python 回归 65 项通过，完整前端 Node 回归 98 项通过，源码边界检查通过。
- 检查当前页面及进程后，使用 `restart-web.bat` 重启 8000 Web。新服务健康检查通过；主页面确认“智能体／人群”与“技能／大脑”属于两个菜单。
- 写操作全部通过浏览器在独立 8002 服务完成。输入包由 `tests/frontend/resource_tabs_packages.py` 准备；脚本只生成交换文件，不写 Studio 数据库或实验工作目录。
- 从智能体 Tab 导入人群及成员；从技能 Tab 导入大脑及两层依赖，共享子技能只加入一次。实际导出四类 ZIP，使用 Protocol 读取器校验内容和附件。
- 实际下载的大脑包重新上传后，三项均显示复用；取消大脑后单独提取一个 Skill，提交结果为新增 0、复用 1。
- 通过 UI 创建实验 `c6d76ae7-cd65-49af-ad71-fa956d681931`，核对包内智能体、人群、技能、大脑 Tab 和刷新后选中状态；未创建 Run 或调用模型。大脑编辑器返回按钮显示“大脑列表”。

验收工作目录为 `C:/Users/lenovo/AppData/Local/Temp/ga-resource-tabs-ui-e_14n56t`。原始下载保留在本机 `Downloads`，来源为该独立 Studio 的已保存资源；下表时间为文件下载完成时间，时区 `+08:00`。

| 下载文件 | 时间 | 大小（字节） | 内容／附件数 | SHA256 |
| --- | --- | ---: | --- | --- |
| `Tab 验收人群-config.zip` | 22:25:22 | 2099 | 1 人群、1 智能体／2 | `85c5c031b26bc1657d746553341b7824e25db3203826361b7cdbd781977f17c7` |
| `tabs-brain-config.zip` | 22:27:20 | 2532 | 1 大脑、2 技能／5 | `9bd0f4ae1a6c3eb2aac0854bb14fe2b247b261a0a073be1ea80b5fab6e17ace5` |
| `Tab 验收人物-config.zip` | 22:29:57 | 2013 | 1 智能体／2 | `b5d3a4f9c9550c47fdedb8669748ddc8327b044f0d3a62d744467c3be4ba1e1b` |
| `tabs-observe-config.zip` | 22:30:13 | 1772 | 1 技能／3 | `1872663bcb495faf5960e756418d6bccd759341946d5a361af8d56f850facdc5` |
