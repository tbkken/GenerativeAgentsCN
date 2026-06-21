# 复现实验与扩展设计 v0.1

## 文档目标

本文件用于规划全书第四部分“复现实验与扩展”。

第四部分不是随便做几个演示，而是要完成三件事：

1. 用 Generative Agents 复现论文中的经典现象。
2. 把复现实验转化成教学可操作的实验任务。
3. 为后续“前沿演进与项目升级”提供可评价的基线。

## 前置文件

- `01_original_paper_outline.md`
  - 定义论文中的经典问题和评价方法。
- `02_paper_to_code_mapping.md`
  - 定义论文概念对应到当前项目的源码位置。
- `03_source_reading_index.md`
  - 定义源码精读顺序和函数级锚点。

## 第四部分章节结构

21. 复现论文中的情人节派对传播
22. 复现镇长竞选信息扩散
23. 设计自己的小镇事件
24. 增加新角色、新地点、新关系
25. 用中文本地模型重跑论文思想
26. 如何评价一个智能体是否“可信”
27. 风险、伦理与边界

## 实验总原则

1. 每个实验必须可运行。
   - 要给出建议命令、起始时间、参与角色、step、stride。
2. 每个实验必须有证据材料。
   - 至少使用 `simulation.md`、`conversation.json`、checkpoint、`movement.json` 之一。
3. 每个实验必须有评价指标。
   - 不能只写“看起来挺像”。
4. 每个实验必须记录失败模式。
   - 没传播、乱传播、幻觉、错过地点、拒绝不合理，都要进入分析。
5. 每个实验必须能回扣论文。
   - 情人节派对、竞选、关系形成、反思消融都来自论文问题意识。

## 统一运行流程

所有实验默认在项目根目录执行：

```bash
cd generative_agents
python start.py --name <实验名> --start "<开始时间>" --step <步数> --stride <分钟> --agents "<角色1>,<角色2>,..."
python compress.py --name <实验名>
python replay.py
```

回放地址：

```text
http://127.0.0.1:5000/?name=<实验名>&step=0&speed=2&zoom=0.6
```

实验材料位置：

```text
generative_agents/results/checkpoints/<实验名>/
generative_agents/results/checkpoints/<实验名>/conversation.json
generative_agents/results/compressed/<实验名>/simulation.md
generative_agents/results/compressed/<实验名>/movement.json
```

## 统一观察材料

### `simulation.md`

用途：

- 看角色基础人设。
- 看活动时间线。
- 看对话内容。
- 快速判断实验是否出现目标现象。

### `conversation.json`

用途：

- 追踪对话传播路径。
- 统计关键词出现次数。
- 分析信息从谁传给谁。

### checkpoint

用途：

- 查看每一步角色状态。
- 查看记忆、日程、行动、位置。
- 做更细粒度的消融对比。

### `movement.json`

用途：

- 分析角色是否到达目标地点。
- 分析多人是否在同一时间窗口聚集。
- 支持浏览器回放。

---

## 实验 1：情人节派对传播

### 对应章节

第 24 章：复现论文中的情人节派对传播。

### 对应论文现象

论文中 Isabella Rodriguez 计划举办情人节派对。她通过对话邀请其他人，信息通过社交互动扩散，部分角色调整计划并参与活动。

### 当前项目内置线索

角色：伊莎贝拉。

关键设定：

```text
伊莎贝拉计划于2月14日下午5点在霍布斯咖啡馆与她的顾客举行情人节派对。她正在收集聚会材料，并告诉大家在2月14日下午5点至7点在霍布斯咖啡馆参加聚会。
```

关键文件：

- `generative_agents/frontend/static/assets/village/agents/伊莎贝拉/agent.json`

### 实验目标

验证一个事件能否通过智能体对话扩散，并影响其他角色的计划或行动。

### 推荐参与角色

最小实验：

- 伊莎贝拉
- 亚当
- 阿伊莎
- 埃迪
- 玛丽亚
- 克劳斯

扩展实验：

- 伊莎贝拉
- 亚当
- 阿伊莎
- 埃迪
- 玛丽亚
- 克劳斯
- 山姆
- 约翰
- 拉托亚
- 乔治

角色选择理由：

- 伊莎贝拉是事件发起者。
- 亚当、阿伊莎、埃迪在示例结果中已经出现过被邀请或讨论派对的线索。
- 玛丽亚、克劳斯常去霍布斯咖啡馆，有机会接触信息。
- 其他角色用于测试更大范围传播。

