# GenerativeAgentsCN 中文代码导览

规则见 [AGENTS.md](../AGENTS.md)，文件合同见[系统架构](capability-composition-platform-design.md)，目录依赖见[源码组织](source-organization-design.md)。所有产品源码在 `src/generative_agents/`；四模块之间通过自包含文件交换业务数据。

## 从任务找到代码

| 任务 | 当前入口 |
| --- | --- |
| `ga` 命令 | [adapters/cli/main.py](../src/generative_agents/adapters/cli/main.py) |
| Studio 服务 | [adapters/web/app.py](../src/generative_agents/adapters/web/app.py)，`ga studio serve` |
| 公共作者资源 | [ga_studio/api.py](../src/generative_agents/ga_studio/api.py) → resources/ |
| 实验导入、编辑、封存 | [ga_studio/experiments/workspace.py](../src/generative_agents/ga_studio/experiments/workspace.py)、builder.py、editor.py |
| Run 新跑、恢复、控制 | [ga_runtime/api.py](../src/generative_agents/ga_runtime/api.py) → lifecycle/ |
| Step 推进与世界提交 | [ga_runtime/engine/scheduler.py](../src/generative_agents/ga_runtime/engine/scheduler.py)、world.py、storage/commit.py |
| Brain 和对象 Skill | ga_runtime/skills/brain.py、objects.py；共享执行器 executor.py |
| MCP 身份和权限 | [ga_runtime/capabilities/server.py](../src/generative_agents/ga_runtime/capabilities/server.py) |
| 文件记忆 | [ga_runtime/memory/stream.py](../src/generative_agents/ga_runtime/memory/stream.py) |
| StepResult 类型 | [ga_protocol/schemas/facts.py](../src/generative_agents/ga_protocol/schemas/facts.py) |
| Replay | [ga_replay/api.py](../src/generative_agents/ga_replay/api.py) → reader.py、projections/、cache.py |

## 一次创建与执行

1. Studio 读取用户明确选择的公共地图、Agent、Brain、Skill 和模型配置。工作区服务展开依赖闭包，物理复制资源并写完整性清单。
2. 实验编辑只修改草稿内副本；封存校验产生 `.gaexp`。公共资源后续编辑或删除不改变这个包。
3. Runtime 创建新 Run，将实验完整嵌入。执行器获得单写者锁，读取最近完整检查点并创建 Attempt。
4. lifecycle/assembly.py 装配 ActorState、世界、模型、Brain、对象执行器、文件记忆和提交器。ActorState 只保存参与者状态；没有固定排程、反思或聊天业务流水线。
5. Scheduler 每步处理 Agent 动作，再处理对象 Skill，并提交完整 StepResult、帧及恢复数据。Skill 文本进入过程审计；世界事实必须经过 MCP 与提交器。
6. Replay 只读已提交边界，归约状态、对象、对话和质量。其缓存可丢弃，不能替代源 Run。Web 按当前 Experiment/Run/Attempt 展示结果。

公共 Skill 试运行是可选作者功能：Studio 在临时目录准备物理闭包与模型配置，适配层交给 Runtime。密钥只在执行环境中注入，调用仿真 MCP 的 Skill 必须进入实验。

## 数据与资源归属

- ga_protocol/schemas 保存包合同；Studio 的地图编辑状态、公共素材 ID、数据库 Skill 身份分别在 resources/map_document.py、skill_document.py。导入时显式转换，不带入运行期数据库定位条件。
- ga_studio/storage 包含 ORM、数据库会话、当前迁移基线、上传素材和本机凭据。其他模块不能导入数据库实现。
- ga_studio/bundled 保存显式导入的地图素材及可编辑 Skill 种子；没有默认地图或内置公共 Agent。Runtime 和 Replay 只使用已复制到包内的资源。
- adapters/web/static/shell 是页面和请求协调；resources 是作者编辑器；replay 是播放器与人物图集；vendor 保存前端依赖。资源 URL 不再暴露旧源码目录。
- var 是用户工作区和运行数据，不是源码；tests 和 tools 分别维护回归与发布工具。

## 验证入口

`tools/check_source_boundaries.py` 扫描全部 Python 文件、延迟导入与种子脚本，并检查传递依赖。架构测试防止旧顶层目录或通配导出回流。协议安全、身份隔离、暂停恢复、对象响应、前端异步隔离和 wheel 验收见[测试指南](test-strategy.md)。

源码重构不改写教材案例证据；浏览器、正式模型、原始导出各自的验收范围仍以对应记录为准。
