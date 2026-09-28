# 第四章：文档内Mermaid转换记录

本轮范围是十节正文的30个图位及本章目录、配图索引。**26组采用上方正文内Mermaid、下方原图参考的双图对照，另4幅独立使用图片。**每幅关系图仅在所属正文维护一份当前Mermaid源；章README中的研究总览改为链接，不再复制同一图。

转换位置统一使用：

```text
<!-- book-figure: 原图片stem -->
随后是mermaid代码围栏中的图源
代码块结束后：**原图参考（转换前）**
对应的原始PNG图片引用
```

没有另建`.mmd`，没有删除或改写PNG、SVG、Python绘图脚本。图号和原图注保留；下方原图供对照，不自动跟随Mermaid后续编辑。当前编辑入口仍是正文Mermaid，见[逐图索引](figures/README.md)。

## 26组Mermaid与原图对照清单

| 正文 | 原图标识 | 当前形式 | 保留的信息 |
| --- | --- | --- | --- |
| 00 | study-design | flowchart | 共同条件、单变量、独立Run、证据和成本 |
| 00 | study-information-boundaries | flowchart | 作者答案、角色私有、共同材料分开流转 |
| 00 | study-time-and-repeats | flowchart | 61时间点覆盖60分钟；Run与Attempt区别 |
| 4.1 | hall-intervention | flowchart | 缺字段才问，基线推测，对照真实补充，两组共同后续 |
| 4.1 | hall-scoring | flowchart | 四任务分母，H2有效回应但未闭环，3/4、2/7、3/4 |
| 4.2 | handover-sources | sequenceDiagram | 查询、保存、通知、接收、复述；独立自查不抬交接分 |
| 4.2 | handover-checklist | flowchart | 五条原始事实汇总再整理，不与五栏目机械对应 |
| 4.2 | handover-scoring | flowchart | K1/K2/K5到达，K3错、K4自查，3/5及辅助分母 |
| 4.3 | gallery-evidence-chain | flowchart | 发现、匹配、实际抵达、ACT；讲解员补救与非像素感知 |
| 4.3 | gallery-scoring | flowchart | 四人、40格、缺活动／超预算，2/4及全体120格成本 |
| 4.4 | correction-sequence | sequenceDiagram | 三人逐个确认后才更正；逐人通知，不自动广播 |
| 4.4 | correction-belief-states | flowchart | 当前信念追加／替代，历史观察保留，真实ID与limit=8 |
| 4.4 | correction-scoring | flowchart | 个人接收边界，甲0/2、乙2/2、丙问句不计 |
| 4.5 | learning-information | flowchart | F1—F4分别私有，主持人实际取得，定稿后逐人传达 |
| 4.5 | learning-teacher-schedule | flowchart | 三条时段—活动—教室对应链、36选1、教师答案边界 |
| 4.5 | learning-scoring | flowchart | C4不满足时6/7仍非正确，信息共享3/4不能靠猜补分 |
| 4.6 | deliberation-constraints | flowchart | 四候选各人的条件匹配，不存在全员可接受项，不代投票 |
| 4.6 | deliberation-confirmations | flowchart | 理由复述和候选征询是两类确认，换候选重新征询 |
| 4.6 | deliberation-scoring | flowchart | R5遗漏、三人明确回应、分歧记录，4/5、3/3、1/2 |
| 4.7 | closure-executed-path | flowchart | 四执行点对应三条绿色边，灰色剩余计划不计路程 |
| 4.7 | closure-scoring | xychart-beta | 条值12/18/9；类别明确两人已到达、一人未到达 |
| 4.8 | memory-timeline | flowchart | 完整日期和+08:00、10分钟步长、145/289步、窗口外承诺 |
| 4.8 | memory-instance-types | flowchart | 一次性、按日期周期、本人明确接受承诺分别判 |
| 4.8 | memory-scoring-cost | flowchart | 2/5重复、3/3闭环、6/8保真、1440/(3×24)成本 |
| 4.9 | comparison-denominators | flowchart | Run同权55.6%与任务同权20%的分母区别 |
| 4.9 | comparison-cost | flowchart | A/B全部投入、闭环分母、逻辑与物理尝试分开 |

合计：23个flowchart、2个sequenceDiagram、1个xychart-beta。简单条形可以用Mermaid表达，因此4.7人工移动负担也已转换；到达状态写在类别中，不依赖某种颜色才能识别。

