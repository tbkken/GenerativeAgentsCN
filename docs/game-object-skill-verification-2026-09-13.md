# Game Object Skill 实现验证记录

日期：2026-09-13。设计依据：用户明确确认绑定一个 Skill 即具备自主执行、周围感知和交互响应，使用自然语言与大模型驱动，并授权实现。运行合同见 [Game Object Skill 运行合同](game-object-skill-runtime.md)。

## 已实现的行为

| 范围 | 实际行为 |
| --- | --- |
| 地图编辑 | 选择一个对象 Skill 即启用；无需主动/被动开关或触发器。感知、注意力和交互参数默认折叠。 |
| 对象运行 | 每 Step 执行一次根 Skill，即使没有 Agent 或没有收到询问；根 Skill 通过模型调用公共能力，可组合子 Skill。 |
| 身份与感知 | 使用对象自身身份及私有记忆；复用 Agent 空间感知，限制视野和注意力；只提供实际执行轨迹的可见片段与事实 ID。 |
| 动作提交 | 固定对象可记录 ACT、WAIT 或修改自身状态；每轮最多提交一次，模型正文不直接修改世界。 |
| 交互响应 | Agent 提交请求，对象在自己轮次处理；同一动作可切换状态并回复多个请求。回复在下一轮只进入请求者上下文，并只消费一次。 |
| 恢复与回放 | 对象状态、请求、活动键和进度进入检查点；同一活动键跨 Attempt 不能重复提交；回放读取已提交状态事实。 |
| 可观测性 | 对象执行有独立身份的 Trace 和质量条目，估算计入对象数量，不新增公共 Agent。 |
| 文件隔离 | 根 Skill、子 Skill 与资源随实验物理复制进 Run；作者修改不改变已有运行副本。 |

## 自动化结果

本次最终目标回归 **104 项通过**，新增根 Skill 附带脚本的边界回归 **1 项通过**，共 **105 项 Python 检查通过**。前端 **5 项通过**。本记录不声称整个仓库测试套件全绿。

104 项目标回归命令（仓库根目录，Python 3.13.9）：

```powershell
.venv/Scripts/python.exe -m pytest tests/runtime/test_object_skill_runtime.py tests/test_object_skill_package.py tests/test_quality_projection.py tests/runtime/test_brain_capability_runtime.py tests/runtime/test_world_commit_event_sync.py tests/test_portable_package_protocol.py tests/foundation/test_state_appearance.py tests/foundation/test_experiment_resource_editors.py tests/foundation/test_skill_mcp_dependencies.py tests/architecture/test_portable_module_boundaries.py tests/foundation/test_game_object_skills.py::test_interaction_selection_prepares_request_without_running_object_in_agent_identity tests/foundation/test_map_editor_v2.py::test_game_object_binding_derives_mode_without_a_separate_switch -q --disable-warnings --maxfail=2
```

结果：`104 passed in 26.47s`。随后补充验证附带 `scripts/main.py` 或其他脚本资源不会绕过对象根 Skill 的模型执行：

```powershell
.venv/Scripts/python.exe -m pytest tests/runtime/test_object_skill_runtime.py::test_object_root_uses_model_even_when_its_bundle_contains_scripts -q --disable-warnings
```

结果：`1 passed in 6.08s`。这项测试已加入上面的测试文件，再运行完整命令时会一并收集。

```powershell
& 'C:/Program Files/nodejs/node.exe' --test tests/frontend/object-skill.test.cjs tests/frontend/state-appearance.test.cjs
```

结果：5 项通过。覆盖对象 Skill 选择与保存默认值，以及状态图精确匹配、向前/向后跳转恢复外观和编辑器显示尺寸。相关 Python 编译检查、前端语法检查与本次文件的差异空白检查通过。

### 文件包测试的具体证据

`tests/test_object_skill_package.py` 在临时目录中生成真正的 `.gaexp`，由 Runtime 建立 Run，运行无 Agent、一个 Skill 对象的三步场景：

1. 第一步提交 GREEN 后暂停；检查点记录对象状态和活动键。
2. 修改公共作者源文件，然后恢复同一 Run；新 Attempt 读取 Run 内嵌 Skill 和上次已提交进度，第二步提交 RED。
3. 第三步提交 GREEN；核对模型仅执行步骤 1、2、3，无重复提交。
4. 从 Run 目录及导出的 `.garun` 读取回放，第二步均为 RED；质量报告记录 3 个对象轮次、0 个 Agent 轮次。

其他对象测试覆盖：骑手起终点都在视野外但实际路径穿过摄像头范围；未执行计划路径不被看到；注意力裁剪后不能引用未返回证据；Agent 无法直接修改绑定对象状态；回复对象不能伪造请求者；模型超时回滚未提交私有记忆并保留请求；自然语言子 Skill 共享对象身份且无最终动作权限。

## 扩展旧测试的未通过项

扩大检查时发现以下已有测试与当前仓库接口或数据库状态不一致，本次没有扩展修复这些问题，也未将其统计为通过：

| 测试 | 观察到的未通过原因 |
| --- | --- |
| `tests/foundation/test_game_object_skills.py::test_game_object_can_bind_a_text_only_skill` | 旧断言读取已不存在的 `PassiveSkillResult.revision`。当前结果使用内容哈希。 |
| `tests/foundation/test_game_object_skills.py::test_demo_resources_materialize_through_public_apis` | 旧 Web app 启动路径访问缺失的 `tool_definitions` 表。 |
| `tests/foundation/test_map_editor_v2.py::test_map_editor_document_and_real_tiles_are_served` | 同一旧 Web app 启动路径访问缺失的 `tool_definitions` 表。 |
| `tests/foundation/test_map_editor_v2.py::test_custom_blank_map_does_not_inherit_ville_materials_or_nodes` | 旧测试匹配已变化的上传 `fetch` 调用文本。 |

以上属于工程测试观察，不是本次交通治理实验的浏览器运行证据。

## 当前边界

- 模型使用可控测试替身，但经过真实 SkillRuntime 工具循环、世界提交、文件封存和恢复链路。尚未验证真实模型的交通规则理解、抓拍准确率、连续违规分段、延迟和成本。
- 当前对象阶段在 Agent 阶段之后；本步切换的状态和交互回复供下一轮 Agent 使用。仍是整数分钟步长，本次未增加秒级交通动力学。
- 文件注册表已改为 `object_roots`，对象根条目为 `kind: object`；旧 `passive_roots` 包需要按新模型重新导入/生成独立实验包，不能直接沿用。已有封存文件和历史 Run 未被改写。
- 交通治理实验仍停留在文件准备阶段。本次未配置正式地图、人物或摄像头 Skill，未调用真实模型启动该实验，也未重启服务。