### 推荐运行命令

轻量版：

```bash
cd generative_agents
python start.py --name book-party-small --start "20240214-08:00" --step 72 --stride 10 --agents "伊莎贝拉,亚当,阿伊莎,埃迪,玛丽亚,克劳斯"
python compress.py --name book-party-small
```

扩展版：

```bash
cd generative_agents
python start.py --name book-party-full --start "20240214-08:00" --step 84 --stride 10 --agents "伊莎贝拉,亚当,阿伊莎,埃迪,玛丽亚,克劳斯,山姆,约翰,拉托亚,乔治"
python compress.py --name book-party-full
```

时间解释：

- 从 08:00 开始。
- `step=72`、`stride=10` 覆盖 12 小时，能跑到 20:00。
- 派对时间设定为 17:00-19:00。

### 观察问题

1. 伊莎贝拉是否主动提到派对？
2. 哪些角色直接收到邀请？
3. 是否有人把派对消息转述给第三人？
4. 是否有人明确接受、拒绝或犹豫？
5. 17:00-19:00 是否有角色出现在霍布斯咖啡馆？
6. 对话是否影响了后续日程或活动描述？

### 关键词

用于搜索 `simulation.md` 和 `conversation.json`：

```text
情人节
派对
聚会
邀请
霍布斯咖啡馆
下午5点
17:00
参加
婉拒
```

### 指标设计

#### 信息传播指标

- `direct_invitation_count`
  - 伊莎贝拉直接邀请的人数。
- `indirect_mention_count`
  - 非伊莎贝拉角色主动提到派对的次数。
- `unique_informed_agents`
  - 明确知道派对消息的去重角色数。
- `diffusion_depth`
  - 信息传播最大链路深度。

#### 态度指标

- `accepted_count`
  - 明确表示愿意参加的人数。
- `declined_count`
  - 明确拒绝的人数。
- `uncertain_count`
  - 表示可能参加或没有明确表态的人数。

#### 行为指标

- `attendance_count`
  - 17:00-19:00 出现在霍布斯咖啡馆的人数。
- `attendance_match_rate`
  - 明确接受邀请的人中实际到场比例。
- `party_related_action_count`
  - 活动描述中包含派对准备、布置、参加等行为的次数。

### 证据收集方式

1. 打开 `simulation.md` 搜索关键词。
2. 打开 `conversation.json` 找到提到派对的对话。
3. 打开 `movement.json` 检查 17:00-19:00 期间角色位置。
4. 必要时查看 checkpoint 中角色 `schedule` 和 `action`。

### 成功标准

基础成功：

- 伊莎贝拉至少邀请 1 个角色。
- 至少 1 个角色在对话中回应派对。

中等成功：

- 至少 3 个角色知道派对。
- 至少 1 个角色调整计划或在活动描述中提到准备/参加派对。

高级成功：

- 出现间接传播。
- 17:00-19:00 多名角色聚集在霍布斯咖啡馆。
- 角色态度有差异，有接受也有拒绝。

### 失败模式

1. 伊莎贝拉没有遇到其他角色。
   - 可能原因：角色路线错开，感知范围不够。
2. 对话发生但没有提到派对。
   - 可能原因：检索没有召回 `currently` 或相关记忆。
3. 角色口头接受但没有到场。
   - 可能原因：计划修订不足，或后续日程覆盖。
4. 角色无条件都接受。
   - 可能原因：模型过度合作，社会行为不够真实。
5. 派对时间或地点被模型说错。
   - 可能原因：记忆压缩、幻觉、上下文不稳定。

### 写作重点

这一章不要写成“派对故事”。要把它写成：

- 一个事件如何进入发起者的当前状态。
- 发起者如何通过对话传播事件。
- 对话如何成为其他人的记忆。
- 记忆如何改变后续行动。
- 为什么传播失败同样有分析价值。

### 可选扩展

1. 改写伊莎贝拉 `currently`：
   - 将派对地点改到玫瑰酒吧。
   - 比较传播是否仍能稳定。
2. 增加“必须邀请某人”的目标。
   - 测试计划驱动能力。
3. 修改 `generate_chat.txt`：
   - 增加“如果有正在组织的活动，可以自然提及”。
   - 对比修改前后传播效果。

---

## 实验 2：镇长竞选信息扩散

### 对应章节

第 25 章：复现镇长竞选信息扩散。

### 对应论文现象

论文中 Sam Moore 竞选镇长的信息会在小镇中传播，其他角色通过对话知道竞选、形成态度，并在后续交流中继续提及。

