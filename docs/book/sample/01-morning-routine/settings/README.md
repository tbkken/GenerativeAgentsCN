# 运行设置

- `simulation-settings.json`：从 `07:00:00+08:00` 开始，32 步、每步 3 分钟，随机种子 42，检查点每步一次、保留最近 2 个；这是当前案例实验的运行参数。
- `models.json`：本次参考 Run 的非凭据模型与连接参数，已从嵌入实验核对；`credential_env` 保留教学环境变量名示例。使用其他环境时选择自己的可用服务，密钥只通过环境变量注入。
- `results.json`：每步保留投影，开启模型 Payload 记录，与本次参考 Run 的诊断配置一致。关闭记录属于另一次运行的参数选择，不能将关闭后的配置标为本次实测原样配置。
- `engine.json`：固定 Brain Skill key `book-case01-morning-routine`。
- `browser-authoring-checklist.md`：学员按浏览器创建地图、Agent、五 MCP Brain 和实验的总清单，不要求复制实例 ID。
- `verification-record.md`：保存当前与历史实例 ID、运行状态和已验证事实。
- `authoring-selection.json`：本地说明文件的路径索引，不是系统导入接口；不能用历史 JSON 代替浏览器清单。

本案例不配置子 Skill、被动 Skill 或 Evaluator；实验使用案例自己的单 Agent 人群。浏览器中的实验资源、地图切片和对象状态以 Studio 当前保存结果为准，本目录的旧 JSON 不是系统导出包。

当前录入参数还包括：Agent 视野半径 10、注意力带宽 6、地图显示大小 3 格、实验出生点 `[9,5]`。聊天模型为 `Qwen3.8-27B-UD-Q4_K_XL`，本机聊天端口为 8888，Embedding 端口为 5002。Embedding 的配置名为 `auto`、`resolved_model=null`，并未验证其实际解析出的模型名；模型上下文窗口也未提供明确值。不要把旧示例中的 Qwen3-8B、8000/v1、nomic-embed-text 或 11434/v1 当成本次实测配置，也不要把本机服务视为系统内置服务。对应 UI 和归档来源见 [最终验证记录](verification-record.md)。

`map/map-definition.json`、`map/semantic-addresses.json`、`map/collision-and-placements.json`、`agents/陈明远/studio-definition.json`、`agents/陈明远/experiment-placement.json`、`skill/brain/brain-definition.json`、`verification/preconditions.json` 和 `case-manifest.json` 保留为早期作者参考，其中仍可能出现旧地图尺寸、旧运行窗口或禁止记忆等过期规则。它们不是当前可导入资产，不能覆盖当前浏览器地图、Agent、Brain 或实验；复现实验以中文浏览器清单和当前运行参数说明为准。
