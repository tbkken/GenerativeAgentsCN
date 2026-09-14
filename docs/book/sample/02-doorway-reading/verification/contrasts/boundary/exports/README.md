# 边界对照：下载副本与核验记录

核验日期：2026-09-09。仅核验本目录两份浏览器下载副本，并与案例 2 正式运行的下载 ZIP、对应本地诊断 Brain 做只读比较；没有读取或改写原 Run 工作目录、数据库、系统配置，没有调用系统接口或运行 Runtime。原 ZIP 和 JSON 保持不变。行为解释与覆盖范围见 [边界对照结果](../README.md)。

## 原始产物

| 文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| [结果审计 ZIP](result-bundle-step-000001-d1bda0b3.zip) | 2,338,970 | `4bfd8bb64f48928922f871e113f7d0ecc8fbae684ff17c2215c4cac740da1883` |
| [质量报告](quality-report-step-000001-8b1382652399b0c3-19199cb7.json) | 1,176 | `8b1382652399b0c32d636a6f9a874fe9696b15791d42dde1433171707f401707` |

ZIP 共 45 个成员，未压缩总量 2,971,570 字节，无重复成员，CRC 校验通过。内含质量报告与独立 JSON 逐字节一致。该报告的包内来源记录为 Run `7dc92866-7807-4a55-89bc-51fccbaf91c8`、`COMPLETED`、Step `1/1`、`partial=false`；文件大小和哈希相符，生成时间为 `2026-09-09T12:57:17.713530Z`（北京时间 20:57:17）。

## 身份与完整性

- 实验：`9affc077-dd55-4648-87a3-8920e1cb0c9d`。
- Run：`7dc92866-7807-4a55-89bc-51fccbaf91c8`。
- Attempt：`e03251f1-50d3-46a4-be59-fc6f53bcd20b`，状态 `COMPLETED`，`resumed_from_step=0`，无失败。
- Attempt 起止：`2026-09-09T12:55:26.371654Z` 至 `2026-09-09T12:55:48.656652Z`，耗时 **22.284998 秒**。

内嵌实验的 SHA-256 清单覆盖 **13 个文件**，文件集合无缺漏，逐文件哈希匹配。清单根哈希 `3f4ede825b0109f689af527966546b5ec294090b580be0901b0745ccd9f19d9e` 与 Run Manifest 引用一致。包含人物、地图、空间语义索引、Brain、模型与运行参数及三张图片；背景 1536 × 1024、头像 32 × 32、行走图 96 × 128。

与正式 8 步下载包比较，三张 PNG、`world/world.json`、`world/semantic-index.json`、`models/models.json` 和结果配置均逐字节相同。改变的实验文件是 `manifest.json`、`agents/index.json`、`runtime/simulation.json`、`runtime/engine.json`、`skills/registry.json` 和 `skills/items/s0001/SKILL.md`；差异对应独立实验身份与任务、注意力 6→1、Agent `currently`、诊断 Brain 和 8→1 步。人物长期目标、起点 `[7,7]`、视野 10、头像/行走图引用及显示比例不变。

Step 1 的 `checkpoints/` 与 `recovery/` 两套快照，其声明成员的大小和 SHA-256 全部匹配，快照帧与唯一已提交帧逐字节一致。包内没有 Attempt 可变工作存储。

## 精确 Brain

包内 Brain 为 `book-case02-perception-boundary-check`，无子 Skill 或对象被动 Skill。原文件在 `experiment/skills/items/s0001/SKILL.md`，1,661 字节，SHA-256：

```text
99790b7752ab5fbc13b6c7cf4707bf5ef5cba8f55db7090daafcef572a229ad8
```

该哈希与 Skill Registry、实验完整性清单和 Step 1 Skill 执行记录一致。与 [本地诊断 Brain](../../../../skill/contrasts/perception-boundary-check.md) 正文一致，仅换行格式不同：本地 1,647 字节、14 个 LF，SHA-256 为 `d7fcc77421ea2bf4f185bf2192d9ca738e81a771c643c8ccfa5c5fb15cce3e0b`；包内为 14 个 CRLF。统一换行后逐字节相同，本轮未覆盖文件。

## 事实、调用与报告

唯一帧为 Step 1，虚拟时间 `2026-09-09T09:00:00+08:00`，Run/Attempt 身份相符。唯一世界事件含完整 SPO“林晨 / 检查空间边界 / 视野、注意力与地点简称”及非空 `structured_payload`，动作类型为 ACT。前后坐标均 `[7,7]`，地址为门廊，执行路径为空；无 MOVE、记忆增量、记忆工具调用或对话。

| 实际 MCP 调用 | 输入与结果 |
| --- | --- |
| `world-perceive`，1 次成功 | 输入 `{"radius_tiles":100}`；返回请求 100、生效 10、视野上限 10。attention=1，候选空间/Agent/对象/Event 为 1/0/2/3，实际附近输出为 1/0/1/1，`truncated=true`；三个 CURRENT 语义锚点保留。 |
| `world-navigate`，1 次预期拒绝 | 输入 `{"target_address":["阅读角","",""]}`；返回 `navigation requires an exact arena/object address`。精确单元素输入未覆盖。 |
| `world-act`，1 次成功 | 原地 ACT“检查空间边界”，不移动。摘要把地址写成 `["阅读角"]`，其文本不能替代上述真实输入证据。 |

模型 Trace 包含 3 次逻辑结束、3 次物理开始、3 次物理尝试；全部模型请求成功，物理 `attempt_no` 均为 1，即 **3 逻辑 / 3 物理 / 0 物理重试**。MCP 为 3 次，其中 1 次刻意触发的地址边界拒绝，不等于模型请求失败。

唯一质量项 `MCP_TOOL_ERROR` 定位该次导航调用，报告保存的输入确实是三元素残缺地址。没有把预期拒绝记成新的系统缺陷，也没有把 Run 完成描述成零观察项。

## 交付边界

这是普通结果审计 ZIP。内嵌实验资源与已提交事实可在下载副本中核对，但 ZIP 根目录没有 Run 的 `integrity/sha256.json`，本目录没有正式 `.gaexp` / `.garun` 产物；本次没有完成异机导入、正式封存或续跑验证，不承诺一键恢复。