### 当前项目内置线索

核心角色：

- 山姆：打算竞选地方市长，正在告诉邻居。
- 汤姆：对选举感兴趣，但不喜欢山姆。
- 约翰：好奇谁会参加选举，并询问别人。
- 拉托亚：地方市长选举是她与他人交谈的核心话题。
- 乔治：经常谈论谁会参加选举。
- 亚当：好奇谁会参加选举。

关键文件：

- `generative_agents/frontend/static/assets/village/agents/山姆/agent.json`
- `generative_agents/frontend/static/assets/village/agents/汤姆/agent.json`
- `generative_agents/frontend/static/assets/village/agents/约翰/agent.json`
- `generative_agents/frontend/static/assets/village/agents/拉托亚/agent.json`
- `generative_agents/frontend/static/assets/village/agents/乔治/agent.json`
- `generative_agents/frontend/static/assets/village/agents/亚当/agent.json`

### 实验目标

验证竞选信息能否在不同角色之间传播，并观察支持、反对、中立等态度是否出现分化。

### 推荐参与角色

轻量版：

- 山姆
- 詹妮弗
- 汤姆
- 约翰
- 拉托亚
- 乔治
- 亚当

扩展版：

- 山姆
- 詹妮弗
- 汤姆
- 简
- 约翰
- 梅
- 拉托亚
- 乔治
- 亚当
- 伊莎贝拉

角色选择理由：

- 山姆是候选人。
- 詹妮弗是亲密支持者。
- 汤姆是潜在反对者。
- 约翰、拉托亚、乔治、亚当都是信息传播节点。
- 伊莎贝拉的咖啡馆是公共交流场所。

### 推荐运行命令

```bash
cd generative_agents
python start.py --name book-election-small --start "20240213-06:00" --step 84 --stride 10 --agents "山姆,詹妮弗,汤姆,约翰,拉托亚,乔治,亚当"
python compress.py --name book-election-small
```

扩展版：

```bash
cd generative_agents
python start.py --name book-election-full --start "20240213-06:00" --step 96 --stride 10 --agents "山姆,詹妮弗,汤姆,简,约翰,梅,拉托亚,乔治,亚当,伊莎贝拉"
python compress.py --name book-election-full
```

### 观察问题

1. 山姆是否主动提到竞选？
2. 山姆首先告诉了谁？
3. 哪些角色在没有直接遇到山姆时也提到选举？
4. 汤姆是否保持“不喜欢山姆”的态度？
5. 约翰、拉托亚、乔治是否推动了信息再传播？
6. 选举话题是否进入其他议题，如商业、社区安全、艺术节目？

### 关键词

```text
山姆
竞选
市长
镇长
地方市长
选举
候选人
投票
支持
反对
不喜欢
社区安全
公园设施
公共活动
```

### 指标设计

#### 传播指标

- `election_mentions`
  - 所有对话中选举相关提及次数。
- `unique_agents_mentioned_election`
  - 提到选举的去重角色数。
- `sam_direct_contacts`
  - 山姆直接谈及竞选的对象数。
- `non_sam_mentions`
  - 非山姆角色主动谈及竞选的次数。
- `diffusion_paths`
  - 可能的信息传播路径。

#### 态度指标

- `supportive_mentions`
  - 支持山姆或认可竞选计划的表达。
- `opposing_mentions`
  - 反对、不喜欢、不信任山姆的表达。
- `neutral_inquiry_mentions`
  - 只是询问或讨论选举，不表态。

#### 主题迁移指标

- `policy_topic_count`
  - 选举话题是否扩展到社区安全、公园设施、商业环境、公共服务等政策。
- `cross_domain_mentions`
  - 是否进入文学、艺术、播客、商业等其他角色专业领域。

### 证据收集方式

1. 搜索 `simulation.md` 中选举关键词。
2. 在 `conversation.json` 中抽取对话链。
3. 查看 checkpoint 中角色 `associate.chat` 和 `associate.thought`。
4. 比较不同角色对山姆竞选的表达差异。

### 成功标准

基础成功：

- 山姆至少向 1 个角色提到竞选。
- 至少 2 个非山姆角色提到选举。

中等成功：

- 选举话题进入 4 个以上角色对话。
- 出现支持、中立或反对中的至少两类态度。

高级成功：

- 出现非山姆角色之间传播选举信息。
- 选举话题扩展到政策、商业、社区生活等更具体议题。
- 汤姆的不喜欢山姆设定影响了他的表态。

