# 第 13 章 改配置，自定义智能体实验

本章节，我们来尝试只改配置，构造一个新的小场景，观察 Generative Agents 如何从角色设定、空间位置和日程目标中生成新的行为。

场景的实验名为 `book-config-ai-seminar`。已有角色“阿伊莎”和“克劳斯”在这里作为两个配置槽位，临时改成一场“生成式智能体校园应用讨论会”的参与者。整个过程只触碰两个 `agent.json`，不修改 `start.py`、不新增 prompt、不改地图、不改智能体逻辑。

## 13.1 配置迁移

配置层负责定义角色、地点、目标和日程，具体配置和实验的过程如 图13-1 所示。

```mermaid
flowchart LR
    Boundary["确认配置边界"] --> Scene["设计新场景"]
    Scene --> AgentJson["修改两个 agent.json"]
    AgentJson --> Run["运行 2 step 短实验"]
    Run --> Log["解读控制台输出"]
    Log --> Conversation["阅读 conversation.json"]
    Conversation --> Compressed["压缩并阅读 simulation.md / movement.json"]
    Compressed --> Transfer["迁移到自己的场景"]
```

*图 13-1：配置迁移路线，先确认配置边界，再用短实验验证配置是否真的改变了智能体行为。*


## 13.2 配置层能做什么，不能做什么

先看启动脚本里和角色选择有关的代表性代码：

```python
personas = [
    "阿伊莎", "克劳斯", "玛丽亚", "沃尔夫冈",
    # ...
]

if args.agents:
    selected_personas = [a.strip() for a in args.agents.split(",") if a.strip()]
    unknown_agents = [a for a in selected_personas if a not in personas]
    if unknown_agents:
        raise ValueError("Unknown agents: " + ", ".join(unknown_agents))
```

这段代码划定角色选择边界。`--agents` 不能随便传入两个全新的名字，例如 `张三`、`李四`，因为 `start.py` 会先检查角色是否在 `personas` 列表中。新增角色名、头像、注册逻辑和资源路径，需要进入源码和资源注册流程。

尽管如此，配置层仍然可以做很多事。`--agents "阿伊莎,克劳斯"` 选择的是两个已注册角色槽位，而角色的背景、目标、生活习惯、当天计划和初始坐标都来自各自的 `agent.json`。只改这两个 JSON，就能把默认人物临时改造成新的实验人物。

角色名仍然使用“阿伊莎”和“克劳斯”，角色设定改成“生成式智能体校园应用讨论会”的两位参与者。配置迁移的完整链路由此展开。

## 13.3 先设计场景，再写 JSON

写配置之前，把实验设计成一句清楚的话：

> 2024 年 2 月 13 日上午 10 点，阿伊莎和克劳斯在奥克山学院图书馆桌子旁讨论生成式智能体在校园治理和学习公平中的应用。

这句话拆开就是配置目标。

| 设计项 | 实验取值 | 对应配置或命令 |
| --- | --- | --- |
| 实验名 | `book-config-ai-seminar` | `--name book-config-ai-seminar` |
| 时间 | `20240213-10:00` | `--start "20240213-10:00"` |
| 参与者 | 阿伊莎、克劳斯 | `--agents "阿伊莎,克劳斯"` |
| 地点 | 奥克山学院，图书馆，图书馆桌子 | `coord: [119, 24]` |
| 阿伊莎的任务 | 主持生成式智能体校园应用讨论 | `currently`、`scratch.daily_plan` |
| 克劳斯的任务 | 从社会影响和公平性角度提问 | `currently`、`scratch.daily_plan` |
| 运行长度 | 2 个 step，每步 10 分钟 | `--step 2 --stride 10` |

场景设计要落在项目已有地图上。地点复用原地图里的奥克山学院图书馆，角色复用已注册的阿伊莎和克劳斯。配置迁移先在现有边界内做出一个新的可信情境。

`coord: [119, 24]` 不是从地图截图里猜出来的，而是从地图配置文件反查出来的。地图文件在：

```text
generative_agents/frontend/static/assets/village/maze.json
```

