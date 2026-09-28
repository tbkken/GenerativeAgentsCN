# 第四章参考资料与依据范围

资料核查日期：**2026-09-25**。本章八个场景和其中的题目、人物、SOP、判分示例为本书作者设计，不是外部研究的复述或已经获得的运行结论。外部资料用于核对研究描述和分析方法，项目资料用于确定实际系统边界。

## 方法资料

| 来源 | 本章使用之处 | 不应据此声称的内容 |
| --- | --- | --- |
| Grimm 等，2020，[The ODD Protocol for Describing Agent-Based and Other Simulation Models: A Second Update](https://www.jasss.org/23/2/7.html)，JASSS 23(2)7，DOI 10.18564/jasss.4259 | 借鉴明确说明目的、实体、尺度、过程、初态、输入与实现依据的写法 | 本章模板等同完整 ODD；遵循文档格式即可证明模型真实 |
| NIST/SEMATECH，[Randomized block designs](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm) | 解释在相近批次内比较条件、记录非研究因素；本书将其应用到模型实验的执行安排 | 批次记录自动消除远端服务变化，或随机化保证每次模型输出相同 |
| NIST/SEMATECH，[Censoring](https://www.itl.nist.gov/div898/handbook/apr/section1/apr131.htm) | 理解固定截止时刻后事件时间未知，避免把未完成样本填成按时完成 | 本章已为任务完成时间拟合生存模型，或所有中断都可按相同删失机制处理 |

以上三页本次已实际读取。关于如何把方法落到本书场景、怎样组织模板与人工演算，是作者的教学应用；不能将这些设计表述为原作者对 GenerativeAgentsCN 的验证或推荐。

## 项目依据

- [仓库规则](../../../AGENTS.md)：模型、内核、文件协议、行为和案例操作边界。
- [第三章正文](<../chapter 03/README.md>)与[第三章核查记录](<../chapter 03/verification.md>)：操作基础及当时确认的实现范围。
- [场景设计底稿](../scenario-case-designs.md)：本章的八类问题、原始对照设计与制作顺序；正文提供了更具体的教学条件。
- [Scheduler](../../../src/generative_agents/ga_runtime/engine/scheduler.py)和[算法常量](../../../src/generative_agents/ga_protocol/schemas/engine.py)：Step 1 的时间点、步长和固定移动预算。
- [能力服务](../../../src/generative_agents/ga_runtime/capabilities/server.py)、[对象执行](../../../src/generative_agents/ga_runtime/skills/objects.py)、[记忆流](../../../src/generative_agents/ga_runtime/memory/stream.py)：身份、消息、导航知识、对象状态与当前词法检索。
- [Run 执行与质量](../../../src/generative_agents/ga_runtime/lifecycle/executor.py)：业务判分与运行终结状态分开，不能仅凭 Evaluator 资源存在推定已经运行。

核查以当前工作区为准，并非一个干净发行版本。第四章未重新执行第三章浏览器验收，也未对八个新场景进行 UI 配置、模型调用或运行；源码说明不能代替这些证据。

## 教学数据与图

[comparison.json](examples/comparison.json) 的六行数据全部人工构造，演算程序只核对汇总算术。[研究流程图](figures/study-design.svg)是方法示意，[比较图](figures/teaching-comparison.svg)使用这组人工数据。两图由[绘图脚本](figures/render_figures.py)原创生成，均不是实际系统截图。

每节的人工判分示例都在本节明确标注。学生后续取得真实结果时，应另建带证据的文件，保留作者原示例，避免把教学行号改成 Run 名称后冒充实测。

[返回本章](README.md) · [本次检查记录](verification.md)