### 失败模式

1. 所有人都支持山姆。
   - 说明模型过度合作，未充分利用角色冲突设定。
2. 选举信息只停留在山姆本人。
   - 说明传播链没有形成。
3. 角色讨论选举但忘记山姆是候选人。
   - 说明记忆检索或上下文约束不足。
4. 选举话题被幻觉成不存在的候选人或错误政策。
   - 需要记录并分析模型稳定性。

### 写作重点

这一章要强调“社会传播不只是转述事实”。更重要的是：

- 不同角色带着不同身份理解同一事件。
- 同一信息进入不同人的记忆后，会产生不同态度。
- 传播过程可能发生变形。

### 可选扩展

1. 强化汤姆反对山姆的设定。
2. 给山姆增加明确竞选纲领。
3. 给拉托亚增加“采访候选人”的目标。
4. 统计不同模型下选举传播是否更稳定。

---

## 实验 3：关系形成与记忆影响

### 对应章节

第 26 章：设计自己的小镇事件。

也可在第 29 章中作为“可信行为评价”的案例。

### 对应论文现象

论文强调，智能体的关系不是只靠固定人设，而会通过对话、共同经历和反思形成。Klaus 与 Maria 的例子说明 reflection 能影响后续社交选择。

### 当前项目可用角色

- 克劳斯：社会学学生，研究低收入社区中产阶级化。
- 玛丽亚：物理专业学生，兼职 Twitch 游戏主播，喜欢建立联系和探索新想法。

关键文件：

- `generative_agents/frontend/static/assets/village/agents/克劳斯/agent.json`
- `generative_agents/frontend/static/assets/village/agents/玛丽亚/agent.json`

### 实验目标

观察两个角色是否会因为共同场所、共同话题或多次对话形成更稳定的关系记忆。

### 推荐参与角色

最小关系实验：

- 克劳斯
- 玛丽亚

加入干扰角色：

- 克劳斯
- 玛丽亚
- 阿伊莎
- 沃尔夫冈
- 伊莎贝拉

选择理由：

- 克劳斯和玛丽亚都与奥克山学院、霍布斯咖啡馆有关。
- 加入其他学生可以观察克劳斯是否仍然偏向与玛丽亚形成关系。
- 伊莎贝拉提供公共场所和派对事件。

### 推荐运行命令

```bash
cd generative_agents
python start.py --name book-relationship-small --start "20240213-08:00" --step 72 --stride 10 --agents "克劳斯,玛丽亚"
python compress.py --name book-relationship-small
```

扩展版：

```bash
cd generative_agents
python start.py --name book-relationship-full --start "20240213-08:00" --step 96 --stride 10 --agents "克劳斯,玛丽亚,阿伊莎,沃尔夫冈,伊莎贝拉"
python compress.py --name book-relationship-full
```

### 观察问题

1. 克劳斯和玛丽亚是否相遇？
2. 第一次对话内容是什么？
3. 后续是否引用前一次对话？
4. 是否出现对彼此兴趣、专业、计划的记忆？
5. 是否出现“以后再聊”“一起参加活动”“互相帮助”等未来计划？
6. 如果加入其他角色，关系是否被稀释？

### 关键词

```text
克劳斯
玛丽亚
社会学
物理
Twitch
游戏
研究
论文
咖啡馆
一起
下次
有机会
```

### 指标设计

- `pair_chat_count`
  - 克劳斯与玛丽亚对话次数。
- `memory_reference_count`
  - 后续对话中提到过去对话内容的次数。
- `future_plan_count`
  - 形成未来共同计划的次数。
- `relationship_summary_quality`
  - `summarize_relation` 结果是否体现双方共同兴趣。
- `competing_interaction_count`
  - 克劳斯或玛丽亚与其他角色互动次数。

### 证据收集方式

1. 在 `conversation.json` 中筛选克劳斯和玛丽亚对话。
2. 在 checkpoint 中查看双方 `associate.chat` 和 `associate.thought`。
3. 搜索 `simulation.md` 是否出现重复提及过去对话。
4. 对比加入干扰角色前后的互动频率。

### 成功标准

基础成功：

- 克劳斯和玛丽亚至少发生一次对话。

中等成功：

- 第二次对话引用第一次对话内容。
- 任一方形成关于对方的记忆摘要。

高级成功：

- 出现共同计划或后续邀约。
- 在有其他角色干扰时仍表现出关系连续性。

### 失败模式

