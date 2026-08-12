# 可组合能力仿真平台设计

状态：第一阶段已实施并通过最终发布门禁
设计版本：1.1
能力合同版本：`ga-capability/v1`
首次验证组合：一辆右转汽车 × 一名过街行人
兼容基线：斯坦福小镇现有实验、地图、大脑、人群和 Run Manifest 不迁移、不重写

## 1. 产品目标

系统从“围绕斯坦福小镇配置 Agent”演进为“定义实体、装配能力、连接互动并运行可复现实验”的仿真组合平台。

场景不是新的产品孤岛。场景模板只是以下可复用资产的已保存装配：

1. 地图、路径、区域和世界对象；
2. 人类 Agent；
3. 工具、凭证和可控实体；
4. 原子能力、组合能力和能力包；
5. 多方互动；
6. 时间策略；
7. 观察器和指标。

斯坦福小镇继续作为一个完整的兼容组合存在；交通实验不得要求历史小镇实验迁移到新的运行时才能继续运行和回放。

## 2. 不可破坏的产品原则

### 2.1 Agent 核心仍然是人

汽车、自行车、门禁卡、信号灯和门都是世界实体或工具，不因为具有状态机或控制能力就成为 Agent。人可以拥有、携带、占用或控制工具；工具向满足关系条件的人开放动作。

未来自动驾驶等非人控制器可以作为新的控制主体扩展，但不能迫使当前人类 Agent 模型退化为“所有东西都是 Agent”。

### 2.2 能力引用版本，不复制实现

地图画块、Agent、大脑、工具、互动和观察器只保存：

- 能力 Revision 身份；
- 本实例参数；
- 实例输入输出绑定；
- 实例运行策略。

发布后引用不可变 Revision。能力升级必须产生新 Revision，历史实验和历史 Run 仍解析旧 Revision。

### 2.3 能力通过类型化端口连接

禁止以交通场景专用字段在能力之间暗连。所有连接显式声明源输出端口、目标输入端口和可选适配器。

### 2.4 运行频率属于能力实例

统一虚拟时间轴允许不同能力使用不同更新策略：

- 固定间隔；
- 仅事件触发；
- 状态变化触发；
- 关键事件触发 LLM；
- 手动触发。

车辆动力学可以 0.2 秒推进，日程规划可以 10 分钟推进，二者不需要同频调用。

### 2.5 观察器也是能力

TTC、PET、最近距离、让行检测、对话统计和关系变化都由可装配观察能力产生。结果页读取稳定指标和事件合同，不直接理解某个场景的内部实现。

## 3. 统一能力合同

每个能力 Revision 使用 `ga-capability/v1` 合同，至少包含：

| 字段 | 含义 |
| --- | --- |
| `kind` | `SENSOR`、`DECISION`、`ACTION`、`CONTROLLER`、`OBSERVER`、`ADAPTER` |
| `targets` | 可附加对象：`AGENT`、`BRAIN`、`TOOL`、`MAP_OBJECT`、`ZONE`、`INTERACTION`、`WORLD` |
| `parameters_schema` | 用户可配置参数的 JSON Schema |
| `inputs` | 类型化输入端口 |
| `outputs` | 类型化输出端口 |
| `state_schema` | 可持久化内部状态的 JSON Schema |
| `triggers` | 支持的触发方式及默认运行策略 |
| `implementation` | 状态机、工作流、规则、Python 或 LLM 实现描述 |
| `dependencies` | 依赖的能力 Revision 或能力接口 |
| `permissions` | 读取世界状态、控制工具、执行脚本等权限 |
| `observability` | 允许记录的输入、输出、状态和指标 |

端口必须有稳定 key、方向、数据类型、是否必需、是否支持多连接和说明。第一版类型系统支持：

- `event/<name>`；
- `state/<name>`；
- `command/<name>`；
- `metric/<name>`；
- `entity-ref/<name>`；
- `scalar/number`、`scalar/boolean`、`scalar/string`；
- `any`，仅用于兼容适配器。

输出到输入必须同型，或通过显式 `ADAPTER` 能力转换。发布校验拒绝缺失的必需输入、重复实例 key、非法目标、循环同步绑定和未发布依赖。

## 4. 能力的四层产品资产

### 4.1 原子能力

例如计时器、状态机、区域存在感知、相对运动感知、条件判断、连续步行、沿路径移动、速度控制、指标聚合。

### 4.2 组合能力

组合能力由带参数的成员实例和端口绑定构成，例如：

