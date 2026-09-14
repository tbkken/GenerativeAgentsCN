# 案例 2 对照：视野、注意力与残缺地址

更新：2026-09-09。本页记录浏览器完成的独立 1 步对照，以及从下载副本核验的真实结果。完整产物、哈希与检查范围见 [导出核验记录](exports/README.md)。这次对照留在门廊检查边界，没有执行正式阅读路线。

## 实际运行

- 实验：`9affc077-dd55-4648-87a3-8920e1cb0c9d`，“案例2对照：视野上限、注意力与简称地址”。
- Run：`7dc92866-7807-4a55-89bc-51fccbaf91c8`；Attempt：`e03251f1-50d3-46a4-be59-fc6f53bcd20b`。
- `COMPLETED`，1/1 步；Attempt 耗时 22.284998 秒，对应页面 22 秒；3 次逻辑模型调用、3 次物理尝试、0 次物理重试。
- 林晨的前后坐标均为 `[7,7]`，位置仍在门廊；只提交一次 ACT“检查空间边界”，没有 MOVE、记忆读写或对话。

与 [正式 8 步运行](../../exports/README.md) 比较，包内地图、语义索引、三张图片、模型设置、人物起点和视野保持一致。改变的是：

| 配置 | 正式运行 | 本次对照 |
| --- | --- | --- |
| 注意力带宽 | 6 | 1 |
| Brain | `book-case02-doorway-reading-brain` | `book-case02-perception-boundary-check` |
| 观察窗口 | 8 步 × 1 分钟 | 1 步 × 1 分钟 |
| 本次任务 | 准备、移动、阅读、摘记 | 原地检查感知边界和残缺地址 |
| 实际调整字段 | — | 实验名称与 `experiment.goal`、Agent 的 `currently`；人物长期 `goals` 和生活计划未改 |

其余关键参数仍为：`Asia/Shanghai`、虚拟开始 `2026-09-09T09:00:00+08:00`、起点 `[7,7]`、视野 10、人物显示 2.5 格、随机种子 42、每步检查点、保留 2、Payload 记录开启。模型仍为正式案例使用的 `Qwen3.8-27B-UD-Q4_K_XL` 与相同本地服务配置。

## 观察 1：请求 100 格，实际仍受 10 格视野限制

这次真实 `world-perceive` 输入为：

```json
{"radius_tiles":100}
```

工具成功返回 `requested_radius_tiles=100`、`radius_tiles=10`、`vision_radius_tiles=10`。因此，本次调用确实使用了 `radius_tiles` 入参，返回的生效范围被限制为角色配置的 10 格，没有修改角色视野来满足请求。这是该字段在本次运行中被接受的证据；下载的模型 Payload 仍没有完整工具参数 schema，不能据此补写其他未观察的参数规则。

## 观察 2：注意力裁剪候选，当前位置锚点保留

比较两个 Run 的 Step 1：

| 项目 | 正式运行，attention=6 | 边界对照，attention=1 |
| --- | --- | --- |
| 候选数量 | 附近空间 1、附近 Agent 0、Game Object 2、Event 3 | 同左 |
| 实际输出 | 附近空间 1、附近 Agent 0、Game Object 2、Event 3 | 附近空间 1、附近 Agent 0、Game Object 1、Event 1 |
| `truncated` | `false` | `true` |
| CURRENT 语义锚点 | World、Sector、Arena 共 3 个 | 相同 3 个，完整保留 |

对照中保留的 Game Object 是“置物边柜”，“阅读桌”没有进入这一轮实际对象候选输出。World“林晨的门廊与阅读室”、Sector“馆内阅读空间”、Arena“门廊”均保留，角色不会因注意力缩小而失去当前位置语义。林晨当时不位于 Game Object 内，所以 CURRENT 锚点是三层，不应凭空补出第四层对象。

这份证据分别列出各类候选与实际输出，不能把 attention=1 描述成整个返回对象里只能出现一个元素。

## 观察 3：残缺地址被拒绝，但精确单元素输入未覆盖

[诊断 Brain](../../../skill/contrasts/perception-boundary-check.md) 指定的输入是 `target_address=["阅读角"]`。实际 MCP 记录却是：

```json
{"target_address":["阅读角","",""]}
```

工具返回：

```json
{"isError":true,"error":"MCP error: navigation requires an exact arena/object address"}
```

可确认的是：**缺少完整层级且含空项的地址被拒绝**，没有靠简称进入阅读角，也没有产生 MOVE。**不能宣称这次已精确测试单元素 `["阅读角"]` 的输入**，因为真实请求与原计划不同。

最后的 ACT 摘要将请求写成了 `["阅读角"]`，省略了实际调用中的两个空项。这段自然语言摘要不够准确，应以 `mcp.call.input_text`、工具返回和质量报告证据为准，不能用摘要覆盖真实参数，也不能为对齐计划而改写归档记录。

## 质量与结论边界

质量报告仅有 1 项 `MCP_TOOL_ERROR`，准确定位 Step 1 的 `world-navigate` 残缺地址拒绝。这是本次边界对照刻意触发的预期拒绝，不是新发现的系统缺陷；模型请求全部成功，与 0 次物理重试不矛盾。执行状态 `COMPLETED` 与质量状态 `WARNING` 分别表达运行结束和留下观察记录。

本次覆盖了超范围感知请求的返回边界、注意力候选裁剪、当前位置锚点保留，以及残缺地址拒绝。精确单元素地址、成功完整地址导航、实际移动和阅读活动不由这个 1 步对照验证；后两者的证据在正式 8 步 Run 中。

交付仍是普通结果审计 ZIP，没有 Run 根完整性清单，也未进行正式 `.garun` 封存或异机恢复验证。