1. 两人全天没有相遇。
2. 对话每次都像第一次见面。
3. 关系摘要过于泛泛。
4. 角色专业和兴趣没有进入对话。
5. 出现不符合人设的亲密关系突变。

### 写作重点

关系形成实验要强调：

- 关系不是写死字段。
- 关系来自对话、记忆检索和反思。
- 如果后续对话无法引用过去，就说明长期行为连续性不足。

---

## 实验 4：反思机制消融

### 对应章节

第 29 章：如何评价一个智能体是否“可信”。

也可作为源码扩展实验放在第 33 章，如果后续增加章节。

### 对应论文现象

论文通过 ablation study 比较完整架构、无反思、无反思和计划等条件，证明 reflection 和 planning 对可信行为有贡献。

### 当前项目切入点

核心函数：

- `generative_agents/modules/agent.py`
  - `Agent.reflect()`
- `generative_agents/modules/agent.py`
  - `Agent.think()`

关键配置：

- `generative_agents/data/config.json`
  - `think.poignancy_max`

### 实验目标

比较开启反思和抑制反思时，角色记忆、关系、计划连续性是否出现差异。

### 消融方式

#### 方式 A：提高反思阈值

将 `think.poignancy_max` 设置为极高值，例如：

```json
"poignancy_max": 999999
```

效果：

- 基本不触发反思。
- 不需要改源码。
- 适合教学实验。

#### 方式 B：临时跳过 `reflect()`

在 `Agent.think()` 中临时注释或跳过：

```python
self.reflect()
```

效果：

- 直接关闭反思。
- 需要改源码。
- 适合源码实验，但要提醒读者保留备份或用分支。

#### 方式 C：只关闭对话反思

修改 `Agent.reflect()` 中处理 `self.chats` 的部分。

效果：

- 可观察对话是否仍能转化为长期计划和记忆。
- 适合高级实验。

### 推荐实验对象

关系形成消融：

- 克劳斯
- 玛丽亚
- 阿伊莎
- 伊莎贝拉

信息传播消融：

- 伊莎贝拉
- 亚当
- 阿伊莎
- 埃迪

选举消融：

- 山姆
- 汤姆
- 约翰
- 拉托亚
- 乔治

### 推荐运行命令

完整架构：

```bash
cd generative_agents
python start.py --name book-reflection-on --start "20240213-08:00" --step 96 --stride 10 --agents "克劳斯,玛丽亚,阿伊莎,伊莎贝拉"
python compress.py --name book-reflection-on
```

关闭反思后：

```bash
cd generative_agents
python start.py --name book-reflection-off --start "20240213-08:00" --step 96 --stride 10 --agents "克劳斯,玛丽亚,阿伊莎,伊莎贝拉"
python compress.py --name book-reflection-off
```

### 对比指标

- `thought_count`
  - 每个角色 thought 记忆数量。
- `chat_reference_count`
  - 后续对话引用过去对话次数。
- `plan_change_count`
  - 因对话或事件导致活动描述变化的次数。
- `relationship_continuity_score`
  - 后续对话是否体现双方认识加深。
- `repeated_intro_count`
  - 多次对话是否反复自我介绍或重复寒暄。

### 证据收集方式

1. 比较两个实验的 `simulation.md`。
2. 比较 checkpoint 中 `associate.thought` 数量。
3. 搜索是否出现“上次”“刚才”“之前提到”等记忆引用。
4. 统计同一对角色对话是否更连续。

### 成功标准

完整架构应表现出：

- 更多 thought 记忆。
- 更少“重新认识”式对话。
- 更多后续计划引用。
- 更稳定的关系描述。

关闭反思可能表现出：

- 对话更碎片化。
- 关系进展变弱。
- 行动更依赖即时日程。

### 失败模式

1. 关闭反思后差异不明显。
   - 可能是实验时间太短。
   - 可能是参与角色太少。
   - 可能是对话次数不足。
2. 完整架构也没有形成 thought。
   - 可能是 `poignancy_max` 太高。
3. 小模型输出不稳定导致差异被噪声覆盖。

### 写作重点

这一章要告诉读者：评价一个 Agent 系统，不能只看一段好玩的对话。要通过消融比较证明某个模块是否真的带来行为差异。

---

## 实验 5：设计自己的小镇事件

### 对应章节

第 26 章：设计自己的小镇事件。

### 实验目标

教读者从零设计一个可传播、可观察、可评价的小镇事件。

### 事件设计模板

一个好事件至少包含：

