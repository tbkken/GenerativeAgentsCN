# 移动预算：当前合同与浏览器入口

**预算来源为只读代码核查，实测证据为正式 Run 的浏览器记录与导出事实。** 本例保存 1 分钟步长和初始 (7,7)/门廊，正式正常流程已 `COMPLETED 8/8`；实际三次 MOVE 消费 4+4+2 格，见 [正式运行记录](../verification/formal-run-record.md)与 [导出核验](../verification/exports/README.md)。静态查询与对象地址导航的终点不同，分别记录如下。

## 速度来自哪里

当前 `src/generative_agents/ga_protocol/schemas/engine.py` 的 `GA_CN_V1` 固定 `movement_tiles_per_minute=4`；同文件注册表只有 `ga-cn-v1`。`schemas/experiment.py` 的 `EngineConfig.algorithm_version` 也只允许 `ga-cn-v1`。因此当前正常配置下的世界移动速度是 **4 格/虚拟分钟**。

Runtime 在 `src/generative_agents/ga_runtime/lifecycle/executor.py` 根据实验中的 `engine.algorithm_version` 取得 profile。`ga_runtime/engine/scheduler.py` 的 `_movement_budget()` 从该 profile 读取速度并按步长计算：

`movement_budget = max(1, stride_minutes × max(1, movement_tiles_per_minute))`

`src/generative_agents/ga_runtime/engine/world.py` 在 MOVE 提交时仅消费 `planned_path[:movement_budget]`，余下部分保留为 remaining path。其他普通 ACT 不消费这条移动路径。

## 当前 UI 能设置什么

当前没有可单独修改“格/分钟”的速度输入。浏览器中可以打开实验的 **“时间与运行参数”**，修改 **“单步时间跨度”**（输入控件 ID `stride`，单位分钟，最小值 1）。入口在 `src/generative_agents/adapters/web/static/shell/experiment-console.html`，同目录 `console-api.js` 保存为 `definition.simulation.stride_minutes`。

主 Agent 已通过 UI 将正式实验步长保存为 **1 分钟**，当前算法下每轮 MOVE 最多消费 **4 个格**，正式 Step 2–4 实际分别消费 4、4、2 格。地图逻辑 tile 的 32px、素材切片的源网格、人物显示 2.5 格和回放倍速均不是该速度设置。

## 如何让短路展示 2–3 轮 MOVE

本例地图两点路径检查为 **(7,7) → (16,7)，可达、11 格**，仅经 y8 门洞。按当前 4 格/分钟、已保存 1 分钟步长，理论上最多为 4+4+3 格，需 3 轮 MOVE。**B02-006 修复后实验已成功保存初始 (7,7)/门廊**；旧 (3,4) 及首次保存失败属于历史。对象地址导航可能在 x16/y5–8 选择另一个可走终点，不能要求返回同样的 11 格。早期 (16,6) 与纸面约 10 格仍只保留为历史候选。

上述 11 格、4+4+3 是静态查询的历史推导，**不强排 Step，也不要求模型伪造路线**。正式 Agent 使用真实阅读桌地址导航，系统选择终点 **(16,8)**，完整路径为 `(8,7) → (8,8) → (9,8) → (10,8) → (11,8) → (12,8) → (13,8) → (14,8) → (15,8) → (16,8)`，不含起点共 10 格。Step 2–4 的真实落点为 (10,8)、(14,8)、(16,8)，剩余路径长度为 6、2、0；导出逐格连续性与碰撞检查通过。

Step 3 虽已进入阅读角 Arena，仍继续 MOVE；Step 4 抵达阅读桌对象后，Step 5 才开始取书。Brain 向同一实际对象地址发 MOVE，没有把 `next_coord` 当最终目标，也没有在途中提前阅读。边界对照继续单独验收，不能用正常路线通过代替不可见、带宽或堵门条件的结果。

不用新增速度 UI 或修改代码即可完成本例设计。当前 4 格/分钟是现有算法合同，不是建议把这个速度作为以后所有案例的永久设计参数；以后版本变化时应重新核查来源。