```text
智能信号灯
= 区域存在感知 + 计时器 + 相位状态机 + 灯头输出

门禁
= 凭证感知 + 权限判断 + 门体状态机 + 开关门动作

驾车出行
= 工具可用性 + 出行方式选择 + 上车 + 控制车辆 + 下车
```

### 4.3 能力包

能力包面向某类对象快速装配，例如司机能力包、行人能力包、斯坦福小镇居民大脑包。能力包仍可展开、替换和覆盖参数。

### 4.4 场景模板

场景模板只保存世界、实体、能力实例、绑定、互动和观察器的完整装配，不拥有新的私有能力机制。

## 5. 画块、地图对象与空间语义

地图编辑器需要区分以下图层：

1. 外观画块层：图片、颜色、Emoji 和简单图形；
2. 碰撞与地形层：可通行性、成本和占用；
3. 路径网络层：步行路径、车道、转向连接和运输线路；
4. 区域层：等待区、冲突区、房间、门区和检测区域；
5. 对象层：信号灯、门、读卡器、生成点和世界设备。

“画块模板”是版本化资产；画到地图上的内容是实例。实例引用画块 Revision，并保存位置、方向、参数覆盖和能力绑定。外观与能力分离，因此同一个信号控制能力可以使用图片、颜色或 Emoji 表示。

不是每个画块都默认具有感知。需要响应世界事件的实例显式装配 `SENSOR` 能力，传感输出再连接到控制能力。

## 6. Agent、工具与控制关系

工具模板和工具实例分开管理。第一版关系合同包括：

- `OWNS`：拥有；
- `CARRIES`：携带；
- `MAY_USE`：有权使用；
- `OCCUPIES`：正在占用；
- `CONTROLS`：正在控制；
- `PARKED_AT`：停放于。

工具提供 affordance，而不是把实现复制给 Agent。例如汽车向满足 `MAY_USE + OCCUPIES + CONTROLS` 的人开放加速、制动和沿车道运动。

人类 Agent 保持一个连续身份和记忆，但拥有模式状态：

```text
WALKING -> APPROACHING_TOOL -> ENTERING -> DRIVING -> PARKING -> EXITING -> WALKING
```

模式决定当前动作集合、承载位置和空间规则。交通工具选择能力读取日程紧迫度、距离、工具可用性和偏好，在高层决策频率上选择模式。

## 7. 大脑能力管理

平台级“能力中心”管理所有能力。大脑编辑器只是按 `BRAIN`、`AGENT` 目标筛选并装配认知能力，不能成为能力唯一入口。

现有斯坦福小镇大脑在产品上逐步显示为以下能力包：

- 日程与状态；
- 感知与注意；
- 关联记忆；
- 行动与空间；
- 对话与关系；
- 反思。

第一阶段兼容适配器继续使用现有五个大脑工作流和 Prompt。能力化展示不得改变既有发布 Revision 的工作流内容、哈希和运行结果。

## 8. 多频率时间策略

统一时间配置包含：

- 世界基础 Tick；
- 能力实例运行策略；
- 轨迹采样间隔；
- 数据库投影间隔；
- LLM 触发条件；
- 最大虚拟时长；
- 随机种子。

一车一人默认策略：

| 能力 | 默认策略 |
| --- | --- |
| 世界与连续运动 | 0.2 秒 |
| 距离、速度和碰撞感知 | 0.2 秒 |
| 人车通行决策 | 0.5 秒 |
| 信号灯 | 定时器与事件触发 |
| 日程和出行方式 | 初始化、目标变化或 30 秒 |
| 关键事件记忆 | 事件触发 |
| LLM | 默认关闭，仅关键事件可开启 |
| Frame 提交 | 1 秒，内含 5 个轨迹样本 |

固定随机种子必须同时约束规则随机数、状态机抖动、感知误差和能力脚本的随机源。

## 9. 一车一人的纯配置装配

### 9.1 世界实例

- 右转车道路径；
- 行人过街路径；
- 行人等待区；
- 斑马线冲突区；
- 车辆和行人离开区；
- 机动车和行人信号灯。

### 9.2 实体实例

- 人类 Agent：司机；
- 人类 Agent：行人；
- 工具实例：汽车；
- 地图对象：信号控制器和灯头。

第一项科学验证中司机初始已在车内，避免把“出行方式选择”和“通行博弈”两个变量混合。随后使用同一装配增加从小镇住处选择步行或开车的长时实验。

### 9.3 能力装配