1. 发起人。
2. 明确目标。
3. 明确时间。
4. 明确地点。
5. 需要通过社交传播。
6. 可能有接受、拒绝、遗忘、误传等结果。
7. 能在 `simulation.md` 和 `movement.json` 中观测。

### 示例事件 A：校园讲座

发起人：

- 克劳斯

事件：

```text
克劳斯计划在2月13日下午4点于奥克山学院图书馆组织一次关于低收入社区中产阶级化影响的小型讨论会。他正在邀请对社会议题感兴趣的同学和居民参加。
```

目标角色：

- 阿伊莎
- 玛丽亚
- 沃尔夫冈
- 梅

观察指标：

- 有多少人知道讲座。
- 有多少人表示参加。
- 是否在 16:00 聚集到图书馆。
- 对话是否围绕社会议题展开。

### 示例事件 B：艺术展筹备

发起人：

- 詹妮弗

事件：

```text
詹妮弗计划在2月13日下午3点于约翰逊公园展示几幅新的水彩画，并希望邀请年轻艺术家给她反馈。
```

目标角色：

- 拉吉夫
- 拉托亚
- 海莉
- 阿比盖尔

观察指标：

- 艺术相关角色是否被吸引。
- 是否出现作品反馈。
- 是否形成后续合作计划。

### 示例事件 C：药店健康咨询

发起人：

- 约翰

事件：

```text
约翰计划在2月13日上午11点于柳树市场和药店举行一次免费的用药咨询，希望提醒居民关注常见药品的正确使用方式。
```

目标角色：

- 梅
- 汤姆
- 简
- 山姆
- 詹妮弗

观察指标：

- 是否产生公共服务话题。
- 是否被汤姆联系到商店经营。
- 是否被山姆联系到竞选政策。

### 修改方式

优先修改角色的 `currently` 字段，不直接改代码。

示例：

```text
generative_agents/frontend/static/assets/village/agents/克劳斯/agent.json
```

将 `currently` 改成包含事件目标、时间、地点和邀请意图的描述。

### 推荐运行命令

```bash
cd generative_agents
python start.py --name book-custom-event --start "20240213-08:00" --step 72 --stride 10 --agents "克劳斯,阿伊莎,玛丽亚,沃尔夫冈,梅"
python compress.py --name book-custom-event
```

### 写作重点

这章要教读者“设计事件”的方法，而不是只给一个例子。要强调：

- 事件描述要明确。
- 时间地点要与地图存在的地址一致。
- 目标角色要有接触机会。
- 事件应该能通过对话传播。
- 结果要能被数据验证。

---

## 实验 6：增加新角色、新地点、新关系

### 对应章节

第 27 章：增加新角色、新地点、新关系。

### 实验目标

让读者理解项目如何扩展，而不是只运行内置小镇。

### 新角色设计原则

一个新角色至少需要：

1. `name`
2. `portrait`
3. `coord`
4. `currently`
5. `scratch`
   - `age`
   - `innate`
   - `learned`
   - `lifestyle`
   - `daily_plan`
6. `spatial`
   - `address.living_area`
   - `tree`

### 新关系设计原则

关系不要单独做成一张关系表，而应写入角色自然语言设定中：

- “A 是 B 的朋友。”
- “A 不喜欢 B。”
- “A 正在和 B 合作。”
- “A 想邀请 B 参加某活动。”
- “A 上次和 B 讨论过某事。”

这些关系应进入：

- `currently`
- `scratch.learned`
- `scratch.daily_plan`

### 新地点设计原则

新地点比新角色复杂，因为需要同时考虑：

1. 后端 `maze.json`。
2. 前端 `tilemap.json`。
3. tile 图片资源。
4. 地址层级。
5. collision。
6. 角色 `spatial.tree` 是否知道该地点。

教学阶段建议先不新增真实地图图块，而是在现有地点上设计新事件。

### 推荐扩展顺序

1. 先新增关系。
   - 改 `currently` 和 `learned`。
2. 再新增事件。
   - 改 `currently`。
3. 再新增角色。
   - 复制一个相近角色目录。
4. 最后再尝试新增地图地点。
   - 需要单独章节或附录。

### 新角色示例

角色：林晓

设定：

```text
林晓是奥克山学院的访问学者，研究人工智能如何影响社区生活。她计划在下午3点到霍布斯咖啡馆采访居民，了解他们对市长选举和社区活动的看法。
```

实验目标：

- 看她能否触发选举和派对两条信息线。
- 看不同居民是否给出不同观点。

### 写作重点

