# GenerativeAgentsCN

用中文自然语言 Skill 定义智能体与 Game Object 行为的仿真项目。

Studio 用于编辑公共作者资源和实验；Runtime 执行自包含实验包；Replay 从已提交事实读取结果。

架构约束、文件协议、代码入口与验收规则统一见 [AGENTS.md](AGENTS.md)。

在根目录的 [services.toml](services.toml) 配置 Studio 和 SimWorld 服务地址。在「基础配置 → 地图」新建 3D 地图，填写本系统的名称、稳定键和用途说明，再选择 SimWorld 场景。创建后地图进入本系统列表，打开时仅查看远端实景；创建实验和更换地图统一从本系统地图目录选择。实验复制本地地图元数据和场景 ID，不复制远端场景内容。公共地图和实验共用在线查看器。3D 运行通过服务执行动作，Run 保存已提交的实际位置、动作和对话。服务不在线时可读历史事实，不能打开 UE 实景。[3D 集成与能力边界](docs/simworld-3d-scene-design.md)

本机查看东京街区，双击 [start-simworld.bat](start-simworld.bat)。控制台和 UE 游戏窗口会保持打开；关闭网页只释放相机。再次双击会替换所配置端口上属于该服务的进程和 UE。日志在 `%LOCALAPPDATA%\GenerativeAgentsCN\SimWorld-<端口>\logs`。

**修改端口和服务地址**

| [services.toml](services.toml) 配置项 | 生效位置 |
| --- | --- |
| `[studio]` 的 `host`、`port` | Web 监听、`restart-web.bat` 的健康检查与打开地址、`ga studio serve` |
| `[simworld]` 的 `url` | 本机 UE 启动端口，以及 Studio 选图、实时画面、相机和角色服务的连接地址 |
| `[models]` 的 `chat`、`embedding`、`decision` | 新建模型时的建议地址；空模型库初始化时使用其中的生成式和向量地址 |
| `[decision_compiler]` | Studio 从判别式 Skill 正文解析输出结构时实际调用的生成式 LLM；公共 Skill 和实验草稿共用 |

例如，把 `[simworld]` 下的 `url` 改为 `"http://127.0.0.1:8790"`，网页和启动脚本就会使用同一新端口。换端口前先停止旧端口的实例，然后重启场景服务和 Web。远程部署时填远程 HTTP(S) 地址，由 GPU 主机管理场景服务，本机只启动 Studio。已保存的模型在模型中心修改；已选入实验和 Run 的模型连接保留其快照。UE 相机的内部控制端口由服务按会话自动分配。

源代码安装默认读取本项目的文件；仅安装 wheel 时使用随包的同一份默认配置。要使用本机专用配置，将 `GA_SERVICES_CONFIG` 设为另一份 TOML 的绝对路径，只需写下要覆盖的项。临时 Web 参数按「`--host/--port` → `GA_HOST/GA_PORT` → TOML」优先；`GA_SIMWORLD_URL` 优先于 TOML，未指定完整 URL 时也可用 `GA_SIMWORLD_PORT` 临时覆盖本机直连端口。不要把 API Key 或 `GA_SIMWORLD_TOKEN` 写入此文件。

**安装与启动**

在仓库根目录操作。需要 Python 3.11 或更高版本；前端回归测试另需 Node.js。

~~~bash
python -m venv .venv
~~~

Windows PowerShell 激活环境：

~~~powershell
.\.venv\Scripts\Activate.ps1
~~~

macOS / Linux 激活环境：

~~~bash
source .venv/bin/activate
~~~

安装运行依赖并注册 ga 命令：

~~~bash
python -m pip install -e ".[runtime,studio,web]"
python tools/link_local_source.py
ga studio serve
~~~

打开终端显示的 Studio 地址，健康检查位于同一地址下的 `/api/studio/health`。`ga studio serve` 默认使用当前工作目录下的 `var/generative-agents.db` 和 `var/`；启动参数见 `ga studio serve --help`。

Web 页面、API 和回放共用一个监听端口。Runtime 能力通过身份绑定的进程内接口调用，默认 SQLite 数据库使用文件，均无需额外监听端口。

Windows 也可使用根目录的 `restart-web.bat` 启动或重启 Web。首次使用先安装项目；以下命令无需激活虚拟环境：

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[runtime,studio,web]"
.\.venv\Scripts\python.exe tools/link_local_source.py
.\restart-web.bat
~~~

脚本优先使用仓库的 `.venv\Scripts\python.exe`，没有该环境时才使用 PATH 中的 Python，并在停止旧服务前检查项目和 Web 依赖。若提示 `No module named 'generative_agents'`，表示所选 Python 尚未安装项目，按脚本显示的安装命令补齐即可。后台进程提前退出时会立即显示错误和日志路径；仅进程创建成功不代表服务可用，须看到健康检查通过的 `[OK]` 提示。

