# 当前测试与验收指南

测试依据是 [AGENTS.md](../AGENTS.md) 和 [现行合同索引](README.md)，重点验证文件协议、作者资源隔离、世界提交、恢复和只读回放。历史阶段的通过数量不能充当当前分支的结果。

**环境与常用命令**

从仓库根目录、已安装运行依赖的环境执行：

~~~bash
python -m pip install -r generative_agents/requirements-dev.txt
python -m pytest -q -p no:cacheprovider tests/architecture/test_portable_module_boundaries.py tests/test_portable_package_protocol.py
node --test tests/frontend/*.test.cjs
~~~

Node.js 用于前端测试；浏览器专项另需相应 Playwright 环境。普通单元测试通常使用临时目录、临时作者数据库和可控模型替身，不应修改真实 var 工作区或启动正式模型 Run。

**按改动选择回归**

| 改动范围 | 优先入口 |
| --- | --- |
| 四模块边界与包协议 | tests/architecture/test_portable_module_boundaries.py、tests/test_portable_package_protocol.py |
| 实验副本、封存与并发保存 | tests/foundation/test_experiment_resource_editors.py、test_sealed_experiment_run.py、test_package_save_concurrency.py |
| 空间位置与导航 | tests/test_initial_location_contract.py、tests/test_navigation_package.py、tests/foundation/test_navigation.py |
| 对象 Skill、状态与真实 MOVE | tests/test_object_skill_package.py、tests/runtime/test_object_skill_runtime.py、test_move_activity_semantics.py、test_world_commit_event_sync.py |
| 运行恢复、回放隔离与产物来源 | tests/foundation/test_checkpoint_resume_console.py、test_windows_projection_recovery.py、test_replay_execution_isolation.py、tests/test_portable_artifact_provenance.py |
| 质量结果 | tests/test_quality_projection.py、tests/foundation/test_run_acceptance_views.py |
| 前端列表、作用域、异步状态与回放 | tests/frontend/*.test.cjs，按改动选择相关文件 |
| 密钥与资产边界 | tests/runtime/test_secret_protection.py、test_asset_store.py |

**退役后的安全回归**

数据库表断言以 tests/foundation/test_database_and_service.py 为准：ORM 与 Alembic 必须恰好对应 11 张 Studio 表及迁移元数据。旧 Revision、数据库 Run 队列、数据库日志/回放/产物和演示入口断言已删除。

仍有效的检查按当前合同迁移：

| 安全目标 | 当前测试 |
| --- | --- |
| 原生 symlink、跨 Run 文件链接、产物内容身份、Attempt 日志归属与有界 UTF-8 读取 | tests/architecture/test_portable_storage_security.py |
| Windows 根目录、中间目录、末级目录和跨目录 junction | tests/architecture/test_windows_reparse_storage_boundaries.py |
| Agent 初始坐标、语义地址、空间树、碰撞和唯一身份 | tests/foundation/test_package_placement_validation.py |
| 素材幂等、密钥脱敏与 HTTP 错误 | tests/foundation/test_catalog_services.py、test_web_api.py |
| 包内资源完整性、已提交帧、恢复和只读回放 | tests/test_portable_package_protocol.py、tests/foundation/test_replay_execution_isolation.py、test_checkpoint_resume_console.py |

[发布 CI](../.github/workflows/native-symlink-release-gate.yml)在 Linux、Windows 分别运行原生符号链接与安装包验收，覆盖 PR、main/master 和 codex 分支推送。原生门禁通过 [run_symlink_release_gate.py](../tools/run_symlink_release_gate.py)运行迁移后的七条指定断言，要求全部执行、零跳过；普通本机 pytest 因权限不足而跳过不等于门禁通过，不能放宽 CI 的能力检查。

现行行为通过实际代码验证。tests/legacy/ 保留共享内核的 RNG 隔离、检查点、记忆、模型重试和导入副作用等回归，目录名称不表示可以删除。

~~~bash
python -m pytest tests -q -p no:cacheprovider
python tools/run_symlink_release_gate.py
node --test tests/frontend/*.test.cjs
~~~

**wheel 安装验收**

~~~bash
python -m pip wheel . --no-deps --no-build-isolation -w dist
python tools/verify_wheel.py dist/generative_agents_cn-0.1.0-py3-none-any.whl
~~~

[verify_wheel.py](../tools/verify_wheel.py)逐字节核对 Python 源码、种子、素材、当前 Web 静态文件与 village 资源，拒绝缺失或额外的旧文件，再将 wheel 安装到临时目录，从仓库外调用实际的 `ga` 可执行入口。

安装验收通过 Studio HTTP 接口创建和编辑地图、Agent、Brain、对象 Skill 与模型配置，物理导入实验并封存；作者资源后续编辑不改变已导入副本。随后删除临时作者工作区，执行 CLI 实验校验/封存、Run 创建/启动/暂停/封存/异地恢复/重跑/取消请求、Replay 摘要/时间线/状态读取。检查同一 Run 的新 Attempt、已提交帧不变、对象状态恢复和回复仅在下一轮投递一次。取消命令检查控制请求写入；执行中取消的中断行为由运行控制回归覆盖。

这项验收只替换外部模型为本地确定性 HTTP 服务，Studio、文件协议、CLI、MCP、检查点和 Replay 均使用安装后的真实代码；不连接正式模型，不接触真实 var，不代表浏览器视觉验收或真实模型行为验收。CI 的安装包任务还运行 Python 与 Node 全套回归。

**验收证据**

文件协议测试应覆盖包身份、完整性、安全解压、资源闭包、状态提交和恢复幂等。Replay 测试应证明它从已提交事实读取，且不依赖 Studio、Brain 或模型调用。模型替身的通过证明机制与合同，不能证明真实模型一定遵守案例 SOP。

教材配置和运行验收按 AGENTS.md 由主 Agent 通过浏览器执行；新发现缺陷先报告并等待针对该问题的明确处理决定。批准修复并完成测试后，重启 Web，再回到原浏览器路径验收。记录具体 Experiment / Run / Attempt / Step、输入输出、质量条目与导出哈希。

新增记录写清实际命令、结果、测试替身、浏览器范围和未验证项。未完成事项与已验证结果分开；删除过期文档不等于关闭缺陷。