这章要强调扩展项目的难度层级。新增角色相对容易，新增地图地点最难。不要让读者一上来就改地图，否则容易被资源和坐标问题卡住。

---

## 实验 7：用中文本地模型重跑论文思想

### 对应章节

第 28 章：用中文本地模型重跑论文思想。

### 实验目标

比较本地中文模型在生成式智能体任务上的表现，包括成本、速度、稳定性、对话自然度和结构化输出可靠性。

### 当前项目支持

LLM provider：

- Ollama
- OpenAI-compatible
- MiniMax

Embedding provider：

- Ollama
- OpenAI
- HuggingFace

核心配置文件：

- `generative_agents/data/config.json`

### 推荐模型维度

不在正文里承诺某个模型一定最佳，而是设计比较维度：

1. 小模型快速实验。
2. 中等模型平衡速度与质量。
3. 推理模型用于反思和复杂计划。
4. 云端模型作为质量上限参考。

### 比较指标

- `runtime_per_step`
  - 每个仿真 step 平均耗时。
- `llm_failure_count`
  - LLM 调用失败次数。
- `structured_parse_failure_count`
  - JSON / Pydantic 解析失败次数。
- `conversation_naturalness`
  - 对话自然度人工评分。
- `plan_consistency`
  - 日程是否重复、冲突或不符合人设。
- `memory_usage_quality`
  - 后续是否引用过去记忆。

### 推荐实验

固定实验场景：

- 情人节派对小实验。

固定角色：

- 伊莎贝拉
- 亚当
- 阿伊莎
- 埃迪
- 玛丽亚
- 克劳斯

固定命令：

```bash
cd generative_agents
python start.py --name book-model-test-<model> --start "20240214-08:00" --step 36 --stride 10 --agents "伊莎贝拉,亚当,阿伊莎,埃迪,玛丽亚,克劳斯" --log run.log
python compress.py --name book-model-test-<model>
```

### 证据收集

- `results/checkpoints/<name>/run.log`
- `simulation.md`
- `conversation.json`
- LLM summary 信息。

### 写作重点

这一章要讲清楚：本地模型不是免费替代云端模型。它带来低成本、可控和隐私优势，但也带来速度、格式稳定性和推理质量挑战。

---

## 实验 8：可信行为评价框架

### 对应章节

第 29 章：如何评价一个智能体是否“可信”。

### 实验目标

将论文中的 controlled evaluation 和 end-to-end evaluation 转化为适合本项目的教学版评价框架。

### 评价维度

#### 1. 身份一致性

问题：

- 角色行为是否符合年龄、职业、性格、生活习惯？

证据：

- `simulation.md` 活动描述。
- 对话内容。

示例指标：

- `persona_consistency_score`

#### 2. 记忆连续性

问题：

- 角色是否记得过去对话或事件？

证据：

- 后续对话是否引用过去内容。
- checkpoint 中 `associate.chat` 和 `associate.thought`。

示例指标：

- `memory_reference_count`
- `repeated_intro_count`

#### 3. 计划合理性

问题：

- 日程是否符合生活习惯？
- 是否出现重复吃饭、深夜乱逛、工作地点错误等问题？

证据：

- checkpoint 中 `schedule`。
- `simulation.md` 活动时间线。

示例指标：

- `schedule_conflict_count`
- `sleep_consistency_score`

#### 5. 反应合理性

问题：

- 遇到他人或对象占用时是否合理反应？
- 是否该聊天时聊天，不该聊天时不聊？

证据：

- `conversation.json`
- `Agent._wait_other()` 相关行动记录。

示例指标：

- `appropriate_reaction_count`
- `unnecessary_chat_count`

#### 6. 社会传播

问题：

- 信息是否通过对话传播？
- 传播是否保持核心事实？

证据：

- `conversation.json`
- `simulation.md`

示例指标：

- `diffusion_depth`
- `fact_preservation_score`

#### 7. 行动落地

问题：

- 角色是否真的到达目标地点？
- 口头承诺是否转化为行动？

证据：

- `movement.json`
- `simulation.md`

示例指标：

- `promise_action_match_rate`
- `target_arrival_rate`

### 评分建议

采用 1-5 分人工评分更适合教学：

1. 完全不可信。
2. 大量问题。
3. 基本合理但有明显瑕疵。
4. 大部分可信。
5. 非常可信。

每个评分必须附证据行：

- 时间。
- 角色。
- 对话或行动片段。
- 判断理由。

### 写作重点

