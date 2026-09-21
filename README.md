[English](README_en.md)

# GenerativeAgentsCN

用中文自然语言 Skill 定义智能体与 Game Object 行为的仿真项目。Studio 用于编辑公共作者资源和实验；Runtime 执行自包含实验包；Replay 从已提交事实读取结果。

当前规则见 [AGENTS.md](AGENTS.md)，完整入口见 [文档索引](docs/README.md)。项目源于 Generative Agents / wounderland，现行运行方式以本页为准。

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
python -m pip install -r generative_agents/requirements.txt
python -m pip install -e .
python -m generative_agents.web.main
~~~

打开 [Studio](http://127.0.0.1:8000/)。健康检查地址是 [api/studio/health](http://127.0.0.1:8000/api/studio/health)。Web 默认使用 var/generative-agents.db 和 var/；启动参数、备份及恢复见 [运行手册](docs/operations-runbook.md)。

当前 pyproject.toml 没有声明完整运行依赖，单独执行 editable 安装不能代替 requirements 安装。uv.lock 也不是完整运行环境的锁定清单。

**创建第一个实验**

1. 在基础配置中创建用户地图、智能体，以及所需 Brain / 子 Skill / 对象 Skill；按场景配置空间素材和人群。
2. 在模型中心配置聊天和向量模型的服务地址、明确的模型 ID、必要的 API Key，并测试连接。详见 [模型配置](docs/model-configuration.md)。
3. 新建实验时显式选择地图、Brain 和模型，以及需要的 Agent / Crowd。选入的资源和依赖立即物理复制到实验工作目录。
4. 在实验草稿内核对地图、初始位置、包内 Skill、模型参数和仿真时间，通过校验后封存。
5. 启动 Run，在实验结果中查看提交进度、质量问题、日志和回放。公共资源后续修改不会自动更新已有实验。

系统不提供默认地图或内置公共 Agent。实验生命周期为 DRAFT → SEALED；封存后若需调整，复制为新的独立实验。具体仿真流程由 Brain Skill 决定，内核提供感知、导航、隔离记忆和经过校验的世界动作。

**文件协议与命令行**

| 模块 | 职责 | 事实来源 |
| --- | --- | --- |
| ga_protocol | 清单、身份、完整性、安全归档、空间索引与公共文件合同 | 包内文件 |
| ga_studio | 公共作者资源、实验编辑、包构建和可重建目录索引 | 作者数据库与实验工作目录 |
| ga_runtime | 执行、监督、控制、提交和恢复 | Run 内嵌实验与 Run 文件 |
| ga_replay | 概览、时间线、状态归约与质量读取 | Run 内已提交事实 |

数据库只属于 Studio 作者侧；Runtime 和 Replay 不以数据库为事实来源。旧目录中仍有被当前入口复用的底层组件，实际调用关系见 [代码导览](docs/code-guide-cn.md)。

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

暂停的 Run 可用 ga run resume ./my-run 续跑；已完成 Run 用 ga run rerun ./my-run ./another-run 重跑。从 .garun 续跑须提供 --destination 指定可写目录。完整命令和状态含义见 [运行手册](docs/operations-runbook.md)。

**阅读与验证**

- [文档索引](docs/README.md)：统一查阅现行合同、操作指南和案例证据。
- [文件包架构](docs/capability-composition-platform-design.md)与[实验工作区 UX](docs/experiment-resource-composition-ux.md)。
- [教材案例入口](docs/book/sample/README.md)：每个案例独立保存地图、人物、Skill、参数和验收证据。
- [测试指南](docs/test-strategy.md)：当前文件包专项、前端测试及旧测试的适用边界。

研究来源：[Generative Agents](https://github.com/joonspk-research/generative_agents)、[wounderland](https://github.com/Archermmt/wounderland)。许可见 [LICENSE](LICENSE)。
