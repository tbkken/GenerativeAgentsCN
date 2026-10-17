# 25 人实验：超时与取消修复后的运行验收

## 结论与边界

2026-10-15，主 Agent 通过系统界面运行原有完整 25 人、120 步实验，未替换小实验或改模型参数。父 Brain → 子 Skill → 接口 → 子 Skill → 父 Brain 的调用与结果传递已实际发生。取消活动模型请求约 283 ms 后记录 ABORTED，随后没有新物理请求。完整 120 步没有完成：本 Run 专门验证活动请求取消，取消时尚在 Step 1，最终提交边界为 0。

这证明本次覆盖到的调用机制可用，不证明 29 个子 Skill、7 个接口全部通过。模型超时总预算和中断机制已修复；本机模型历史 600 秒无响应的服务性能根因仍未知。当前未提交帧，不能将过程文本或工具请求认作世界事实或回放验收通过。

## 身份和重启

| 项目 | 事实 |
| --- | --- |
| Experiment | `b0b85db8-e439-450b-93ad-670d7d6a9caa`，斯坦福小镇·原版25人中文实验，SEALED |
| Run | `16c8418e-657c-4cb0-9690-93d758ed2059` |
| Attempt | `f114f3ed-ccb6-46fd-9878-5783ceb43380` |
| 参数 | 25 Agent，120 步，10 分钟/步，2024-02-13 06:00+08:00，种子 42，保存 Payload |
| 模型 | `Qwen3.8-27B-UD-Q4_K_XL`；向量模型 `qwen3-embedding-0.6b`，原参数保留 |
| 重启前 | 旧故障 Run 已 CANCELLED、2/6；本案草稿、0 Run |
| 重启 | 隐藏窗口执行已授权 `restart-web.bat`；旧监听 PID 45292 停止，新监听 PID 49376，启动器 PID 54980，启动于 15:41:33+08:00 |
| 健康与页面 | 重启脚本健康检查成功，主 Agent 重新加载原浏览器入口并实际启动实验 |
| 运行开始 | 2026-10-15T07:44:15Z 附近（北京时间 15:44:15） |
| 最终状态 | CANCELLED；`committed_step=0`；Attempt failure=null；原因 `cancel requested; uncommitted Step discarded` |

重启日志：[restart-after-cancellation-fix.log](restart-after-cancellation-fix.log)。工程检查：Python 648 passed / 7 skipped、Node 178 passed、源码边界通过、wheel 构建及仓库外安装验收通过。安装验收模型使用临时确定性 HTTP 服务，和下列真实模型证据分开。

## 动态调用证据

证据来自 UI“仿真诊断 → 模型调用”的 Payload，以及 UI 下载 ZIP 内 `traces/model-calls-001.jsonl` 和对应 Payload。Step 均为 1。

| 链路 | 证据 | 判定 |
| --- | --- | --- |
| 父 Brain 调子 Skill | seq2，`stanford-town-full-brain` 发出 `call_skill(name=base-desc)`；seq5 子 Skill 成功返回中文背景 | 已执行 |
| 子结果返回父 Brain | seq14 请求消息含相同 tool_call_id `kUS0gOcWiWmS3aAvSg9PjOkCOGPeQWff` 的 base-desc 结果，并含 wake-up 返回 `7` | 已执行并传回 |
| 初始日程与全天日程 | `schedule-init` 返回主要活动；`schedule-daily` 随后运行；seq38 父 Brain 消息含全天日程子结果 | 已执行并传回 |
| 子 Skill 调记忆检索 | seq29，`schedule-daily` 请求 `memory-stream-search(query="2024-02-13 山本百合子 全天日程 计划", limit=8)`；seq35 请求消息保留真实工具结果 `[]` | 已执行 |
| 子 Skill 调记忆写入 | seq35 请求消息含 `memory-stream-append` 返回，`id=874830bc-2518-5953-aac5-fe8cef943543`、`agent_key=yuriko-yamamoto`、state=ACTIVE、created_step=1 | 当时写入成功；最终未提交 Step 被取消，不能当最终持久事实 |
| 世界动作 | seq41，call_id `7587bbf2-d5b2-4c2d-ae08-ec6fe8c5f73e`；请求 `world-act`，ACT / 整理当日日程并准备起床 / 床，带真实四层 target_address | 已请求；此部分结果包没有终结接口返回或提交帧，不宣称世界动作落地 |
| 第二人 | 沃尔夫冈 `wolfgang-schulz` 已进入同类子 Skill 调用；取消发生在其 `schedule-daily` 活动请求中 | 实际运行到第二人，其他 23 人未被本次覆盖 |

