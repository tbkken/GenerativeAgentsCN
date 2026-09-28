# 第一章参考资料

正文在相应论述旁给出直接来源，本页按主题整理查阅入口。查阅日期为 **2026-09-25**；论文年份、报告日期和模型评测日期分别按原始材料理解。旧论文用于解释原理或方法，不自动代表当前模型成绩。

## 模型与应用基础

| 来源 | 类型与本章用途 |
| --- | --- |
| [Attention Is All You Need](https://arxiv.org/abs/1706.03762) | 2017 年原论文；Transformer 与注意力架构 |
| [Language Models are Few-Shot Learners](https://arxiv.org/abs/2005.14165) | 2020 年原论文；自回归语言模型及上下文中的少样本任务适应 |
| [Training Compute-Optimal Large Language Models](https://arxiv.org/abs/2203.15556) | 2022 年原论文；参数、数据与计算预算的关系 |
| [Training language models to follow instructions with human feedback](https://arxiv.org/abs/2203.02155) | 2022 年原论文；示范、反馈和指令遵循 |
| [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401) | 2020 年原论文；参数知识与外部检索的结合 |
| [WebArena: A Realistic Web Environment for Building Autonomous Agents](https://arxiv.org/abs/2307.13854) | 2023 年原论文；通过真实执行环境评价任务完成 |

## 指标与评价方法

| 来源 | 类型与本章用途 |
| --- | --- |
| [Introduction to Information Retrieval：Evaluation of unranked retrieval sets](https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-unranked-retrieval-sets-1.html) | 作者教材；Precision、Recall 与 F 指标 |
| [scikit-learn：f1_score](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.f1_score.html) | 官方方法文档；宏/微平均与零分母约定 |
| [Evaluating Large Language Models Trained on Code](https://arxiv.org/html/2107.03374v2) | HumanEval 原论文；功能测试与 pass@k 估计 |
| [τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains](https://arxiv.org/html/2406.12045v1) | 原论文；最终状态与重复成功的 pass^k |
| [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/html/2306.05685v4) | 原论文；开放评价与模型裁判偏差 |
| [On Calibration of Modern Neural Networks](https://proceedings.mlr.press/v70/guo17a.html) | 原论文；概率预测与实际正确频率 |
| [scikit-learn：brier_score_loss](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.brier_score_loss.html) | 官方方法文档；Brier 的计算约定 |
| [scikit-learn：Probability calibration](https://scikit-learn.org/stable/modules/calibration.html) | 官方方法文档；概率评分与校准的区别 |
| [NIST：Confidence intervals for a proportion](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm) | 官方统计方法资料；比例置信区间 |
| [Holistic Evaluation of Language Models](https://arxiv.org/html/2211.09110v1) | HELM 原论文；多维评价、条件与局限 |

## 各类能力的代表基准

| 来源 | 本章使用的评价对象 |
| --- | --- |
| [MMLU-Pro: A More Robust and Challenging Multi-Task Language Understanding Benchmark](https://arxiv.org/abs/2406.01574) | 多领域知识与理解 |
| [Instruction-Following Evaluation for Large Language Models](https://arxiv.org/html/2311.07911v1) | IFEval；可验证指令及整题/逐条通过 |
| [DeepSeek-R1 原始研究报告](https://arxiv.org/html/2501.12948v1) | 使用 AIME 2024 的模型研究设置；用于说明单次与投票口径 |
| [GPQA: A Graduate-Level Google-Proof Q&A Benchmark](https://arxiv.org/html/2311.12022v1) | 专家设计的科学问题与分集条件 |
| [SWE-bench Verified 官方说明](https://www.swebench.com/verified.html) | 软件修复、人工核验子集与代理配置 |
| [BFCL 初始方法说明](https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html) | 函数选择、参数结构与执行核验 |
| [BFCL 官方入口](https://gorilla.cs.berkeley.edu/leaderboard.html) | 版本与现行基准入口，正文不将不同版本成绩混用 |
| [LongBench v2 官方项目](https://longbench2.github.io/) | 长上下文中的多类理解任务 |
| [RULER: What's the Real Context Size of Your Long-Context Language Models?](https://arxiv.org/html/2404.06654v3) | 控制长度、检索、多跳和聚合的合成任务 |
| [MMMU-Pro 原论文](https://arxiv.org/html/2409.02813v1) | 需要图像参与的多学科问答 |
| [DocVQA: A Dataset for VQA on Document Images](https://arxiv.org/html/2007.00398v3) | 文档图像问答与 ANLS |
| [ChartQA: A Benchmark for Question Answering about Charts with Visual and Logical Reasoning](https://arxiv.org/html/2203.10244v1) | 图表问答及数值容差 |
| [Robust Speech Recognition via Large-Scale Weak Supervision](https://arxiv.org/html/2212.04356v1) | 语音识别、WER 与文本归一化 |
| [Video-MME 原论文](https://arxiv.org/html/2405.21075v1) | 视频时长、字幕/音频与理解任务 |
| [GenEval: An Object-Focused Framework for Evaluating Text-to-Image Alignment](https://arxiv.org/html/2310.11513v1) | 图像生成中的数量、属性和空间关系 |
| [VBench 原论文](https://arxiv.org/html/2311.17982v1) | 视频生成的分维度质量评价 |
| [FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation](https://arxiv.org/html/2305.14251v1) | 将长回答拆成可核验事实陈述 |

## 1.5 的公开成绩与方法

| 原始来源 | 来源性质及读取范围 |
| --- | --- |
| [Claude Fable 5.1 & Claude Mythos 5.1 System Card](https://www.anthropic.com/claude-fable-5-1-mythos-5-1-system-card) | Anthropic 厂商自报；核对所引表、方法、数据修订及失败个案，PDF 页码见正文 |
| [Claude Fable 5.1 / Mythos 5.1 发布说明](https://www.anthropic.com/claude-fable-and-mythos-5-1) | Anthropic 厂商自报；交叉核对结果及生产防护/模型接替脚注 |
| [Gemini 3.8 Flash：Model evaluation methodology](https://deepmind.google/models/evals-methodology/gemini-3-8-flash) | Google 厂商自报；核对题集、调用标识、方法和原始结果图 |
| [SWE-bench 官方榜单](https://www.swebench.com/) | 基准维护方发布；采用有日期的具体行，不把列入官方站点当成已独立复核 |

更细的样本量、预算、数据修订、未披露项及禁止直接比较的条件，保存在[评测数据记录](evidence-data.json)。它记录本次查阅所能确认的事实，不是原始运行轨迹，也不是本书独立复测包。

本章未复制完整基准题集或厂商图表。教学题目与概念图由本书编写；2026-09-26完成六节共27幅配图，[配图索引](figures/README.md)提供图源和示意边界。真实模型数字通过注明来源的表格及1.5对照图呈现。更新正文数据时，应同时更新日期、来源、条件和解释，不能只替换一个百分比。

此前为1.1图解改版重新读取了上述五篇模型基础论文的arXiv摘要页，核对其所支持的简短原理说明；没有更新1.5的能力快照，也没有重新运行基准。全章增图时保留原证据日期，未重新核验全部外部来源。图中的容量、切分、关联线与回答四格均为教学示意，详见[改版核查](visual-revision-2026-09-26.md)。

[返回本章目录](README.md)

2026-09-26后续图源转换：25组采用正文内Mermaid＋下方原图参考，2幅统计／视觉图仅使用原图；仅调整表达与布局，没有更新上述研究来源或能力快照。见[Mermaid转换记录](mermaid-revision.md)。
