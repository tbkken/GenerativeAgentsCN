# 第五章参考资料与依据范围

核查日期：**2026-09-25**。本章的委托书、交付方法、反例、模板、预算数字和教学量表均为作者设计。它们组织既有系统与案例知识，没有声称来自真实委托、当前厂商报价或服务大厅运行结果。

## 外部方法依据

[GO FAIR：FAIR Principles](https://www.go-fair.org/fair-principles/)本次已实际读取。5.8与交付入口借鉴其关于可发现、可访问、互操作、复用及来源说明的原则；必要的访问可以包含认证与授权。这些原则用于解释为什么材料应有清楚入口与出处，不构成本项目已通过认证、所有资料都必须公开，或结果已经真实有效的证明。

该原则的原始出处为 Wilkinson 等，2016，[The FAIR Guiding Principles for scientific data management and stewardship](https://www.nature.com/articles/sdata201618)，Scientific Data 3:160018，DOI 10.1038/sdata.2016.18。原始论文用于文献定位；本章具体解释以本次读取的 GO FAIR 页面为直接依据。

## 本书与项目依据

| 来源 | 支持的内容 |
| --- | --- |
| [第四章4.1服务大厅](<../chapter 04/01-service-hall.md>) | 四项需求、五名Agent、三个对象、20×16草图、76步窗口、唯一策略变化与评价口径 |
| [第四章4.9分析方法](<../chapter 04/09-comparison-and-your-study.md>)与[模板入口](<../chapter 04/README.md>) | 全体结果、未完成、分母、成本、研究报告；本章不复制一套相同模板 |
| [第三章核查记录](<../chapter 03/verification.md>) | 已观察入口与源码范围，尤其估算、取消、导出和Evaluator限制 |
| [第三章实验生命周期](<../chapter 03/08-experiment-lifecycle.md>) | 公共资源物理复制、DRAFT与SEALED、保存与执行范围 |
| [第三章控制与恢复](<../chapter 03/10-pause-resume-rerun.md>) | Run和Attempt、暂停、续跑、重跑及当前入口边界 |
| [第三章包与复现](<../chapter 03/13-packages-and-reproduction.md>) | config／exp／run、普通结果ZIP、正式封存与独立环境验证的区别 |
| [仓库规则](../../../AGENTS.md) | 模块边界、文件协议、案例浏览器操作、问题报告与历史证据保留 |
| [对象执行器](../../../src/generative_agents/ga_runtime/skills/objects.py)与[能力服务](../../../src/generative_agents/ga_runtime/capabilities/server.py) | 对象输入、身份、记忆、ACT／WAIT与同次responses、下一轮投递 |
| [Skill文档解析](../../../src/generative_agents/ga_protocol/skills/documents.py) | 本章咨询台文字中的name与description元数据 |
| [清单定义](../../../src/generative_agents/ga_protocol/schemas/manifests.py)与[包读写](../../../src/generative_agents/ga_protocol/packages/io.py) | v2类型、包身份、内容完整性与归档机制 |
| [Replay读取](../../../src/generative_agents/ga_replay/reader.py) | 从包内已提交事实读取，身份、边界和完整性检查 |

本轮沿用当前工作区的实现，包含已有未提交修改，不将Git HEAD单独视作完整内容摘要。本次没有新的浏览器验收，没有把第三章的核查扩大为服务大厅成功运行。

## 教学材料与验证强度

5.4提供一份完整咨询台基线文本和一段替换文本，不包含其他三份已完成资源，也不是可导入实验包。5.5的608轮次、1,520逻辑调用、1,900物理尝试和5.13美元均来自明示假设；两Run只是演算范围，不是已确定的真实研究样本计划。

[项目证据链](figures/project-handoff.svg)和[接收检查](figures/recipient-checks.svg)由[绘图脚本](figures/render_figures.py)原创生成，没有使用运行数据。[项目委托书](capstone/brief.md)和四份模板均保持实施项待填写。具体已执行的格式、算术、链接和图形检查见[核查记录](verification.md)。

[返回本章](README.md)