`maze.json` 里的每个 tile 都记录了自己的 `coord` 和 `address`。`coord` 是地图网格坐标，格式是 `[x, y]`，`x` 从左向右增加，`y` 从上向下增加；前端渲染时再按 32 像素的 tile 宽度换算成屏幕位置。`address` 是空间地址，在这个项目里按“区域、房间、对象”组织。例如“奥克山学院，图书馆，图书馆桌子”对应的地址数组是：

```json
["奥克山学院", "图书馆", "图书馆桌子"]
```

进入 `generative_agents` 目录后，可以用下面这条命令反查这个地址下有哪些可用坐标：

```bash
python -c "import json; m=json.load(open('frontend/static/assets/village/maze.json', encoding='utf-8')); print([t['coord'] for t in m['tiles'] if t.get('address') == ['奥克山学院','图书馆','图书馆桌子'] and not t.get('collision')])"
```

输出如下：

```text
[[119, 22], [122, 22], [119, 24], [122, 24]]
```

同一个地点可能覆盖多个 tile。这里的四个坐标都属于“图书馆桌子”，并且都不是碰撞格子，角色可以站在上面。本章选择 `[119, 24]`，是因为它已经能把两个角色放到同一个可交互地点。换成 `[119, 22]`、`[122, 22]` 或 `[122, 24]`，也仍然会落在同一张图书馆桌子附近。

还可以反向验证某个坐标的地址：

```bash
python -c "import json; m=json.load(open('frontend/static/assets/village/maze.json', encoding='utf-8')); print([t for t in m['tiles'] if t.get('coord') == [119, 24]][0])"
```

输出结果会显示：

```text
{'coord': [119, 24], 'address': ['奥克山学院', '图书馆', '图书馆桌子']}
```

控制台日志里的 `coord[119,24]: the Ville:奥克山学院:图书馆:图书馆桌子`，就是这条地图配置在运行时被后端读入后的结果。以后换场景时，先用 `maze.json` 找到目标地点的坐标，再把其中一个非碰撞坐标填进角色的 `agent.json`。

## 13.4 备份并修改阿伊莎的配置

要改的文件是：

```text
generative_agents/frontend/static/assets/village/agents/阿伊莎/agent.json
generative_agents/frontend/static/assets/village/agents/克劳斯/agent.json
```

动手前先备份。下面是 PowerShell 写法；macOS 或 Linux 下用 `cp` 做同样的文件复制即可。

```powershell
cd generative_agents
Copy-Item "frontend/static/assets/village/agents/阿伊莎/agent.json" "$env:TEMP/aisha.agent.json"
Copy-Item "frontend/static/assets/village/agents/克劳斯/agent.json" "$env:TEMP/klaus.agent.json"
```

阿伊莎的配置不需要整份重写。保留 `name`、`portrait`、`spatial` 等字段，只改三个关键部分：初始坐标、当前状态、角色心理草稿。

```json
{
  "name": "阿伊莎",
  "coord": [119, 24],
  "currently": "阿伊莎正在组织一次关于生成式智能体如何服务校园学习的讨论会。她约了克劳斯上午10点在奥克山学院图书馆桌子旁交流，准备把文学研究中的细读方法和智能体行为观察结合起来。",
  "scratch": {
    "age": 20,
    "innate": "好奇、善于提问、组织力强",
    "learned": "阿伊莎是奥克山学院一名关注生成式智能体的学生。她熟悉文学细读，也在尝试把这种方法用于观察智能体的行动、对话和记忆。",
    "lifestyle": "阿伊莎晚上10点左右上床睡觉，早上6点左右醒来，上午通常在图书馆整理阅读材料，下午参加课程或讨论。",
    "daily_plan": "阿伊莎上午10点在奥克山学院图书馆桌子旁主持一场关于生成式智能体校园应用的讨论，并请克劳斯从社会影响角度提出问题。"
  }
}
```

这段配置有四个观察点。

`coord` 把阿伊莎放到图书馆桌子旁。它不是自然语言设定，而是前端地图坐标；如果坐标和目标地点不一致，角色会在回放里出现在错误的位置。