实际触发并返回的子 Skill 覆盖 `base-desc`、`wake-up`、`schedule-init`、`schedule-daily`。确定取得真实接口返回的覆盖为 `memory-stream-search`、`memory-stream-append`。未将静态依赖引用数当动态覆盖率。

原始 ZIP 共 75 条模型记录：25 PHYSICAL_START、25 PHYSICAL_ATTEMPT、25 LOGICAL_END；24 次成功、1 次取消，没有物理重试。成功请求最长 75957 ms。没有发现预算耗尽、brain.rollback、brain.missing_action 或 brain.fallback 的记录；由于本次没有 StepResult，不能以其缺失证明整体质量合格。质量报告为 `NOT_EVALUATED`、`evaluated_agent_steps=0`。

## 活动请求取消

1. UI 明细 seq73 显示请求 RUNNING：call_id `d788718c-d6be-4d71-84e8-00f0fba8c1fe`，`schedule-daily`，第二人；开始于 `2026-10-15T07:53:37.679756Z`。
2. 主 Agent 于 `2026-10-15T07:54:24.725Z` 开始点击“取消仿真”。这是浏览器动作时间，不冒称服务端接收时间。
3. seq74 物理请求于 `2026-10-15T07:54:25.007727Z` 结束，`ABORTED`、`error_code=ModelCallCancelled`；距点击约 282.727 ms。seq75 逻辑终态亦为 ABORTED。
4. UI 于 07:54:32.947Z 读取为 Run / Attempt CANCELLED、0/120。07:56:36.704Z 再查仍 25 条物理请求；ZIP 审计取消点击后新增 PHYSICAL_START 为 0。
5. 当前 Step 丢弃，ZIP 帧与检查点数均为 0；不可据过程记忆、ACT 请求或摘要声称移动/世界提交发生。

## 原始导出与取材范围

通过界面点击“导出结果”，取得 Step 0、已取消、部分结果 ZIP。保留 Downloads 原件并原样复制到本目录，未改写历史 Run 或旧故障日志。

| 文件 | 字节 / SHA256 | 取材与时间 |
| --- | --- | --- |
| [result-bundle-step-000000-a8609771.zip](result-bundle-step-000000-a8609771.zip) | 4203517 / `81b7c8ac6fb14d73574404023d83212d83e4a6ddf8af960b3e5f2f73ef53928f` | UI 标注生成于北京时间 15:54；下载文件时间 `2026-10-15T07:55:15.681166Z`；CANCELLED、Step 0/120 |
| [run-16c8418e-cancelled.log](run-16c8418e-cancelled.log) | 1943 / `30909d5165ec3dd588bf089a508114be29a5d3dcf60f127bde3a9491a0dc2828` | 下载时间 `2026-10-15T07:54:56.382635Z`；UI 原始日志 |

普通结果 ZIP 不等同于本次已完成正式 `.garun` 交换及异机恢复验证。包内没有已提交帧，未提交模型轨迹仅作审计。

## 界面问题 B04-001：修复及原路径复验

- 入口：本案 Run → 仿真诊断 → 模型调用，Attempt 01 CANCELLED，全部用途。
- 动作与实际：取消后 07:56:36Z 仍显示 25 条，随后切第 3 页时明细变为 0 条；点击刷新仍显示“该 Attempt 尚无模型调用明细”，旧选中 seq74 的详情仍留在右侧。
- 预期：已存在的 25 条物理调用应可稳定分页和刷新，Step 0 的汇总不能使物理明细消失。
- 复核：整页重载后恢复 25 条，再切第 3 页正常。下载 ZIP 的记录完整；尚无证据证明文件数据丢失。
- 影响：过程证据在界面上暂时不可见，容易误判未执行模型调用。状态登记为待复现，未将整页刷新恢复称为已修复。
- 验收条件：同 Run / Attempt 在取消前后，切换页签、分页与刷新均稳定显示真实物理请求；Step 0 汇总与未提交审计明细各自保持正确。
- 授权与处置：首次发现后依据仓库案例规则停止新增运行验收。用户随后明确选择“同意修复并继续验收”，工程子 Agent 开始复现和修复；完成后的测试、重启与原路径复验另行追加，不把授权当修复完成。
- 只读源码定位：用途汇总没有直接清空明细；完整刷新提前清空 `traceItems`，并发全量/增量响应缺少独立请求序号，与本次现象相容，但当次请求顺序未保留，不能将竞争假设当作已证实的唯一根因。