看到 `[OK]` 后，窗口会显示本次 Web 启动以来的标准输出和错误日志，并持续显示新内容。按 `Ctrl+C` 或关闭窗口即可结束查看，Web 服务会继续在后台运行。自动化调用可用 `.\restart-web.bat --no-follow`，在健康检查通过后立即返回。

`restart-web.bat` 默认使用仓库的 `var/studio/` 活动数据目录：数据库为其中的 `generative-agents.db`，素材和实验包也从该目录读取，启动日志与进程记录分别位于 `logs/` 和 `web.pid`。设置 `GA_VAR_DIR` 可改用其他完整数据目录，默认数据库路径随之切换；显式设置 `GA_DATABASE_URL` 时优先使用该数据库地址。脚本中的相对路径均以仓库根目录为基准。`data/studio/` 是原始数据快照，脚本不会自动读取或导入它。

直接使用 CLI 访问启动脚本的同一份活动数据时，在仓库根目录运行 `ga studio serve --var-dir var/studio --database-url sqlite:///var/studio/generative-agents.db`；不指定这两个参数的 CLI 仍使用 `var/` 默认路径。

**可选：在 Windows 本机运行 3D 场景服务**

场景服务以独立的 Python 安装包 `ga-simworld-service` 发布。本机启动器默认用本项目的 `.venv`，也可用 `GA_SIMWORLD_PYTHON` 指向已安装服务的其他 `python.exe`。使用预制地图、遥控相机无需官方 `simworld` Python SDK，也不读取 SDK 或场景服务的源码目录。