`currently` 是角色的当前大目标。它告诉模型阿伊莎此刻关心什么，也把克劳斯和上午 10 点的会面写进上下文。

`scratch.learned` 给角色长期背景。这里把阿伊莎从“莎士比亚语言研究”改成“关注生成式智能体的学生”，但仍保留“细读”这个能力，让新设定和旧人物气质之间有连续性。

`scratch.daily_plan` 把目标压进当天日程。只有 `currently` 还不够，日程字段会影响模型如何拆解一天的计划，也会影响 10 点这一段是否真的围绕讨论会展开。

## 13.5 修改克劳斯的配置

克劳斯的配置要和阿伊莎形成互补。阿伊莎负责“细读智能体行为”，克劳斯负责“社会影响和公平性问题”。两个角色必须有共同主题，也必须有视角差异，否则对话容易变成互相复述。

```json
{
  "name": "克劳斯",
  "coord": [119, 24],
  "currently": "克劳斯正在准备一场关于生成式智能体社会影响的讨论。他约了阿伊莎上午10点在奥克山学院图书馆桌子旁交流，重点关注智能体系统在校园和社区治理中的公平性问题。",
  "scratch": {
    "age": 20,
    "innate": "善于倾听、关心公平、乐于辩论",
    "learned": "克劳斯是奥克山学院社会学专业的学生。他关注技术如何改变社区关系，尤其关心生成式智能体系统是否会放大或缓解不平等。",
    "lifestyle": "克劳斯晚上11点左右睡觉，早上7点左右醒来，上午常在图书馆写作，下午会和同学讨论社会议题。",
    "daily_plan": "克劳斯上午10点在奥克山学院图书馆桌子旁与阿伊莎讨论生成式智能体的校园应用，并记录其中的社会影响问题。"
  }
}
```

这段配置的重点是“互相指向”。阿伊莎的 `currently` 里写到克劳斯，克劳斯的 `currently` 里写到阿伊莎；两人的 `daily_plan` 都把上午 10 点、奥克山学院图书馆、生成式智能体讨论写清楚。多智能体仿真不是把两个孤立人设扔到地图上，而是要给他们一个足够明确的共同场域。

保存完两个 JSON 后，就可以启动自定义实验了。

## 13.6 运行配置实验

进入项目运行目录：

```bash
cd generative_agents
```

启动一个 2 step 的短实验：

```bash
python start.py --name book-config-ai-seminar --start "20240213-10:00" --step 2 --stride 10 --agents "阿伊莎,克劳斯" --verbose info --log book-config-ai-seminar.log
```

这条命令的含义很直接：从 2024 年 2 月 13 日 10:00 开始，只运行阿伊莎和克劳斯两个角色，连续推进两次，每次推进 10 分钟，并把日志写到 checkpoint 目录下。

如果控制台偶尔出现下面这样的模型调用重试信息，不要立刻判定实验失败：

```text
LLMModel.completion() caused an error: not enough values to unpack (expected 3, got 1)
```

先继续看后续是否进入 `Simulate Step`，是否写出 `simulate-*.json`，以及日志摘要里是否出现 `F:0`。本次实验出现过短暂重试，但随后正常完成两个 step，并生成了 checkpoint 和 compressed 结果。

## 13.7 控制台输出不是普通日志

启动仿真后，控制台会先打印两个角色的 reset 摘要。下面是阿伊莎的代表性片段：

```text
----------                         阿伊莎.reset                          ----------
name: 阿伊莎
currently: 阿伊莎正在组织一次关于生成式智能体如何服务校园学习的讨论会。她约了克劳斯上午10点在奥克山学院图书馆桌子旁交流，准备把文学研究中的细读方法和智能体行为观察结合起来。
tile:
  coord[119,24]: the Ville:奥克山学院:图书馆:图书馆桌子
  events:
    e_0: 图书馆桌子 此时 空闲 @ the Ville:奥克山学院:图书馆:图书馆桌子
    e_1: 阿伊莎 此时 空闲 @ the Ville:奥克山学院:图书馆:图书馆桌子
    e_2: 克劳斯 此时 空闲 @ the Ville:奥克山学院:图书馆:图书馆桌子
```