工程子 Agent 随后使用真实 `loadModelTraces` / `renderModelTraces` 函数建立确定性回归，修复前三项均失败：刷新返回前分页使 25→0；空增量与全量刷新交错使 25→0；旧全量响应晚到使新快照 1→26。这确认了对应竞态路径存在；原浏览器那次的精确网络时序仍未记录。

修复仅涉及 `adapters/web/static/shell/console-api.js` 和新增前端回归：同查询刷新等待或失败时保留旧快照，成功时原子替换；Run、Attempt、用途筛选和工作区代次构成查询范围，每个活动请求具有独立所有权；增量轮询等待同范围活动刷新，旧响应不改列表或游标。真实空结果仍可清空数据。新增 12 项测试通过，前端全量 **190 passed**；独立审查定向 **14 passed**，未发现阻断问题。

主 Agent 于北京时间 16:08 再次执行隐藏窗口重启；旧监听 PID 49376 停止，新启动器 PID 67184、新监听 PID 48768，脚本健康检查成功。原始日志见 [restart-after-trace-fix.log](restart-after-trace-fix.log)。随后回到原 Run / Attempt 的原浏览器路径：

| UI 操作 | 实际结果 |
| --- | --- |
| 重载模型调用页 | 25 条，Attempt 01 CANCELLED |
| 第 3 页 → 手动刷新 → 立即第 4 页 | 保持 25 条，显示第 4/5 页 |
| 切运行日志再回模型调用 | 保持第 4/5 页、25 条 |
| 用途输入 embedding 并刷新 | 本 Run 确实无该用途，0 条正常 |
| 清空用途刷新，切第 5 页 | 恢复 25 条，末条 seq74 ABORTED |

至此 B04-001 工程回归与原路径 UI 复验均已通过，恢复本案后续运行验收。之前“待复现/等待授权”仅表示发现阶段状态。

## 第二次正常运行

主 Agent 在已封存实验概览点击“执行下一次仿真”，预检仍为 0 阻断、0 警告、1 通过，确认规模为 25 Agent × 120 步后点击确认。此 Run 用于继续正常执行，前一次专门的取消验收 Run 保留。未复制或改变实验、Brain、地图、人物或模型参数。

- Run：`e01a6e72-8899-442a-a768-74ee05fa2eb8`。
- Attempt：`dbf7b2b5-ad39-492e-a36c-996e62d91c62`。
- [运行页](http://127.0.0.1:8800/experiments/b0b85db8-e439-450b-93ad-670d7d6a9caa?view=results&run_id=e01a6e72-8899-442a-a768-74ee05fa2eb8&result_tab=operations&tab=traces)。
- 启动后 UI 已显示 RUNNING、0/120；首个物理调用 seq2 成功，32856 ms；seq4 为进行中的下一请求，证明并非只有创建成功提示。
- 新旧 Run 选择器来回切换：旧 Attempt 显示其 25 条；新 Attempt 当时显示自身 2 条，没有混入旧记录。新 Run 继续运行。
- 后续交付检查：UI 明细共 8 条，7 次 SUCCEEDED（seq2/5/8/11/14/17/20）、1 次 RUNNING（seq22），最大已完成延迟 34194 ms、重试 0。仍为 RUNNING、0/120，本轮尚未完成 25 人的统一提交。

以上是执行中的观察边界，不是完整 120 步完成报告。25 人按 Step 依次执行，0/120 表示尚未完成第一步的整轮提交，不等于未发生模型/工具调用。后续提交事实、回放和其余接口需以此 Run 后续产生的真实结果验收。
