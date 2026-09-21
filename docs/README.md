# 当前文档索引

本页是项目文档的统一入口。阅读顺序为：仓库原则 → 当前架构与功能合同 → 操作和开发指南 → 对应案例的验证记录。文档位置、测试通过和功能全面验收是不同维度。

**当前规则与合同**

| 文档 | 用途 |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | 仓库级强制约束和教材案例工作边界 |
| [可移植实验包与文件化 Run](capability-composition-platform-design.md) | 四模块、gaexp/garun、身份、物理复制、提交与恢复 |
| [实验包构建 UX](experiment-resource-composition-ux.md) | 公共资源与实验副本、工作区作用域、封存和列表交互 |
| [Game Object Skill](game-object-skill-runtime.md) | 对象自主执行、交互回复、权限、可见事实和恢复 |
| [地图通路与导航](map-navigation.md) | 碰撞、只读导航、视野和真实 MOVE 边界 |
| [MOVE 活动语义](move-activity-contract.md) | 自然语言移动活动与内核位移事实的关系 |

这些文件定义现行合同；具体实现状态和验收范围还要核对下列验证记录。发现源码与规则冲突时，应报告差异，不从历史文档中挑选另一套设计。

**操作与开发**

| 入口 | 内容 |
| --- | --- |
| [项目 README](../README.md) / [English](../README_en.md) | 安装、Studio 启动和最短使用路径 |
| [运行手册](operations-runbook.md) | CLI、数据目录、控制、备份、恢复与排障 |
| [模型配置](model-configuration.md) | Studio 模型中心、明确模型 ID、包内环境变量名与本机凭据 |
| [中文代码导览](code-guide-cn.md) | 真实入口、调用链、共享内核与工程边界 |
| [测试指南](test-strategy.md) | 当前专项、前端检查、浏览器验收及历史门禁限制 |
| [CLAUDE 导航](../CLAUDE.md) | 指向本索引和 AGENTS.md 的开发工具入口 |

**案例与验证证据**

- [教材总纲](book/book-capabilities-and-case-roadmap.md)和[案例目录](book/sample/README.md)是教材的正式入口。
- [案例 1：晨间生活](book/sample/01-morning-routine/README.md)；核对其[验证记录](book/sample/01-morning-routine/settings/verification-record.md)与[待定事项](book/sample/01-morning-routine/verification/pending-decisions.md)。
- [案例 2：门口读书](book/sample/02-doorway-reading/README.md)；核对[正式运行记录](book/sample/02-doorway-reading/verification/formal-run-record.md)及同目录的对照证据。
- [交通治理案例资料目录](仿真实验/交通治理场景/)保留实际作者素材、Skill、参数与逐问题验收记录；不因本次文档整理而改变实验状态。
- [对象 Skill 验证](game-object-skill-verification-2026-09-13.md)、[回放与 Skill 加载工程记录](replay-skill-loading-engineering-2026-09-13.md)、[对应回归记录](replay-skill-loading-regression-2026-09-13.md)属于指定时间和范围的证据，不代表全项目验收完成。

docs/images 下是素材及相关来源资料；[早期校园案例草案](sample/README.md)统一引用 creek-university-v2 的共享源素材。[交通治理历史底稿](交通治理场景_back/README.md)保留独有内容，重复图表指向现存原件；这些草稿不作为现行配置教程。

源素材可以只维护一份，但正式案例、实验包和 Run 要保留各自的物理副本。合并源素材不能改写验收证据、导出包或哈希清单。

**维护方式**

过期架构、旧运维和测试说明、阶段台账及 HTML 原型已从工作树删除，不另设归档副本。需要追溯时查阅 Git 历史；有效案例的验收记录与待处理事项仍在各案例目录维护。

当前文档只维护一份权威说明。改变入口或合同后，同步 README、代码导览、本索引与适用的操作指南。将阶段记录标明日期、对象和验证范围；不要将案例参数、某台机器的端口或历史成功次数写成系统默认值。