这段输出先验证配置有没有被加载。`currently` 已经是新场景，`coord[119,24]` 已经落到图书馆桌子，`events` 里同时出现阿伊莎和克劳斯，说明两个角色被放在了同一个可交互空间。读控制台时先看这些硬证据，再看模型生成了什么。

接着进入第一个仿真步：

```text
==========       Simulate Step[1/2, time: 2024-02-13 10:00:00]        ==========
阿伊莎 is making schedule...
阿伊莎 -> wake_up
阿伊莎 -> schedule_init
阿伊莎 -> schedule_daily
阿伊莎 -> schedule_decompose
阿伊莎 percept 0/4 concepts
阿伊莎 is determining action...
阿伊莎 -> determine_sector
阿伊莎 -> determine_arena
阿伊莎 -> determine_object
阿伊莎 -> describe_object
```

这不是普通流水日志。它把一个智能体在一个 step 内经历的关键阶段按顺序摊开：先生成当天日程，再感知周围概念，然后决定行动地点、场所、对象，最后描述对象状态。配置正是在这条推理链条中变成具体行为。

阿伊莎的日程摘要中出现了新设定：

```text
10:00~11:00: 在奥克山学院图书馆桌子旁主持生成式智能体校园应用讨论会，与克劳斯从社会影响角度交流:
  10:00~10:05: 整理桌面资料，迎接克劳斯入座
  10:05~10:10: 介绍本次讨论的主题和议程
  10:10~10:20: 分享上午整理的关于生成式智能体的阅读笔记
  10:20~10:30: 讨论如何将文学细读方法应用于智能体行为观察
```

这段输出说明 `scratch.daily_plan` 没有停留在静态文本里。模型把“一场讨论会”拆成了可执行的小时间片：整理桌面、介绍议程、分享阅读笔记、讨论细读方法。配置写得越具体，日程拆解越容易落到可观察动作上。

克劳斯的输出继续推进到对话决策：

```text
克劳斯 percept 0/4 concepts
克劳斯 -> decide_chat
克劳斯 decides chat with 阿伊莎
克劳斯 -> summarize_relation
阿伊莎 -> summarize_relation
克劳斯 -> generate_chat
阿伊莎 -> generate_chat
...
克劳斯 and 阿伊莎 has chats
```

这段输出证明了配置的作用。两个角色不是因为配置里预写了对话才聊天，而是在同一地点、共同日程和互相指向的目标共同作用下触发了 `decide_chat`，随后进入 `generate_chat`。配置改变仿真的前提，智能体系统把前提展开成行为。

每个角色摘要末尾还有 LLM 调用统计：

```text
llm:
  model: MiniMax-M3
  summary:
    total: S:27,F:0/R:28
    llm_normal: S:27,F:0/R:28
```

`S` 表示成功次数，`F` 表示失败次数，`R` 表示请求次数。`F:0` 加上结果文件生成，说明这次主链路已经跑通。短实验不追求输出多，而是追求证据链清楚。

## 13.8 读取 conversation.json

运行完成后，先看原始对话文件：

```text
generative_agents/results/checkpoints/book-config-ai-seminar/conversation.json
```

代表性片段如下：

```json
{
  "20240213-10:00": [
    {
      "克劳斯 -> 阿伊莎 @ the Ville，奥克山学院，图书馆，图书馆桌子": [
        [
          "克劳斯",
          "早上好，阿伊莎，谢谢你帮我整理资料。今天我们重点聊聊生成式智能体在校园治理中可能放大的公平性问题吧，我昨晚想到几个案例想和你探讨一下。"
        ],
        [
          "阿伊莎",
          "早上好，克劳斯！校园治理中的公平性问题确实是个很好的切入点。我昨晚也在想，如果我们用细读的方式来拆解智能体的决策路径，也许能更清楚地看到哪些环节容易出现偏差。你先说说你想到的那几个案例吧？"
        ]
      ]
    }
  ]
}
```