本章要防止读者用“有趣”替代“可信”。一个故事可以有趣，但行为可能不连续、不一致、不符合人设。评价框架就是为了区分这两件事。

---

## 实验 9：风险、伦理与边界观察

### 对应章节

第 30 章：风险、伦理与边界。

### 实验目标

让读者理解生成式智能体系统的局限，不把仿真结果误认为真实社会预测。

### 需要观察的风险

#### 1. 拟人化风险

表现：

- 读者容易把智能体行为误解为真实心理。

写作处理：

- 明确说明 believable behavior 不等于意识。

#### 2. 社会仿真误用

表现：

- 用小镇结果预测真实选举、消费、舆论。

写作处理：

- 强调这是实验装置，不是现实预测器。

#### 3. 记忆幻觉

表现：

- 角色提到从未发生过的对话。
- 角色误记时间、地点或人物。

证据：

- 对照 `conversation.json` 和 `simulation.md`。

#### 5. 过度合作

表现：

- 角色总是接受邀请。
- 反对关系没有体现。

证据：

- 汤姆和山姆相关对话。
- 派对邀请接受率。

#### 6. 价值偏差

表现：

- 对职业、年龄、关系、政治话题给出刻板表达。

证据：

- 对话文本。

### 写作重点

风险章节不是最后随便提醒几句，而要和前面实验连接：

- 情人节派对看过度合作。
- 镇长竞选看政治话题偏差。
- 关系形成看拟人化风险。
- 反思消融看记忆幻觉。

---

## 第四部分写作顺序

建议正文按以下顺序推进：

1. 先做情人节派对。
   - 最贴近论文，也最容易让读者看到“传播”。
2. 再做镇长竞选。
   - 展示信息传播中的态度分化。
3. 再做关系形成。
   - 进入更细腻的长期记忆问题。
4. 再做反思消融。
   - 从现象回到架构验证。
5. 再教读者设计自定义事件。
   - 让读者具备迁移能力。
6. 再讲新角色、新地点、新关系。
   - 进入项目扩展。
7. 再讲中文本地模型比较。
   - 让实验可落地到低成本环境。
8. 最后讲可信评价与风险边界。
   - 收束到方法论。

## 第四部分必须产出的表格

### 表 1：实验配置表

字段：

- 实验名
- 起始时间
- step
- stride
- 角色列表
- 目标现象
- 输出材料

### 表 2：传播路径表

字段：

- 时间
- 说话者
- 接收者
- 传播内容
- 是否保持事实
- 证据位置

### 表 3：到场行为表

字段：

- 角色
- 是否被邀请
- 是否接受
- 是否到场
- 到场时间
- 到场地点
- 证据位置

### 表 4：消融对比表

字段：

- 实验条件
- thought 数量
- 对话引用次数
- 重复寒暄次数
- 计划变更次数
- 人工评分

### 表 5：可信行为评分表

字段：

- 评价维度
- 分数
- 证据
- 问题
- 改进方向

---

## 需要后续实现的辅助脚本

当前文件只做实验设计。进入正文或真正跑实验时，可以考虑增加辅助脚本，但本阶段不直接修改项目源码。

建议脚本：

1. `tools/analyze_conversation_keywords.py`
   - 输入 `conversation.json`。
   - 输出关键词出现次数和角色传播路径。
2. `tools/analyze_attendance.py`
   - 输入 `movement.json`。
   - 输出指定时间窗内指定地点的角色列表。
3. `tools/compare_reflection_runs.py`
   - 输入两个实验 checkpoint 目录。
   - 对比 thought 数量、chat 数量、schedule 变化。
4. `tools/export_experiment_report.py`
   - 汇总实验指标为 Markdown 表格。

是否真正加入这些脚本，后面写第四部分正文时再决定。

## 第四部分结束时读者应掌握

读完第四部分，读者应该能：

1. 复现论文中的派对传播和选举传播现象。
2. 设计自己的小镇事件。
3. 修改角色设定并预测行为变化。
4. 用数据而不是感觉评价智能体行为。
5. 理解 reflection、memory、planning 对可信行为的贡献。
6. 知道本项目在哪些地方仍然有限。
7. 为第五部分“前沿演进与项目升级”准备好基线实验。

## 后续衔接

本文件完成后，下一个应落盘的文档是：

- `05_frontier_upgrade_notes.md`

该文件用于记录 2023-2026 年生成式智能体领域前沿演进，并明确如何基于 Generative Agents 做升级实现。它只作为第五部分追加，不改动前四部分主结构。