司机：视觉感知、信号感知、风险判断、行人意图推测、共享空间通行决策、驾驶控制、事件记忆。
行人：视觉感知、信号感知、风险判断、司机意图推测、共享空间通行决策、连续步行、事件记忆。
汽车：沿路径运动、速度控制、制动、碰撞外形。
信号灯：计时器、相位状态机、区域存在感知、灯头输出。
互动：共享空间通行协商。
观察：最小距离、TTC、PET、急刹、让行、试探、后退、安全接管。

### 9.4 核心绑定

```text
等待区.行人存在 -> 信号控制器.行人请求
信号控制器.机动车状态 -> 司机.信号感知
信号控制器.行人状态 -> 行人.信号感知
汽车.运动状态 -> 司机/行人.相对运动感知
行人.运动状态 -> 司机/行人.相对运动感知
双方观察 -> 风险判断/意图推测 -> 通行决策
司机.动作 -> 汽车.速度控制
行人.动作 -> 行人.连续步行
双方轨迹 -> 安全观察器
近冲突事件 -> 双方.事件记忆
```

## 10. 产品工作区

平台一级资产中心：

- 能力库；
- 画块库；
- 工具库；
- 能力包；
- 指标库。

实验内部：

- 地图与对象；
- Agent；
- 工具与权限；
- 能力装配；
- 互动关系；
- 时间与运行；
- 观察与结果。

每一个能力详情提供：合同、参数、输入输出、实现、依赖、版本、使用位置和测试台。普通用户配置参数与连接，高级用户编辑状态机和规则，开发者在受控权限下发布 Python 能力。

## 11. 版本与迁移策略

### 11.1 数据资产

能力、画块、工具和能力包全部使用：

```text
容器 -> 唯一 Draft Revision + 多个不可变 Published Revision
```

所有编辑使用乐观锁；发布计算规范化内容哈希；Published Revision 禁止原地修改。

### 11.2 实验定义

现有实验定义和 Run Manifest 为 V1 兼容域。第一阶段能力资产独立落库，不给旧 ExperimentDefinition 增加会参与旧哈希的新默认字段。

能力装配进入实验时采用显式 V2 定义和独立升级入口：

- V1 继续由斯坦福小镇兼容器解释；
- V2 显式包含能力装配快照和毫秒级时间策略；
- 旧发布 Revision 不后台升级；
- 用户复制 V1 实验时可选择“保持 V1”或“升级为可组合 V2 草稿”；
- 升级只生成新 Draft，不修改来源 Revision。

### 11.3 代码与提交

实施使用独立 `codex/capability-composition-platform` 分支。每个可验证阶段形成独立提交边界：

1. 设计与能力合同；
2. 能力资产生命周期；
3. 能力包与装配校验；
4. 画块、工具和关系；
5. V2 实验装配；
6. 多频率运行时；
7. 一车一人能力目录；
8. 产品工作区与端到端验证。

当前工作树已有未提交产品改动，因此不得通过全量暂存把既有改动混入能力平台提交；每次提交前按文件和 hunk 审计。

## 12. 第一阶段完成标准

只有同时满足以下条件才能宣布“一车一人配置化”完成：

1. 能力可创建、编辑、校验、发布、Fork 和版本锁定；
2. 能力端口、参数、状态、触发、实现、权限和可观测合同均可配置；
3. 地图对象、Agent、大脑、工具、互动和观察器都能引用能力 Revision；
4. 画块管理支持外观、语义、能力和实例参数；
5. 感知输出可以在产品中连接到其他能力输入；
6. Agent 可以拥有工具并在步行/驾驶模式间切换；
7. 不同能力可以使用不同执行间隔，LLM 不随物理 Tick 强制调用；
8. 一车一人的所有世界对象、Agent、汽车、能力、绑定和指标均由配置产生；
9. 回放能够显示连续轨迹、信号状态和关键互动事件；
10. 固定种子结果可复现；
11. 斯坦福小镇既有配置、发布、运行和回放回归通过；
12. 数据迁移、API、产品工作区和端到端旅程都有测试证据。

## 13. 第一阶段实现证据矩阵

以下证据用于发布审计。页面文字、设计意图和单一 API 响应不单独作为完成证据；每项都必须同时存在可执行实现与直接回归测试。