这段对话同时命中了两个人设。克劳斯从“公平性问题”切入，阿伊莎用“细读方式拆解决策路径”回应。两个关键词都来自配置：克劳斯的 `learned` 和 `daily_plan` 里有社会影响、公平性；阿伊莎的 `learned` 和 `currently` 里有文学细读、智能体行为观察。

对话继续向具体案例展开：

```text
克劳斯：第一个案例是关于智能体辅助的奖学金分配——系统会基于学生的历史数据和参与度评分，但来自资源较少社区的学生可能因为课外活动少而被低估。

阿伊莎：系统里的"参与度评分"具体是怎么定义的？是从学校活动报名数据里提取，还是也包含了课外的志愿服务这类信息？
```

这里已经不是“两个角色寒暄”。模型把配置中的校园应用、公平性、细读方法组合成了一个可讨论的问题：奖学金分配中的参与度评分。评估自定义智能体时，`conversation.json` 是最干净的证据之一，因为它能直接看到角色是否按设定交换信息。

## 13.9 压缩结果并阅读 simulation.md

确认 checkpoint 正常后，再生成前端回放和人工阅读材料：

```bash
python compress.py --name book-config-ai-seminar
```

命令完成后会生成：

```text
generative_agents/results/compressed/book-config-ai-seminar/movement.json
generative_agents/results/compressed/book-config-ai-seminar/simulation.md
```

有了 `movement.json`，回放页面就能把这次配置实验画出来。如果回放服务已经启动，可以打开：

```text
http://127.0.0.1:5000/?name=book-config-ai-seminar&step=1&speed=0&zoom=0.75
```

![图 13-2：book-config-ai-seminar 的回放画面](../../assets/chapter_13/fig-13-2-config-ai-seminar-replay.png)

*图 13-2：`book-config-ai-seminar` 的回放画面。左上方是 10:00 的对话记录，地点显示为“the Ville，奥克山学院，图书馆，图书馆桌子”；下方地图能看到两个角色落在图书馆区域。配置里的角色目标、坐标和对话，已经被压缩结果还原成可视化画面。*

`simulation.md` 前面会列出基础人设。它可能包含项目中的全量角色档案，不要被这部分带偏。本次真正参与仿真的角色，要看后面的活动记录、对话记录，以及 `movement.json` 里的角色列表。

本次 `simulation.md` 中，阿伊莎的人设已经变成新实验设定：

```markdown
## 阿伊莎

年龄：20岁
先天：好奇、善于提问、组织力强
后天：阿伊莎是奥克山学院一名关注生成式智能体的学生。她熟悉文学细读，也在尝试把这种方法用于观察智能体的行动、对话和记忆。
生活习惯：阿伊莎晚上10点左右上床睡觉，早上6点左右醒来，上午通常在图书馆整理阅读材料，下午参加课程或讨论。
当前状态：阿伊莎正在组织一次关于生成式智能体如何服务校园学习的讨论会。她约了克劳斯上午10点在奥克山学院图书馆桌子旁交流，准备把文学研究中的细读方法和智能体行为观察结合起来。
```

克劳斯的人设也同步变成社会影响视角：

```markdown
## 克劳斯

年龄：20岁
先天：善于倾听、关心公平、乐于辩论
后天：克劳斯是奥克山学院社会学专业的学生。他关注技术如何改变社区关系，尤其关心生成式智能体系统是否会放大或缓解不平等。
生活习惯：克劳斯晚上11点左右睡觉，早上7点左右醒来，上午常在图书馆写作，下午会和同学讨论社会议题。
当前状态：克劳斯正在准备一场关于生成式智能体社会影响的讨论。他约了阿伊莎上午10点在奥克山学院图书馆桌子旁交流，重点关注智能体系统在校园和社区治理中的公平性问题。
```

接着看 10:00 的活动记录：