## 4幅独立图片的保留理由

| 图 | 保留的信息 | 不用普通关系图替代的原因 |
| --- | --- | --- |
| hall-map | 20×16坐标、四区矩形、边界 | 节点自动布局不保留坐标比例和空间归属 |
| gallery-layouts | 24×18，两块同尺寸非阻挡牌的精确位置 | 必须同时比较位置、区域和不变碰撞，不能把相邻关系当距离 |
| closure-layouts | 17×11墙格、上下缺口、公告占格、绕行 | 碰撞与路径需要逐格几何，自动连线不证明可通行 |
| teaching-comparison | 两面板散点，每Run比率、成功子集时延、汇总线与未完成数 | 简单柱形／折线不能等价保留这些不同观察单位和分布；人工数据原图不重绘 |

## 内容与修改边界

- 关系图首次批量替换时，程序剥离替换前图片和替换后Mermaid，比较其余文本一致；原正文、图注和既有非Mermaid代码块保持不变。
- 4.7条形图另作单处替换；原有`2/3`到达率、`39/3`移动负担、`2/20`无效率解释仍在正文，不把柱形高度当系统实测。
- 教师解答、角色知识边界、正式通知与接收顺序、固定窗口和各项指标分母均保留。新图中的关系没有给角色增加公共答案或工具权限。
- 未改examples数据／算法、templates、产品源码、模型配置、数据库、实验或历史Run。上一轮`visual-revision-2026-09-26.md`与`verification.md`作为历史记录保持原样。
- 双图对照调整只在26个Mermaid块后插入对应原图参考。程序逐文件核对：移除本次新增参考后，正文与调整前完全一致；Mermaid图内源码、原图注和既有代码均未改，4幅独立图片没有重复添加。

## 检查与视觉重点

2026-09-26完成以下检查；格式转换与图像检查不构成系统或案例运行验收。

| 检查 | 结果 |
| --- | --- |
| 图源清点 | 26个Mermaid块、26个唯一标识；23个flowchart、2个sequenceDiagram、1个xychart-beta |
| 双图配对与链接 | 26个Mermaid块分别紧接同stem的PNG原图参考；另4幅独立图片，共30个正文PNG引用，无重复、无缺失；章内Markdown本地链接无缺失 |
| 真实渲染 | 全书使用Mermaid 11.12.0与Chrome渲染；本章26图全部成功，语法错误0、文字越画布0 |
| 人工视觉复核 | 查看覆盖26图的总览，并单看密集图；修订5图后逐幅重新查看实际PNG |

人工检查发现并修正了两幅过宽图和教师解答长标题遮挡；后者没有越出画布，仍需人工判断。

| 本轮复看图 | 修订结果与最终画布 |
| --- | --- |
| study-information-boundaries | 三条纵向信息分类链；从1814像素宽收紧至794×409，未增加跨角色共享箭头 |
| study-time-and-repeats | 上下两个独立分组；从1615像素宽收紧至483×831，仍为61个时间点／60分钟、3个独立Run |
| handover-sources | 966×998；私有K1—K5与按真实请求返回分开表述，不暗示一次查询必得全部资料 |
| learning-teacher-schedule | 589×1108；短标题不再压住首排节点，完整日期、时区与禁发角色说明保留在独立注记 |
| deliberation-confirmations | 675×1045；候选改变后回到逐人征询，不沿用旧候选同意 |

其余重点复核结果：

1. `handover-checklist`的五条事实汇入整理节点，再分到五个栏目，视觉上没有机械一一配对。
2. `learning-teacher-schedule`三条分配链与教师推导可读；教师材料边界明确。
3. `deliberation-constraints`四条候选链与代表约束完整；图中明确是教师分析，不代替实际投票。
4. `closure-executed-path`前三条绿色执行边、后两条灰色虚线清楚；说明节点不计路径。`closure-scoring`为960×460，12／18／9条值与到达状态可辨。
5. `memory-timeline`完整日期和时区可见；午夜后首次检测是条件触发，窗口外承诺仍标为待履行。
6. 两张时序图的查询、回复、实际接收与逐人发送顺序可读，没有把集合参与者解释为一次全员广播。

[返回第四章](README.md) · [当前图源索引](figures/README.md)
