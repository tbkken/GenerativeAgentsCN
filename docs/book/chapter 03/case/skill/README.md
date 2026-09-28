# 青禾开放日：行为材料

这五份 `SKILL.md` 是第三章的可编辑教案。目录结构供本项目 `SkillRegistry` 离线解析；它们不是 `.gaexp`，也不代表已进入 Studio、已封存或已成功运行。

| 文件 | 在 UI 中创建的类型 | 使用位置 |
| --- | --- | --- |
| [qinghe-open-day-brain](brains/qinghe-open-day-brain/SKILL.md) | 大脑 | 实验唯一 Brain |
| [qinghe-compare-options](atomic/qinghe-compare-options/SKILL.md) | 单个技能 | Brain 按需调用 |
| [qinghe-memory-review](atomic/qinghe-memory-review/SKILL.md) | 单个技能 | Brain 按需调用 |
| [qinghe-notice-board](atomic/qinghe-notice-board/SKILL.md) | 单个技能 | 公告栏根 Skill |
| [qinghe-helpdesk](atomic/qinghe-helpdesk/SKILL.md) | 单个技能 | 服务台根 Skill |

先建立两个子技能、两个对象技能，再建立 Brain，保存后从“Scripts 与 MCP”核对依赖。地图的公告栏和服务台分别绑定自己的根技能。将地图、Brain、人物与模型配置选入实验后，应核对实际递归复制的闭包，不以本目录文件存在代替 UI 操作。

本系统当前 front matter 只支持 `name`、`description`、`example_input`；类型来自目录或 UI。文档名称必须与对应目录一致，正文中的 `$技能名` 引用会被识别为依赖。两份子 Skill 只返回建议；根 Brain 负责记忆写入和世界动作。

本案例只有四名 Agent。活动人数是计划资料，不是实际模拟人数。共同 Brain 不包含服务台的私有预约事实；此事实只在服务台技能中出现。请不要把公告更新结果提前补入来访者的人物资料，否则会破坏信息传播练习。

对象初始公开状态约定：公告栏 `original`，服务台 `ready`。公告栏接受真实组织者请求后，将 `state` 改为 `revised`，并在私有 `notice_text`、`approved_request_id`、`updated_at` 中保留内容和来源。地图可只提供初始 `state`，正文由技能给出初始解释。公告栏两张状态图片的键分别为 `original`、`revised`；服务台 `ready` 沿用背景默认外观，不需要单独的状态图片。其他对象内部字段不自动公开，Agent 要通过 `INTERACT` 查询正文。

本资料不固定出生坐标、路径或发言 Step。人物读取实际 `IterationContext`；导航使用当前感知或已知空间中的真实地址；对话和请求使用运行时身份。四人初始场所分别为公共大厅、手作室、交流室、阅读室，具体装配以本章人物与地图材料为准。

校验边界：可以使用本项目的纯文档解析器读取名称、类型、依赖和工具引用。这样的离线检查不调用模型、不创建实验，也不证明对象交互、导航或故事结果成功。运行验收仍须在浏览器配置、封存、运行并保留真实事实。

2026-09-25 的资料检查已通过：使用 `SkillRegistry.list/get/dependencies/snapshot` 和 `referenced_mcp_tools` 只读解析本目录，得到五份文档、一个 Brain、四个 atomic Skill；Brain 的两个子依赖均存在，Brain 闭包共三份，Brain 加两个对象根的合并闭包共五份；没有脚本附件。共同 Brain 与公告栏不包含服务台的预约截止时间。此次检查没有调用模型、创建实验、执行 Runtime 或验证 UI。
