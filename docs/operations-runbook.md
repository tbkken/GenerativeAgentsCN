# 当前运行手册

适用入口：ga CLI 与 ga studio serve。安装见 [README](../README.md)，模型连接见 [模型配置](model-configuration.md)，规则见 [AGENTS.md](../AGENTS.md)。本页按当前源码组织；不沿用旧数据库 Run 或 legacy import 流程。

**启动 Studio**

从仓库根目录、已安装依赖的 Python 环境启动：

~~~bash
ga studio serve --database-url sqlite:///var/generative-agents.db --var-dir var --host 127.0.0.1 --port 8000 --max-concurrent-runs 2
~~~

浏览器入口为 http://127.0.0.1:8000/，健康接口为 GET /api/studio/health。健康检查只证明 Web 与 Studio 数据库可用，不证明模型连接成功或某个 Run 可恢复。

命令行参数也可由 GA_DATABASE_URL、GA_VAR_DIR、GA_HOST、GA_PORT、GA_MAX_CONCURRENT_RUNS、GA_WEB_LOG_LEVEL 提供。Web 固定一个 Uvicorn worker；FileRunSupervisor 管理子进程和有界执行槽位。

Windows 的 [restart-web.bat](../restart-web.bat) 优先使用仓库 .venv 中的 Python，缺省回退到 PATH 中的 python 重启端口 8000 上的本项目 Web，输出到 var/logs/。运行前确认该解释器环境正确；若有活动 Run，先按 AGENTS.md 在 UI 中安全暂停，再重启和核验。它不是模型服务启动器。

**配置与数据位置**

| 内容 | 当前默认位置或来源 |
| --- | --- |
| Studio 作者数据库 | var/generative-agents.db；以启动参数为准 |
| 实验与 Run 包 | var/packages/ 下，由 Studio 目录索引定位；包内 ID 才是身份 |
| 包内实验配置 | manifest.json 的 entrypoints 指定 world、agents、skills、models 等文件 |
| Run 身份、状态与控制 | run.json、status.json、control.json |
| Attempt 与已提交事实 | attempts/、frames/、checkpoints/、traces/ 等 Run 自有文件 |
| 本机模型凭据映射 | var/model-credentials.json；密文在 Studio Secret 存储，密钥由 GA_MASTER_KEY 或 var/master.key 提供 |
| 主进程日志 | 使用重启脚本时为 var/logs/；子进程日志保存在对应 Run 中 |

首次启动由 Studio 初始化作者表和 Skill 种子。没有默认地图或公共 Agent，必须由用户创建并选择。模型与实验配置在 Studio 明确编辑并复制入包。

现行 schema 准备逻辑会先备份不兼容的本地 SQLite 作者库，再重建当前基线；这不是旧 Run 的兼容迁移。对已有工作区升级前保留备份，不用旧表结构判断文件包是否存在。

**文件命令**

ga 由 editable 安装注册；也可以将下列 ga 替换为 python -m generative_agents.adapters.cli.main。以下路径都是用户准备的示例，命令不自动生成案例内容。

~~~bash
ga experiment seal ./my-experiment ./my-experiment.gaexp
ga experiment validate ./my-experiment.gaexp
ga run create ./my-experiment.gaexp ./my-run --steps 24
ga run resume ./my-run
ga run status ./my-run
ga replay summary ./my-run
ga replay timeline ./my-run --start 1 --end 12
ga replay state ./my-run 12
~~~

run create 只创建 Run；run resume 执行该目录。要一次创建并执行，可改用 ga run start ./my-experiment.gaexp ./my-run --steps 24。目标 Run 目录须由该次创建使用；真实执行会调用已配置的模型。

在另一个终端请求控制：

~~~bash
ga run pause ./my-run
ga run cancel ./my-run
~~~

控制请求写入 Run 文件；应等待 status.json 确认实际状态后再备份或恢复。暂停后使用 ga run resume ./my-run，保持 run_id 并创建新的 attempt_id。已经完成的 Run 使用重跑命令生成新 run_id。

~~~bash
ga run seal ./my-run ./my-run.garun
ga run resume ./my-run.garun --destination ./resumed-run
ga run rerun ./my-run.garun ./another-run --steps 24
~~~

封存应在 Runtime 停止写入后进行；从 .garun 续跑先安全解压到指定目录，并经完整性校验和单写者锁。已完成包不能靠续跑增加业务步骤；重跑使用其内嵌实验。

**观察状态和质量**

Run 状态包括 CREATED、QUEUED、RUNNING、FINALIZING、PAUSED、CANCELLED、COMPLETED、FAILED。实验 DRAFT/SEALED 与 Run 执行状态分别显示。COMPLETED 只说明执行结束，质量问题及特定业务结论应查看报告和评估记录。

排查时记录 experiment_id、run_id、attempt_id、最后提交 Step 和实际错误。包校验失败时先保留原件；不要手动改身份、Step 进度或完整性哈希来消除报错。模型问题检查当前包的模型 ID、API Base URL、凭据环境变量和 Trace；公共模型配置的修改不会自动修正已有包。

**备份与恢复**

实验包和 Run 目录/.garun 是运行与回放事实，应完整保留。活动 Run 先安全暂停并确认提交边界；不要把尚在写入的目录复制成“最终结果”。使用封存命令生成可校验的归档，并保留生成边界、文件大小和 SHA256。

Studio 作者库需要单独备份。最简单的本机备份是在安全暂停 Run、停止 Web 后保存作者数据库、公共资源文件、凭据映射及主密钥；数据库存在 WAL 时使用一致性备份，不能只复制正在变化的主文件。包可以脱离作者库运行，但另一台主机仍须安装依赖、提供模型服务与包所声明的凭据。

数据库中的包位置索引可从清单重建；公共作者资源及其密钥不能靠 Run 回放反向恢复。恢复实际实验前验证包身份、完整性、最近完整检查点与下一提交 Step。一次本机测试不等于已经完成异机复现验收。
