# 案例 2 参数与实测结果

**状态：B02-006 已闭环，正式正常流程 `COMPLETED 8/8`；边界对照仍在进行。** 实验 ID `c63b2d81-433a-4c2f-9d75-b44bcf04e318`，名称“案例2：穿过门口，到阅读角看书”。初始位置 (7,7)/门廊已保存并重开核对，预检和封存通过。参数与结果以 [正式运行记录](../verification/formal-run-record.md)、[导出核验](../verification/exports/README.md)为依据；历史操作见 [界面构建记录](browser-building-record.md)及 [问题记录](../verification/blockers.md)。

| 项目 | 正式配置或实测结果 |
| --- | --- |
| 角色数 | 1，林晨 |
| 总步数 | 8 步，已完成 8/8 |
| 步长 | UI 已保存 1 分钟 |
| 虚拟开始时间、时区 | UI 已保存 2026-09-09 09:00、Asia/Shanghai（2026-09-09T09:00:00+08:00） |
| 地图名称 | 已在 UI 创建“林晨的门廊与阅读室” |
| 地图 ID / key | `fe34ac77-2178-48fa-ae90-2dab98786d4e` / `book-case02-doorway-reading` |
| 已保存地图网格与背景 | 24×16、32px tile；原图 1536×1024，以源图 48×32 格完整切片“门廊与阅读室完整背景”绑定 World 并保存 |
| 地图构建进度 | 六节点、背景和碰撞已保存；静态检查及正式 8 帧路径、碰撞、画面核验通过 |
| 当前已保存初始位置 | **(7,7)**，真实地址：林晨的门廊与阅读室 → 馆内阅读空间 → 门廊；重开编辑确认一致 |
| 已知空间 | 仅同一门廊，物件列表为空；没有虚构 Game Object 或预置阅读桌 |
| 历史初始位置 | 创建时为 (3,4)/置物边柜；B02-006 修复前同步 (7,7) 保存失败，修复后原路径保存成功，原值已替换 |
| 阅读站位、门洞 | 作者静态查询终点为 (16,7)；正式对象地址导航选中 (16,8)，均在阅读桌 GO 的可走 x16 列；唯一门洞 x9–10/y8 |
| 阅读桌完整地址 | Step 1 实际感知返回 林晨的门廊与阅读室 → 馆内阅读空间 → 阅读角 → 阅读桌；key `game_object-mttvfdsu-3ijqb3`，距离 9 格 |
| Agent key、年龄 | 已保存独立林晨 `agent-mttvoove-oxit70`，21 岁 |
| Crowd 名称 | 已保存“案例2：林晨穿过门口去阅读”，仅包含本例林晨 |
| Crowd ID / key | `c8751983-07e5-4a41-bf2c-1c7b52eff796` / `crowd-2-b8aa4c8580d6` |
| 图片与显示比例 | 头像、行走图已复制并在导出中核验；显示 2.5 格，本轮门洞与桌旁画面已核对；未配置独立坐姿/阅读动画 |
| vision_radius、attention_bandwidth | 10、6；正常流程 Step 1 实际半径 10、未截断；缩小视野/注意力的边界对照单独验收 |
| 移动速度（格/分钟） | 只读代码合同已核查：当前唯一 `ga-cn-v1` profile 固定为 4；UI 无独立速度字段 |
| 单步移动预算（格） | stride=1，每步预算 `1×4=4` 格；Step 2–4 实际消费 4、4、2 格 |
| 起点至有效终点的路径长度（格） | 静态 (7,7) → (16,7) 为 11 格；正式对象地址导航到 (16,8) 为 10 格，两者终点不同 |
| Brain ID / key | 已保存 `bcfada37-6dd6-4152-b172-e4e3d96d1e1d` / `book-case02-doorway-reading-brain` |
| Brain 正文与 UI 哈希 | `skill/SKILL.md` 与包内正文文本一致，UI 哈希 `743a968cf2b2`；本地 LF、包内 CRLF 导致文件 SHA256 不同，精确运行字节见原始 ZIP |
| MCP 白名单 | UI 已确认 world-perceive、world-navigate、world-act、memory-stream-search、memory-stream-append，共 5 项；0 脚本、0 子 Skill |
| 聊天模型与服务 | `vllm`，`Qwen3.8-27B-UD-Q4_K_XL`，`http://127.0.0.1:8888/v1`；temperature=0.2、max_tokens=2048、timeout=90 秒、retry_attempts=1、enable_thinking=false；密钥不写入教材 |
| 嵌入模型与服务 | `openai_compatible`，model=`auto`，`http://127.0.0.1:5002/v1`；timeout=120 秒，transport 与 index retry max=3 |
| 随机种子 | UI 已保存 42 |
| 检查点与投影 | UI 已保存 checkpoint_interval=1、checkpoint_retention=2、agent_step_projection_interval=1 |
| 模型 Payload | UI 已开启 |
| Experiment ID / 名称 | `c63b2d81-433a-4c2f-9d75-b44bcf04e318` / 案例2：穿过门口，到阅读角看书 |
| 导入资源 | UI 已确认正确导入本例 map、crowd、Brain，1 Agent |
| 预检 | 0 阻断、0 警告、0 自动检查、1 通过 |
| Run / Attempt ID | `b4b4ca09-87bd-4980-bdc5-8146474d6225` / `881415da-972b-4831-8df5-a2ede8d62bcb`；`COMPLETED 8/8`，1 个 Attempt，本轮未暂停恢复 |
| 耗时与模型调用 | UI 5 分 48 秒；导出 Attempt 持续 348.274705 秒；33 次逻辑调用、33 次物理尝试、0 次重试 |
| MCP 调用 | 36 次：perceive 8、search 8、append 8、navigate 4、act 8；0 次错误/拒绝 |
| 质量结果 | 唯一 WARNING：Step 1 `EMPTY_MEMORY_RETRIEVAL`；后续已能读回进度便签 |
| 导出核验 | 原始结果 ZIP 和独立质量 JSON 已归档；8 帧、嵌入实验 13 文件完整性及协议校验通过，详情见导出核验清单；未验收正式 `.garun` 或异机恢复 |

当前速度来源和 UI 入口已核查，见 [移动预算合同](movement-budget.md)。静态查询 (7,7) → (16,7) 的 11 格路线原预计需要 3 轮 MOVE；正式对象地址导航选择 (16,8)，实际 10 格也分 3 轮完成，落点依次为 (10,8)、(14,8)、(16,8)。不能要求不同终点返回相同路径长度。

本次实际为整理书包 1 个 ACT、移动 3 轮、到达后的取书、浏览、阅读和笔记各 1 个 ACT。8 步结果来自自然语言 SOP、路径预算与完成证据，没有按 Step 编号强排；模型耗时和描述不作为后续复现的逐字保证。
