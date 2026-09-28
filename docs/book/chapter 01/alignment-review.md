# 第一章图文契合复核

本轮按本图要回答的问题、前置概念、图内标签、图注和相邻例子逐项核对。当前图注放在Mermaid之后、原图参考之前；旧PNG、SVG与绘图脚本保持原样。

| 当前图 | 读图入口与本节的连接 |
| --- | --- |
| [图1.1-1](01-what-is-a-large-model.md#figure-llm-answer-check) | 先用同一份手作安排检查两件事：15人能否坐下，10:00能否使用房间。 |
| [图1.1-2](01-what-is-a-large-model.md#figure-model-family-map) | 这里有三种不同的读法：看技术归属、看命名角度，再看规模由哪些量描述。 |
| [图1.1-3](01-what-is-a-large-model.md#figure-tokens-and-context) | 同一句话涉及两个问题：文本怎样变成模型可处理的数值，本次请求又要占用多少上下文空间。图中分开画出。 |
| [图1.1-4](01-what-is-a-large-model.md#figure-text-generation) | 从已经送入的内容出发，沿回环看模型怎样接着生成；回环结束才形成完整回答。 |
| [图1.1-5](01-what-is-a-large-model.md#figure-attention-and-order) | 先把“容量12人”和“参加15人”联系起来，再比较“甲交给乙”和“乙交给甲”：关联信息与保留顺序都影响理解。 |
| [图1.1-6](01-what-is-a-large-model.md#figure-training-and-inference) | 今天把房间表交给助手，与训练一个模型有什么不同？先看三个框的标题是否写着“更新参数”。 |
| [图1.1-7](01-what-is-a-large-model.md#figure-knowledge-sources) | 回答里的信息可能来自参数，也可能是这次才读到的材料。沿进入“当前上下文”的连线，区分后面表格中的四种来源。 |
| [图1.1-8](01-what-is-a-large-model.md#figure-variability-and-errors) | 先核对容量与预约事实，再比较两次回答是否相同；这两个判断不能合并成一个“稳定可靠”。 |
| [图1.2-1](02-from-model-to-application.md#figure-s12-time-check) | 本小例只安排10人参加30分钟活动：房间容量12人，09:00—12:00开放，10:00—10:30已有预约。先在时间轴上找出可用的连续半小时。 |
| [图1.2-2](02-from-model-to-application.md#figure-application-layers) | 时间轴给出了约束，应用还要把它变成可核对的交付。沿图看输入、候选、实际执行与核验各由哪里承接。 |
| [图1.2-3](02-from-model-to-application.md#figure-s12-model-and-app) | 保持模型不变，只改变它拿到的资料、可用工具和检查程序，开放日任务的结果会怎样变化？ |
| [图1.3-1](03-measuring-capabilities.md#figure-s13-evaluation-chain) | 这一小节用十道开放日事实题计算准确率。先把目标、题目、判据、分母和结论范围连起来，再读下面的逐题得分。 |
| [图1.3-2](03-measuring-capabilities.md#figure-s13-extraction-counts) | 这次改做报名提取：真实12项，输出10项，正确找到8项。图把“应该找到多少”和“报出来多少”分成两个分母。 |
| [图1.3-3](03-measuring-capabilities.md#figure-s13-repeat-success) | 对同一道固定任务，假设每次独立成功的概率都是0.8。增加尝试次数时，“至少一次成功”和“每次都成功”会朝相反方向变化。 |
| [图1.3-4](03-measuring-capabilities.md#figure-s13-evidence-check) | 要回答“阅读活动是否需要改期”，先找原安排与最新占用通知，再检查回答是否正确引用它们。下图把证据进入候选与最终交付分开。 |
| [图1.4-1](04-capabilities-and-benchmarks.md#figure-s14-instruction-levels) | 先沿用1.3.3的两份通知：每份有四项要求。逐条数合格项，再逐份检查是否全部合格，会得到两个不同的比例。 |
| [图1.4-2](04-capabilities-and-benchmarks.md#figure-s14-code-evidence) | 下面比较两种代码任务：按说明写时间冲突函数，以及修复报名程序中的重复记录。它们各自需要执行证据。 |
| [图1.4-3](04-capabilities-and-benchmarks.md#figure-s14-tool-state) | 以预约教室为例，先区分“模型提出预约参数”和“宿主真的提交预约”。沿消息方向找到真实状态返回的位置。 |
| [图1.4-5](04-capabilities-and-benchmarks.md#figure-s14-temporal-input) | 同样完成搬椅子和贴指示牌，两段视频的先后顺序仍可能相反。回答“先做什么”需要时间信息。 |
| [图1.5-1](05-capability-snapshot.md#figure-s15-score-card) | 接下来每个成绩都先填这四格：谁、测什么、怎样运行、证据从哪里来。随后用HLE快照具体读一遍。 |
| [图1.5-2](05-capability-snapshot.md#figure-s15-partial-strict) | 先看两项指标的定义，再按每个模型相邻的两根柱读数；不要把完成部分检查点的得分读成全部任务通过率。 |
| [图1.5-3](05-capability-snapshot.md#figure-s15-comparison-boundary) | 读下面的SWE与ProgramBench成绩前，先检查这四种“看似一样”的条件。尤其不要把不同任务上的80%视为同一能力水平。 |
| [图1.6-1](06-choosing-and-evaluating-models.md#figure-s16-task-grid) | 先用8题练习和调整，再冻结另一组24题评价。正式评价分为6组、每组4题，避免把调过提示的练习题算进成绩。 |
| [图1.6-2](06-choosing-and-evaluating-models.md#figure-s16-choice-tradeoff) | 同样24次任务，乙完成更多，但费用和耗时更高。先检查是否出现不能接受的“假完成”，再比较成功任务的平均成本。 |
| [图1.6-3](06-choosing-and-evaluating-models.md#figure-s16-selection-process) | 开放日筹备助手与仿真角色使用同一套选择步骤，但各自的任务、硬约束和证据不同。图中的五步用于填写后面的选择记录。 |

另核对两幅独立原图：图1.3-5先说明8/10与80/100再读Wilson区间；图1.4-4先区分三类视觉问题，并说明右侧柱形缺刻度，不能计算精确人数差。

代码与SOP不因图文编辑而改写，人工算例、来源快照和运行事实边界分别保留。原图是历史设计参考；当前表述以正文Mermaid和相邻图注为准。

全书渲染与完整性检查统一记录在[图文契合复核](../figure-context-review.md)。本轮是文稿编辑，不构成真实模型测试或案例运行验收。

[返回本章](README.md)
