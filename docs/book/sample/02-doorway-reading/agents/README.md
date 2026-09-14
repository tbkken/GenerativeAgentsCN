# 案例 2 人物

本例仅使用大学生林晨。头像与行走图已经物理保存到本例 [林晨/assets](林晨/assets/README.md)，运行时不引用其他案例目录。

[林晨的录入记录](林晨/README.md)保存已逐字核对的人设正文，不复制早期校园两日安排、旧地图坐标或旧模型设置。主 Agent 已新建独立公共 Agent 林晨并唯一加入本例人群，头像和行走图均从本例目录通过 UI 上传，实验内两图显示已复制。

| 已保存项目 | 值 |
| --- | --- |
| 公共 Agent | 林晨，key `agent-mttvoove-oxit70`，21 岁 |
| Crowd 名称 | 案例2：林晨穿过门口去阅读 |
| Crowd ID / key | `c8751983-07e5-4a41-bf2c-1c7b52eff796` / `crowd-2-b8aa4c8580d6` |
| 人群成员 | 仅上述林晨，1 人 |
| 图片与显示 | 本例 portrait.png / texture.png 已 UI 上传；显示 2.5 格 |
| 感知 | vision_radius=10，attention_bandwidth=6 |

**状态：公共 Agent 与 Crowd 已导入正式实验，正常流程已完成 8/8 步；边界对照仍在进行。** B02-006 已修复，实验初始 (7,7) 与真实门廊三层地址保存、重开核对通过；已知空间仅门廊，物件列表为空。正式回放已核对显示 2.5 格人物穿门和桌旁位置，导出已确认头像、行走图及 display2.5/vision10/attention6 均在嵌入实验中。见 [正式运行记录](../verification/formal-run-record.md)和 [导出核验](../verification/exports/README.md)。本例没有独立坐姿/阅读动画，不能将 ACT 文字当作这些动画已实现。出生位置保存在实验中，不写入公共 Agent。