| 完成标准 | 可执行实现 | 直接证据 |
| --- | --- | --- |
| 1. 能力完整版本生命周期 | `services/capabilities.py` | `test_capability_draft_publish_conflict_and_fork_lifecycle`、`test_builtin_reseeding_versions_changed_capabilities_and_bundles` |
| 2. 完整能力合同 | `config/capabilities.py`、能力中心表单 | `test_capability_bundle_validates_parameters_ports_and_versions`、`test_capability_workspace_exposes_form_driven_editors` |
| 3. 六类挂载目标 | 空间资产、Agent 扩展、大脑扩展、工具附件和场景互动挂载 | `test_spatial_asset_can_attach_published_perception_capability`、`test_agent_can_own_car_and_use_capability_driven_mobility_choice`、`test_brain_revision_mounts_capability_packages_by_category`、`test_one_car_one_pedestrian_executes_published_capability_graph` |
| 4. 画块外观、语义、能力和实例参数 | `config/spatial_assets.py`、`services/spatial_assets.py`、地图工作区 | `test_builtin_spatial_assets_cover_blocks_objects_zones_and_markings`、`test_public_map_can_opt_into_versioned_spatial_scene_without_changing_v1` |
| 5. 感知输出连接能力输入 | 等待区 `presence` → 信号控制器 → 司机信号输入 | `test_reusable_three_lane_intersection_map_is_seeded_from_spatial_assets`、`test_one_car_one_pedestrian_executes_published_capability_graph` |
| 6. 人拥有并控制交通工具 | Agent 工具授权与出行方式决策扩展 | `test_agent_can_own_car_and_use_capability_driven_mobility_choice`、`test_agent_extension_rejects_mobility_choice_without_vehicle` |
| 7. 多频率时间推进 | `runtime/multirate.py` | `test_multirate_scheduler_runs_dynamics_more_often_than_reasoning`、`test_event_tasks_run_only_when_matching_events_exist` |
| 8. 一车一人纯配置装配 | 版本化三车道路口和 `one-car-one-pedestrian` 场景模板 | `test_versioned_one_car_one_pedestrian_template_applies_by_actor_slots` |
| 9. 连续轨迹、信号与互动回放 | `runtime/replay_v2.py`、`web/static/replay-player.js` | `test_one_car_one_pedestrian_executes_published_capability_graph` 加浏览器端到端验收 |
| 10. 固定输入可复现 | 能力快照、确定性虚拟时钟和可恢复检查点 | `test_one_car_one_pedestrian_executes_published_capability_graph` 的重复运行与中断恢复等值断言 |
| 11. 斯坦福小镇兼容 | `LEGACY_TOWN` 运行路径和旧流程适配器 | `test_new_experiment_defaults_to_unmodified_legacy_town_mode`、`test_stanford_agents_keep_empty_default_extensions`、全量旧引擎回归 |
| 12. 数据、API、工作区与 E2E | 0009–0022 数据迁移、V1 API、四类产品工作区 | 全量 `pytest` 发布门禁和浏览器端到端验收 |

大脑编排的完成判定另有一条硬门禁：发布图必须由 `runtime/workflow_engine.py` 的 `WorkflowExecutor` 真实执行，不能只保存或渲染节点。标准新建节点只提供开始、结束、大模型、代码、选择器、变量赋值、变量聚合和子工作流；旧节点仅用于读取已发布的斯坦福小镇 Revision。`test_agent_completion_result_is_changed_by_the_published_workflow_graph` 直接证明发布图会改变 Agent 的真实完成结果。

## 14. 最终发布验收记录

最终验收使用独立数据库，通过真实产品界面完成“标准四向三车道路口 + 斯坦福小镇 25 人公共人群 + 一车一人场景模板”的创建、校验、发布、运行和回放旅程：

- 发布门禁只计算场景中绑定的 2 个物理角色和 1 个工具，不再用未参演居民的小镇地址阻断组合场景；组合角色与工具的米制初始位置和路线改由场景边界校验负责；
- 发布弹窗显示 0 个阻断项、0 次模型调用、20 秒场景、100 ms 物理 tick、20 个结果快照和 1300 次确定性能力执行；
- revision 002 在 8 秒墙钟时间内完成 20/20 个结果步，空间路口、四组信号灯、行人与车辆均可回放；
- Step 8 显示黄灯，Step 10 显示红灯；驾驶员检查器在信号阻断阶段显示 `YIELD`，证明“等待区感知 → 信号控制 → 司机决策”通过运行数据闭环；
- 运行生成 Replay V2 和 Markdown 报告两个可下载产物；
- 自动化发布门禁最终结果为 `357 passed, 7 skipped`。唯一警告为测试依赖的 Starlette TestClient/httpx2 迁移提示，不影响运行语义。

浏览器验收额外发现并固化了三项回归保护：组合模式不得继承小镇地址校验；发布后的规范化 JSON 必须与 Revision 哈希严格一致；空间角色使用 Phaser Circle 时必须支持选中、跟随和逐步回放，不能假定所有对象都是 Sprite。
