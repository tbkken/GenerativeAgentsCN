# GenerativeAgentsCN

用中文自然语言 Skill 定义智能体与 Game Object 行为的仿真项目。

Studio 用于编辑公共作者资源和实验；Runtime 执行自包含实验包；Replay 从已提交事实读取结果。

架构约束、文件协议、代码入口与验收规则统一见 [AGENTS.md](AGENTS.md)。

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
ga studio serve
~~~

打开 [Studio](http://127.0.0.1:8000/)。健康检查地址是 [api/studio/health](http://127.0.0.1:8000/api/studio/health)。Web 默认使用 var/generative-agents.db 和 var/；启动参数见 `ga studio serve --help`。

依赖统一在 pyproject.toml 声明。仅检查包或回放时安装 `.`；运行仿真使用 `.[runtime]`；完整 Studio 使用 `.[runtime,studio,web]`；开发测试再加 `dev`。源码统一放在 `src/generative_agents/`，命令从安装后的包加载，可在任意工作目录运行。

**创建第一个实验**

1. 在基础配置中创建用户地图、智能体，以及所需 Brain / 子 Skill / 对象 Skill；按场景配置空间素材和人群。
2. 在模型中心配置聊天和向量模型的服务地址、明确的模型 ID、必要的 API Key，并测试连接。
3. 新建实验时显式选择地图、Brain 和模型，以及需要的 Agent / Crowd。选入的资源和依赖立即物理复制到实验工作目录。
4. 在实验草稿内核对地图、初始位置、包内 Skill、模型参数和仿真时间，通过校验后封存。
5. 启动 Run，在实验结果中查看提交进度、质量问题、日志和回放。公共资源后续修改不会自动更新已有实验。

系统不提供默认地图或内置公共 Agent。实验生命周期为 DRAFT → SEALED；封存后若需调整，复制为新的独立实验。具体仿真流程由 Brain Skill 决定，内核提供感知、导航、隔离记忆和经过校验的世界动作。

地图、智能体、人群、技能、大脑、模型页面均支持导入和导出资源包。可以从 config ZIP、实验 `.gaexp` 或 Run `.garun` 中只选择所需资源；图片、脚本和模板随资源保存。完整结构、关联缺失处理与 v2 升级边界见 [统一资源协议与操作说明](docs/unified-resource-protocol.md)。

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
- [教材案例入口](docs/book/sample/README.md)：每个案例独立保存地图、人物、Skill、参数和验收证据。
- [验证命令](AGENTS.md#8-代码入口命令与验证)：架构边界、Python/Node 回归、文件系统安全及安装包验收。

研究来源：[Generative Agents](https://github.com/joonspk-research/generative_agents)、[wounderland](https://github.com/Archermmt/wounderland)。许可见 [LICENSE](LICENSE)。