```markdown
# 20240213-10:00

## 活动记录：

### 阿伊莎
位置：the Ville，奥克山学院，图书馆，图书馆桌子
活动：整理桌面资料，迎接克劳斯入座

### 克劳斯
位置：the Ville，奥克山学院，图书馆，图书馆桌子
活动：克劳斯与阿伊莎以奖学金分配中"参与度评分"为例，用细读方法拆解校园智能体的采集边界、价值偏向与权力预设，探讨是否应量化社区隐形互助活动。
```

这段时间线告诉我们三件事。第一，两个角色都在同一个具体空间。第二，阿伊莎的行动承担主持者角色，克劳斯的行动承担议题推动者角色。第三，对话结果已经反过来凝结成活动描述，进入了后续状态。

10:10 的活动记录显示状态继续推进：

```markdown
# 20240213-10:10

## 活动记录：

### 阿伊莎
位置：the Ville，奥克山学院，图书馆，图书馆桌子
活动：分享上午整理的关于生成式智能体的阅读笔记

### 克劳斯
位置：the Ville，奥克山学院，图书馆，图书馆桌子
活动：回顾上次关于智能体社会影响的讨论要点
```

这就是短实验的价值。两个 step 已经足够看出配置是否生效：角色到达了目标地点，生成了符合身份的对话，下一步行动继续沿着讨论会推进。

## 13.10 阅读 movement.json

`simulation.md` 适合人读，`movement.json` 适合前端回放。先看文件开头：

```json
{
  "start_datetime": "2024-02-13T10:00:00",
  "stride": 10,
  "sec_per_step": 10,
  "persona_init_pos": {
    "阿伊莎": [119, 24],
    "克劳斯": [119, 24]
  }
}
```

`start_datetime` 和 `stride` 对应运行命令里的 `--start` 和 `--stride`。`persona_init_pos` 说明两个角色的初始回放坐标都是 `[119, 24]`，也就是我们在 `agent.json` 中写入的图书馆桌子坐标。

接着看 `description`：

```json
{
  "阿伊莎": {
    "currently": "阿伊莎正在组织一次关于生成式智能体如何服务校园学习的讨论会。她约了克劳斯上午10点在奥克山学院图书馆桌子旁交流，准备把文学研究中的细读方法和智能体行为观察结合起来。",
    "scratch": {
      "innate": "好奇、善于提问、组织力强",
      "daily_plan": "阿伊莎上午10点在奥克山学院图书馆桌子旁主持一场关于生成式智能体校园应用的讨论，并请克劳斯从社会影响角度提出问题。"
    }
  }
}
```

这部分把配置快照带进了压缩结果。回放不是只保存坐标，它还保留了角色设定。以后做自己的应用场景时，看到 `description` 里的设定不对，就回到 `agent.json` 检查；看到设定正确但行为不对，再继续查调度、感知和 prompt。

再看第一个有效回放帧：

```json
"1": {
  "阿伊莎": {
    "location": "奥克山学院，图书馆，图书馆桌子",
    "movement": [119, 24],
    "action": "整理桌面资料，迎接克劳斯入座"
  },
  "克劳斯": {
    "location": "奥克山学院，图书馆，图书馆桌子",
    "movement": [119, 24],
    "action": "克劳斯与阿伊莎以奖学金分配中\"参与度评分\"为例，用细读方法拆解校园智能体的采集边界、价值偏向与权力预设，探讨是否应量化社区隐形互助活动。"
  }
}
```

前端回放真正用的是这种帧数据：谁在哪里，坐标是多少，正在做什么。`movement` 让角色落在地图上，`location` 给人读空间地址，`action` 变成角色旁边的行为文本。

再看第二个仿真时间点对应的帧：

```json
"61": {
  "阿伊莎": {
    "location": "奥克山学院，图书馆，图书馆桌子",
    "movement": [119, 24],
    "action": "分享上午整理的关于生成式智能体的阅读笔记"
  },
  "克劳斯": {
    "location": "奥克山学院，图书馆，图书馆桌子",
    "movement": [119, 24],
    "action": "回顾上次关于智能体社会影响的讨论要点"
  }
}
```

