# 案例 2 Brain 资料

**状态：正式 Brain 已导入并完成正常流程 8/8 步运行；边界对照仍在进行。** 当前录入依据为 [SKILL.md 正文](SKILL.md)，主 Agent 已将同一正文填入 UI。旧 [SOP 草案](reading-sop-draft.md)仅作为未执行的历史设计保留，不再作为当前操作或合同依据。运行事实见 [正式运行记录](../verification/formal-run-record.md)。

| 项目 | UI 已保存或确认的值 |
| --- | --- |
| Brain ID | `bcfada37-6dd6-4152-b172-e4e3d96d1e1d` |
| Skill key | `book-case02-doorway-reading-brain` |
| UI 内容哈希 | `743a968cf2b2`，页面显示值，不冒充完整文件 SHA256 |
| 正文 | 本目录 `SKILL.md` 与包内文本一致；本地 LF、包内 CRLF，字节及文件 SHA256 不同，精确运行副本以原始结果 ZIP 为准 |
| 包内正文 SHA256 | `d1fae6b4c4fdcf8cbfd98b6b7c5035c40762b7cf1cf1a08d7443b9db5134f90f`，与注册表、完整性清单和 8 帧记录一致 |
| MCP 白名单 | `world-perceive`、`world-navigate`、`world-act`、`memory-stream-search`、`memory-stream-append`，共 5 项 |
| 脚本 / 子 Skill | 0 / 0 |
| Game Object 被动 Skill | 本例地图未配置 |

正式实验 `c63b2d81-433a-4c2f-9d75-b44bcf04e318` 已正确导入本 Brain，并保存 8 步×1 分钟。B02-006 修复后初始 (7,7)/门廊保存、预检和封存通过，正式 Run `b4b4ca09-87bd-4980-bdc5-8146474d6225` 已 `COMPLETED 8/8`。导出已核对嵌入实验的 Skill 闭包、正文与完整性；本地正文与运行副本仅换行符不同，未修改或统一换行，详见 [导出核验](../verification/exports/README.md)。

当前 Brain 的 SOP 要求每条模型响应只发一个工具调用并等待实际结果，避免在感知返回前猜地址。它从真实 `game_objects` 获取阅读桌完整四层地址，用同一 `target_address` 查询与提交 MOVE，**不填写 `target_coord`**。导航使用 `reachable`、`movement_required`、`distance_tiles` 和 `next_coord` 判断；不假设它返回完整路径或最终坐标。实际地址已在阅读桌对象内，且同对象导航返回 `movement_required=false` 时，才允许取书、浏览、阅读与摘记。

私人“阅读进度便签”在世界动作前写入，下一轮以本人已提交 `current_action` 和真实位置确认。成功世界动作结束本轮，不能假定还能补写记忆。精确规则以 `SKILL.md` 为准；本例没有按 Step 编号排程。

正式结果为 5 次 ACT、3 次 MOVE，移动消费 4+4+2 格。Step 5 同对象导航实际返回 `reachable=true`、`distance_tiles=0`、`next_coord=[16,8]`、`movement_required=false`，随后才取书；该证据来自下载轨迹，未仅依据便签。33 次逻辑调用、33 次物理尝试无重试；36 次 MCP 调用无拒绝。唯一质量项为 Step 1 首次空记忆检索，后续检索已返回便签。模型调用次数与 MCP 次数分别统计，不能用“0 次重试”替代工具成功证据。
