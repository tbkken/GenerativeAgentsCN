# 回放与 Skill 只读展示加载失败：工程证据

日期：2026-09-13。工程修复及自动化验证已完成；浏览器重启与原路径验收由主 Agent 记录在 `replay-skill-loading-regression-2026-09-13.md`。

## 根因

对象 Skill 改造把当前可执行注册表字段由 `passive_roots` 改为 `object_roots`，当前 `SkillPackageRegistry` 会拒绝前者。该变更已有明确文档记录：`game-object-skill-runtime.md:36`、`game-object-skill-verification-2026-09-13.md:70`。现场此前 15 个 Run 的内嵌注册表仍有 `passive_roots: []`，即使没有对象 Skill，也会被当前执行合同拒绝。

回放错误调用链：`ReplayReader.__enter__ → _validated_directory → validate_run_directory → validate_experiment_directory → SkillPackageRegistry.model_validate`。实验 Skill 列表、正文和地图只读入口经 `ExperimentResourceEditor.package` 也调用了同一个执行校验。两条只读调用链共同把执行合同错误地作为查看封存事实的前置条件。

这是对象能力字段变更直接触发的回归，底层问题是只读展示与执行校验未分离；不是包文件丢失，也不是需要重新上传公共 Skill。

## 修复范围

- `ga_protocol/validation.py`、`ga_protocol/__init__.py`：提供 `validate_experiment_integrity` 与 `validate_run_integrity`。保留清单身份、安全且存在的入口、全部物理内容哈希、Run 内嵌实验 ID/根哈希、封存 Run 外层完整性。Skill 声明在只读层作为包内原始内容核验哈希，不作旧字段转换。
- `ga_replay/reader.py`：只读入口和验证缓存使用 Run 完整性校验；现有已提交帧边界、帧哈希、Run/Step 身份校验继续生效。
- `ga_studio/catalog.py`：导航索引只检验包身份与完整性，不将可查看内容混同于可执行实验。
- `ga_studio/experiment_resources.py`：GET 使用实验完整性校验；写入、显式预检、Skill 试运行仍使用当前执行合同。显示包内 Skill 正文前校验文件路径，仅读取 Markdown 展示内容。普通地图 GET 不再声称已经完成执行预检。
- `tests/foundation/test_replay_validation_cache.py`：验证缓存绑定新的只读完整性入口。
- `tests/foundation/test_replay_execution_isolation.py`：补充原字段回归、目录/归档只读不变、Web Skill/地图/回放入口、Runtime 严格拒绝、安全入口和篡改拒绝。

Runtime 新建、启动、续跑与重跑仍使用 `validate_experiment_directory` / `validate_run_directory` 的当前执行语义校验。未修改数据库、作者 Skill、原实验包、原 Run、原检查点或原帧；未添加 `passive_roots` 兼容映射。工程子 Agent 未重启服务。

## 自动化验证

最终命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/foundation/test_replay_execution_isolation.py tests/foundation/test_replay_validation_cache.py tests/foundation/test_experiment_resource_editors.py tests/test_portable_package_protocol.py tests/test_object_skill_package.py tests/architecture/test_portable_module_boundaries.py tests/runtime/test_replay_world_state.py -q
```

结果：**49 passed in 35.94s**。测试覆盖目录与 `.garun`、内嵌资源/Skill 字节缺失和篡改、Run 内嵌实验 ID/根哈希、帧身份/帧哈希/连续性、安全且存在的入口、归档外层哈希；同时证明严格执行校验仍拒绝非当前执行合同。

## 原包证据

工程读取了现场 16 个 Run 的全部已提交帧和世界声明素材；全部可读。此前 15 个 Run 仍被执行校验明确拒绝 `passive_roots`，当前交通 Run 仍满足当前合同。

逐文件证据：`tmp/replay-skill-loading-20260913/package-evidence.json`，文件 SHA256：`224cf15dc2a3e8ca8502c3480a10af23253fdab6c9eceeab96de9922dec25354`。静止 Run 对全部文件作读前/读后哈希比较；执行中的 Run 只比较不可变 `run.json` 和完整内嵌实验，避免把继续运行的正常变化误判为读取修改。所有比较均一致。

| 案例 | Experiment | 原 Run | 已读帧数 | 原 `.gaexp` 读前/读后 SHA256 |
| --- | --- | --- | --- | --- |
| 案例 1 | `b1c550ec-1599-4cb8-a4c8-7f303c7344df` | `4f5713c4-7b76-4afe-8f3a-b4bd36ee2a28` | 32 | `d86141b19c1876218d06b51addd32286710879a51edab78b7e7486fda7e3ceff` |
| 案例 2 | `c63b2d81-433a-4c2f-9d75-b44bcf04e318` | `b4b4ca09-87bd-4980-bdc5-8146474d6225` | 8 | `024202c280f14090e2b6ef48a6936d2de2be10127142c0a19617d96a9f8398b4` |

两个源归档的完整性均通过，Skill 正文均可读取，归档字节未变。细节：`tmp/replay-skill-loading-20260913/experiment-evidence.json`，文件 SHA256：`4654ed374f06a1613c83db026432a023c8d9d67a1df7a29d3e25d769b5997c60`。

## 重启边界核查

仓库 `restart-web.bat` 只停止 8000 监听 PID，并要求该 PID 命令包含 `generative_agents.web.main`；不停止进程树。Runtime 由 `FileRunSupervisor` 通过独立 `Popen` 启动 `generative_agents.cli.main run resume`。Web lifespan 收尾仅关闭其数据库连接，不主动停止 Runtime。主 Agent 应按约定重启后核对新 Web PID、健康响应、原 Runtime PID及原浏览器路径。
