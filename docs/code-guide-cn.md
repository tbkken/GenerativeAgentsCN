# GenerativeAgentsCN 中文代码导览

先读 [AGENTS.md](../AGENTS.md) 和 [文件包架构](capability-composition-platform-design.md)。本导览按当前源码入口组织；历史 Revision 服务仍存在于源码中，但不构成当前实验模型。

**先区分三类内容**

公共作者资源由 Studio 按稳定 ID 编辑。实验选入资源时立即复制完整依赖，拥有自己的地图、Agent placement、Skill、模型参数和素材；生命周期为 DRAFT → SEALED。Run 再复制整个实验，并保存每次 Attempt、已提交 StepResult、检查点、Trace 和文件记忆。

实验身份在 manifest.json 的 experiment.experiment_id；Run 身份在 run.json 的 run_id；Attempt 身份在各 attempt.json。目录名用于定位，不能替代身份。SPO 与 structured_payload 一起构成世界事实，模型文本和记忆不直接驱动画面。

**从这些入口开始**

| 要理解的事情 | 入口与后续调用 |
| --- | --- |
| Web 启动 | [web/main.py](../generative_agents/web/main.py) → [ga_studio/web.py](../generative_agents/ga_studio/web.py) |
| 通用命令行 | [cli/main.py](../generative_agents/cli/main.py)，由 pyproject.toml 注册 ga |
| 公共资源编辑 | [ga_studio/resource_api.py](../generative_agents/ga_studio/resource_api.py)、resources.py、model_services.py |
| 创建、编辑和封存实验 | [ga_studio/workspace.py](../generative_agents/ga_studio/workspace.py) → builder.py；包内编辑见 experiment_resources.py |
| Web 运行和结果适配 | [web/portable_api.py](../generative_agents/web/portable_api.py) |
| 文件运行生命周期 | [ga_runtime/service.py](../generative_agents/ga_runtime/service.py)、supervisor.py、executor.py |
| 文件回放 | [ga_replay/reader.py](../generative_agents/ga_replay/reader.py) |

**四个模块的边界**

| 模块 | 核心文件 | 作用 |
| --- | --- | --- |
| ga_protocol | models.py、io.py、validation.py、locking.py、navigation.py、recovery.py、quality.py | 清单、完整性、安全路径、空间编译、恢复边界与事实投影合同 |
| ga_studio | workspace.py、builder.py、resources.py、catalog.py、schema.py | 作者资源数据库、一次性导入、实验副本和可重建目录索引 |
| ga_runtime | package.py、service.py、executor.py、control.py、memory.py、supervisor.py | 只用文件加载、执行、控制、恢复和保存私有记忆 |
| ga_replay | reader.py | 读取已提交事实，返回概览、时间线、状态和质量信息 |

数据库依赖属于 Studio。当前作者侧仍复用 services/、persistence/ 和 skills/database.py；这是现有源码组织，不是允许 Runtime 或 Replay 回查作者库的例外。

**一次创建与执行**

1. Studio 的 ExperimentWorkspaceService 读取用户明确选择的公共资源；ExperimentPackageBuilder 复制资源、展开 Skill 闭包，写清单和完整性记录。
2. 草稿编辑修改实验包内副本。封存校验生成 .gaexp；公共资源改变不会隐式更新这个包。
3. RunService 创建新 Run 目录，复制实验并生成 run.json。Studio 的 FileRunSupervisor 启动 ga CLI 的 run resume 子进程；手动 CLI 也使用同一文件运行实现。
4. executor.execute_run_directory 获得 worker.lock，读取包内模型与 Skill，选择完整恢复点，创建 Attempt，并装配执行上下文。
5. executor 仍调用 [start.py](../generative_agents/start.py) 的 build_runner / SimulationRunner。它们继续复用 Game、Agent、Maze 和文件提交器。
6. 每步按内核顺序处理 Agent 动作、实际移动和 Game Object Skill；冻结 StepResult 后提交帧和检查点，再推进可见状态。Brain 决定调用哪些能力，内核不固定业务 SOP。
7. ReplayReader 读取该 Run 的内嵌实验与已提交帧；浏览器只是这份事实的展示层。

**仍在使用的共享代码**

| 目录或文件 | 当前用途 |
| --- | --- |
| start.py、modules/game.py、modules/agent.py、modules/maze.py | 仿真步推进、参与者和空间计算 |
| runtime/brain.py、object_skills.py、capabilities.py、iteration.py | Brain、对象 Skill、身份隔离 MCP 与轮次上下文 |
| runtime/results.py、result_collector.py、commit.py、checkpoint.py、frame_store.py、file_result_projector.py | StepResult、文件提交与恢复支持 |
| runtime/model_trace.py、modules/model/ | 模型调用、取消、重试与过程审计 |
| ga_runtime/memory.py | Run 自有、身份隔离的文件记忆 |
| skills/registry.py、runtime.py、dependencies.py 与 data/skills/ | 解析、递归依赖、自然语言/脚本 Skill；源码 Skill 是作者种子，运行使用包内副本 |
| config/、services/maps.py、services/spatial_assets.py、services/catalog.py、persistence/ | 配置校验及 Studio 作者侧支持 |
| frontend/static/assets/village/、web/static/replay-assets/ | 当前地图导入或回放仍引用的资源 |

阅读到历史命名的字段或方法时，要继续追踪实际参数与持久化位置。例如包内内容哈希不等于公共作者资源的业务 Revision。不要按目录名整批删除仍被当前链调用的组件。

**前端入口**

正式页面是 [web/static/experiment-console.html](../generative_agents/web/static/experiment-console.html)。console-api.js 管理实验与结果请求；resource-scope.js 隔离公共资源和当前实验副本；map-workspace.js / map-editor-v2.js / map-navigation.js 负责地图；crowd-workspace.js、skill-workspace.js、model-workspace.js 负责作者编辑；replay-player.js 展示已提交事实。

定位前端问题时，先确认当前 Experiment、Run、Attempt 和请求代次，再追踪加载、渲染和事件绑定。不要用已删除的 docs HTML 原型解释正式页面。

**工程边界与测试**

旧数据库业务链、34 个历史 ORM 映射、旧包导出、旧 Manifest/发布预检、独立回放压缩入口和两套演示页面已经退役。当前数据库只映射 11 张 Studio 作者资源和包目录索引表；Run 的状态、队列、日志、帧和产物继续由文件协议管理。

start.py 保留 ga_runtime 使用的仿真循环与 build_runner，不再提供旧 worker 命令行。地图导入仍由 services/maps.py 的 materialize_validated_world 校验当前作者资源，再由 Studio 物理复制；不保留运行期地图外键或 Brain Revision。

安全测试按现行入口维护，见 [测试指南](test-strategy.md)。修改合同后，选择对应的文件包、隔离、恢复或前端测试；教材案例还需遵守 AGENTS.md 的原浏览器路径复验要求。
