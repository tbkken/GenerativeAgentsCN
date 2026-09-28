# 第三章参考与源码定位

核查日期：**2026-09-25**。本章讨论本地项目，主要证据来自仓库规则、当前源码、浏览器入口观察和保留的历史案例。源码基线为 `d635c8891a34d35ca25b69abe6618013b2a9f6ca` 加当前工作区已有修改，不能只检出这个提交就假定得到同样的界面。

## 怎样阅读这些依据

项目规则描述必须遵守的合同；源码说明当前代码怎样实现；浏览器记录说明本次实际看到了什么；Run 证据说明某次执行确实发生了什么。四者有差异时，不能把应然规则写成已经通过的功能验收。本次检查与未执行项目见[verification.md](verification.md)。

以下链接用于读者定位实现，不要求初学者先读完代码。正文中的菜单与机制解释应先阅读；遇到疑问时，再按表找到对应层。

## 系统与实现

| 主题 | 主要依据 | 阅读重点 |
| --- | --- | --- |
| 总体职责与案例规则 | [AGENTS.md](../../../AGENTS.md)、[项目说明](../../../README.md) | 模块边界、文件事实、UI 操作和逐问题验收 |
| Studio 公共操作 | [api.py](../../../src/generative_agents/ga_studio/api.py)、[resources/](../../../src/generative_agents/ga_studio/resources/) | 可编辑公共资源与资源交换 |
| 页面入口与运行时布局 | [experiment-console.html](../../../src/generative_agents/adapters/web/static/shell/experiment-console.html)、[console-api.js](../../../src/generative_agents/adapters/web/static/shell/console-api.js) | 模板须与运行时脚本合看，遗留面板未必有可用页签 |
| 地图与空间选择 | [map-editor-v2.js](../../../src/generative_agents/adapters/web/static/resources/map-editor-v2.js)、[spatial-picker.js](../../../src/generative_agents/adapters/web/static/resources/spatial-picker.js) | 语义、素材、碰撞与实际选择入口 |
| 空间合同 | [spatial/](../../../src/generative_agents/ga_protocol/spatial/)、[语义索引](../../../src/generative_agents/ga_protocol/packages/semantic_index.py) | 层级地址、导航与包内语义内容 |
| Skill 文档与依赖 | [documents.py](../../../src/generative_agents/ga_protocol/skills/documents.py)、[skills/](../../../src/generative_agents/ga_protocol/skills/) | front matter、正文引用与闭包 |
| Brain、对象与执行器 | [brain.py](../../../src/generative_agents/ga_runtime/skills/brain.py)、[objects.py](../../../src/generative_agents/ga_runtime/skills/objects.py)、[executor.py](../../../src/generative_agents/ga_runtime/skills/executor.py) | 自然语言调用链、循环边界与对象执行 |
| 身份、工具与动作 | [capabilities/server.py](../../../src/generative_agents/ga_runtime/capabilities/server.py) | 感知范围、请求参数、动作权限与一次动作限制 |
| 记忆 | [memory/stream.py](../../../src/generative_agents/ga_runtime/memory/stream.py) | file_lexical 检索、有效记录、替代与失效 |
| 草稿到实验 | [workspace.py](../../../src/generative_agents/ga_studio/experiments/workspace.py)、[builder.py](../../../src/generative_agents/ga_studio/experiments/builder.py)、[editor.py](../../../src/generative_agents/ga_studio/experiments/editor.py) | 完整复制、草稿修改与封存条件 |
| 运行时间与世界事实 | [engine.py](../../../src/generative_agents/ga_protocol/schemas/engine.py)、[scheduler.py](../../../src/generative_agents/ga_runtime/engine/scheduler.py)、[world.py](../../../src/generative_agents/ga_runtime/engine/world.py) | Step 时间、移动预算、人物和对象阶段 |
| Run 与恢复 | [lifecycle/](../../../src/generative_agents/ga_runtime/lifecycle/)、[storage/commit.py](../../../src/generative_agents/ga_runtime/storage/commit.py) | 内嵌实验、Run/Attempt 身份与提交边界 |
| 模型与监督 | [models/](../../../src/generative_agents/ga_runtime/models/)、[supervision/](../../../src/generative_agents/ga_runtime/supervision/) | 请求、重试、预算、控制响应和槽位 |
| 事实与 Replay | [facts.py](../../../src/generative_agents/ga_protocol/schemas/facts.py)、[reader.py](../../../src/generative_agents/ga_replay/reader.py)、[projections/](../../../src/generative_agents/ga_replay/projections/) | 只读取已提交边界并重建状态 |
| 质量评价 | [quality.py](../../../src/generative_agents/ga_protocol/facts/quality.py)、[运行执行阶段](../../../src/generative_agents/ga_runtime/lifecycle/executor.py) | 确定性质量汇总与业务评价的实际调用位置 |
| 包协议与交换 | [packages/](../../../src/generative_agents/ga_protocol/packages/)、[资源交换路由](../../../src/generative_agents/adapters/web/routes/resource_exchange.py)、[运行导出](../../../src/generative_agents/ga_runtime/storage/exports.py) | config/exp/run、摘要、完整闭包和安全边界 |
| CLI | [main.py](../../../src/generative_agents/adapters/cli/main.py) | argparse 命令与参数；源码支持不等于浏览器有同名按钮 |

源码路径会随项目维护变化，书稿定稿前应重新核对。本章未为文档核查修改产品实现，也未执行全部产品回归；正文引用已有测试说明验证意图，不意味着本次重新运行了这些测试。

## 案例与历史证据

- [第二章活动材料](<../chapter 02/examples/data/brief.md>)和[业务规则](<../chapter 02/examples/data/rules.md>)：本章活动名称、时长、容量资料与预约冲突的背景。
- [第三章作者资料](case/README.md)：本次编写的地图、人物、Skill 与配置候选。其状态是准备完成，尚未进入新 Run。
- [晨间生活历史记录](../sample/01-morning-routine/verification/README.md)及[导出归档](../sample/01-morning-routine/verification/exports/README.md)：用于讲解连续活动与证据保留。当前工作区缺少该案 `settings/`，不能称材料齐全。
- [穿门阅读正式记录](../sample/02-doorway-reading/verification/formal-run-record.md)及[阻断记录](../sample/02-doorway-reading/verification/blockers.md)：用于理解路径、移动预算与边界对照。B02-007 的封门回放和导出核验未闭环。
- [全书资料规范](../references-and-evidence.md)：统一区别外部文档、源码、历史运行与教学设计。

新地图、四位人物及两幅原理图由本章的[素材脚本](case/render_assets.py)与[配图脚本](figures/render_figures.py)绘制。PNG 用于显示和后续上传，SVG 是可编辑来源；它们不包含实际系统运行画面。

[返回本章](README.md)