新建环境时，取得场景服务的 wheel 后安装一次即可。例如把安装包放在 `dist\simworld\`，在项目根目录执行：

~~~powershell
.\.venv\Scripts\python.exe -m pip install ".\dist\simworld\ga_simworld_service-0.2.2-py3-none-any.whl[windows-launcher]"
.\start-simworld.bat
~~~

UE 游戏运行环境和地图 PAK 单独安装。默认从本项目的上一级目录寻找 `SimWorld_Base_Windows\Base20260201\Windows\SimWorld\Binaries\Win64\SimWorld.exe` 和 `SimWorld_Windows\AdditionEnvironmentPaks\Windows`。也可设置 `GA_PREVIEW_UE_EXE` 为游戏可执行文件、`GA_SIMWORLD_MAP_PAKS` 为地图目录。观看东京街区需要与该运行环境匹配的 `pakchunk2007-Windows.pak`，无需 UE Editor。

在线行动、恢复和实景回放需要场景服务 0.2.2。角色由服务端在所选关卡生成。Runtime 记录真实动作回执和位姿，观察者可在「实验结果 → 仿真回放」查看已提交步、跟随人物或看本步空间事实。SimWorld 服务不可用时仍可读 Run 的文字事实；历史实景取决于服务能否找到该 Run 对应的关卡。

每次 editable 安装后，使用仓库 `.venv` 中的 Python 执行 `tools/link_local_source.py`，将源码关联改为相对于 `site-packages` 的路径；Windows 下为 `../../../src`。该关联按虚拟环境位置解析，与启动工作目录无关。`restart-web.bat` 也按自身目录选择 Python。pip 生成的 `ga` 启动器和虚拟环境的基础 Python 仍有安装位置要求；跨机器搬迁时应重新创建虚拟环境，再执行上述安装步骤。

依赖统一在 pyproject.toml 声明。仅检查包或回放时安装 `.`；运行仿真使用 `.[runtime]`；完整 Studio 使用 `.[runtime,studio,web]`；开发测试再加 `dev`。源码统一放在 `src/generative_agents/`，命令从安装后的包加载，可在任意工作目录运行。

**创建第一个实验**

1. 在基础配置中创建用户地图、智能体，以及所需 Brain / 子 Skill / 对象 Skill；按场景配置空间素材和人群。
2. 在模型中心配置生成式模型和向量模型的服务地址、明确的模型 ID、必要的 API Key，并测试连接。
3. 新建实验时显式选择地图、Brain 和模型，以及需要的 Agent / Crowd。选入的资源和依赖立即物理复制到实验工作目录。
4. 在实验草稿内核对地图、初始位置、包内 Skill、模型参数和仿真时间，通过校验后封存。
5. 启动 Run，在实验结果中查看提交进度、质量问题、日志和回放。公共资源后续修改不会自动更新已有实验。

系统不提供默认地图或内置公共 Agent，也不自动填充 Skill / Brain。实验生命周期为 DRAFT → SEALED；封存后若需调整，复制为新的独立实验。具体仿真流程由 Brain Skill 决定，内核提供感知、导航、隔离记忆和经过校验的世界动作。

创建入口仅区分“技能”和“大脑”，生成式技能能组合子技能，无需选择“技能包”类型。技能正文用 `$child-skill` 引用子技能、`@world-perceive` 等标记引用接口，并以自然语言描述调用条件、输入与结果用途。技能页面的“文件与调用关系”展示实时正文引用、递归子技能和实际脚本。详见 [Skill 编写规范](docs/skill-authoring.md)。

选择决策模型类型后，Studio 使用 [services.toml](services.toml) 的 `[decision_compiler]` 解析 Skill 正文，提取是/否、多分类或评分问题及其候选项。要更换解析模型，修改该节的 `base_url`、`model` 和相应的凭据名称，再重启 Web。模型中心无需勾选解析模型。保存后的问题结构随 Skill 进入实验快照；实验运行时直接调用实验指定的 Laya，不再请求解析 LLM。[配置和使用决策 Skill](docs/skill-authoring.md#llm-类型与决策问题)

技能详情的“试运行”只需输入自然语言情境；生成式技能直接接收输入原文，决策技能直接把输入发送给决策模型，无需填写运行时上下文 JSON。纯文本技能可独立试运行；依赖环境接口时，选择已有实验及其中的 Agent，系统从实验初始场景装配真实地图、身份、位置、时间和感知限制。公共技能按 LLM 类型选择可用模型，实验内的技能使用包内模型配置。修改技能后先保存，再试运行当前内容及其子技能。

试运行使用独立临时场景和记忆，不读取已有 Run 的当前现场，也不启动全体 Agent 的 Brain。原实验和已有运行不受影响；只读技能无需提交动作即可完成。结果提供执行事件、子技能、接口输入输出及实际模型请求的只读审计，并分别展示逻辑调用与含重试的物理尝试数；执行事件数量不等于模型调用次数。接口接受动作不等于世界已提交，试运行不生成正式的 World Commit。

Skill 之间默认传递自然语言；上层 Skill 也可引用已完成子调用的结果原文，由运行器直接交给下一技能。目的、顺序和分支仍由 Brain／SOP 决定，接口参数、身份权限与运行事实继续严格校验。两种输入方式及引用范围见 [Skill 编写规范](docs/skill-authoring.md#skill-之间的输入与结果)。

地图、智能体、人群、技能、大脑、模型页面均支持导入和导出资源包。可以从 config ZIP、实验 `.gaexp` 或 Run `.garun` 中只选择所需资源；图片、脚本和模板随资源保存。完整结构、关联缺失处理与 v2 升级边界见 [统一资源协议与操作说明](docs/unified-resource-protocol.md)。

实验中心和实验详情提供“导出资源包”，默认包含完整实验及全部已有运行结果；取消“包含运行结果”可只下载 `.gaexp`。在另一套系统的实验中心点击“导入实验包”，选择导出的 ZIP、`.gaexp` 或 `.garun`，即可导入实验及包内运行记录，继续查看结果和回放。含结果的导出需先暂停或结束活动 Run；导入不自动启动运行，模型地址和密钥需在目标机器配置。详见 [实验与运行结果交换](docs/unified-resource-protocol.md#实验与运行结果交换)。

**文件协议与命令行**

| 模块 | 职责 | 事实来源 |
| --- | --- | --- |
| ga_protocol | 清单、身份、完整性、安全归档、空间索引与公共文件合同 | 包内文件 |
| ga_studio | 公共作者资源、实验编辑、包构建和可重建目录索引 | 作者数据库与实验工作目录 |
| ga_runtime | 执行、监督、控制、提交和恢复 | Run 内嵌实验与 Run 文件 |
| ga_replay | 概览、时间线、状态归约与质量读取 | Run 内已提交事实 |

数据库只属于 Studio 作者侧；Runtime 和 Replay 不以数据库为事实来源。模块依赖见 [职责与模块边界](AGENTS.md#1-职责与模块边界)，源码定位见 [代码入口](AGENTS.md#8-代码入口命令与验证)。

以下路径是示例，需要已有的完整实验目录和可用模型连接：

~~~bash
ga --help
ga experiment seal ./my-experiment ./my-experiment.gaexp
ga experiment validate ./my-experiment.gaexp
ga run start ./my-experiment.gaexp ./my-run --steps 24
ga run status ./my-run
ga replay state ./my-run 12
ga run seal ./my-run ./my-run.garun
~~~

暂停的 Run 可用 ga run resume ./my-run 续跑；已完成 Run 用 ga run rerun ./my-run ./another-run 重跑。从 .garun 续跑须提供 --destination 指定可写目录。恢复约束见 [文件协议](AGENTS.md#3-文件协议身份与安全)，命令参数见 `ga run --help`。

**阅读与验证**

- [架构与开发约定](AGENTS.md)：统一查阅系统约束、资源生命周期、文件协议和代码入口。
- [教材案例入口](https://github.com/tbkken/GenerativeAgentsCN-Book/blob/main/book/sample/README.md)（私有仓库）：每个案例独立保存地图、人物、Skill、参数和验收证据。
- [验证命令](AGENTS.md#8-代码入口命令与验证)：架构边界、Python/Node 回归、文件系统安全及安装包验收。

研究来源：[Generative Agents](https://github.com/joonspk-research/generative_agents)、[wounderland](https://github.com/Archermmt/wounderland)。许可见 [LICENSE](LICENSE)。