这里的 `"61"` 不是第 61 个仿真 step，而是前端回放用的细粒度帧编号。读 `movement.json` 不要把帧号和 `--step` 混在一起；判断仿真推进，要结合 `start_datetime`、`stride`、`simulation.md` 的时间标题和具体动作。

## 13.11 本次运行生成了哪些文件

这次实验产生两类结果。

| 文件或目录 | 位置 | 用途 |
| --- | --- | --- |
| `book-config-ai-seminar.log` | `results/checkpoints/book-config-ai-seminar/` | 记录 reset、schedule、percept、chat、summary 等运行过程 |
| `simulate-20240213-1000.json` | `results/checkpoints/book-config-ai-seminar/` | 第 1 个 step 的完整状态 |
| `simulate-20240213-1010.json` | `results/checkpoints/book-config-ai-seminar/` | 第 2 个 step 的完整状态 |
| `conversation.json` | `results/checkpoints/book-config-ai-seminar/` | 原始对话记录 |
| `storage/阿伊莎/`、`storage/克劳斯/` | `results/checkpoints/book-config-ai-seminar/` | 两个角色运行后的记忆索引和持久化数据 |
| `movement.json` | `results/compressed/book-config-ai-seminar/` | 前端回放使用的数据 |
| `simulation.md` | `results/compressed/book-config-ai-seminar/` | 人工阅读的仿真时间线 |

检查这类配置实验，不需要先钻进源码。先确认四件事：

1. `movement.json` 的 `description` 是否出现新的角色设定。
2. `simulation.md` 的活动记录是否落在新场景里。
3. `conversation.json` 是否出现符合人设的交互。
4. 第二个 step 是否延续第一个 step 的对话结果。

这四件事成立，配置层迁移已经成功。

## 13.12 恢复配置或保留实验

如果这只是一次读书练习，跑完后把备份文件恢复回去：

```powershell
Copy-Item "$env:TEMP/aisha.agent.json" "frontend/static/assets/village/agents/阿伊莎/agent.json"
Copy-Item "$env:TEMP/klaus.agent.json" "frontend/static/assets/village/agents/克劳斯/agent.json"
```

如果这是自己的项目实验，就不要急着恢复。可以先看 `git diff`，确认这次真的只改了两个角色配置：

```bash
git diff -- frontend/static/assets/village/agents/阿伊莎/agent.json
git diff -- frontend/static/assets/village/agents/克劳斯/agent.json
```

配置实验改动范围很窄，结果证据很完整。一个场景是否成立，可以通过 JSON diff、控制台摘要、对话记录和压缩结果一起判断。

## 13.13 迁移到自己的应用场景

把这套方法迁移到自己的项目时，可以按下面顺序来做，迅速将自己的想法跑通：

| 步骤 | 要做的事 | 不要急着做的事 |
| --- | --- | --- |
| 1 | 选择已有地图里的真实地点 | 先画一张新地图 |
| 2 | 选择已注册角色作为配置槽位 | 先新增角色注册逻辑 |
| 3 | 修改 `currently`、`scratch.learned`、`scratch.daily_plan`、`coord` | 先改 prompt 或调度源码 |
| 4 | 用 2 到 3 个 step 跑短实验 | 一上来跑几十个 step |
| 5 | 读控制台、`conversation.json`、`simulation.md`、`movement.json` | 只看前端回放是否“动起来” |
| 6 | 根据结果微调配置 | 把所有问题都归因到模型能力 |

配置层适合回答三个问题：角色是谁，今天要做什么，在哪里和谁发生关系。只要这三个问题写清楚，Generative Agents 就能生成初步可观察的行为。

新角色名、新头像、新地图、新业务对象和新交互逻辑，会把问题推进到源码层。配置层先完成场景迁移：不改代码，也能把原项目从默认小镇情节改造成一个新的实验场景。

## 参考资料

- Joon Sung Park, Joseph C. O'Brien, Carrie J. Cai, Meredith Ringel Morris, Percy Liang, Michael S. Bernstein. "Generative Agents: Interactive Simulacra of Human Behavior." UIST 2023.
- 实验结果：`generative_agents/results/compressed/book-config-ai-seminar/`。
