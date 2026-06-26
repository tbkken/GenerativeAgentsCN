# 第 23 章 回放系统：断点 checkpoint、movement.json、simulation.md 与 Phaser 前端

## 23.1 核心问题

第三部分最后一章讲回放系统。Generative Agents 的仿真结果不是只存在日志里。运行后，项目会生成断点 checkpoint。然后压缩脚本 `compress.py` 把断点 checkpoint 转换成：

```text
movement.json
simulation.md
```

回放服务 `replay.py` 再通过 Flask 和前端渲染引擎 Phaser 展示小镇动画。这个系统有三个价值。第一，可视化。读者可以看角色在小镇中移动、睡觉、聊天、行动。第二，可复盘。`simulation.md` 记录人物活动和对话，适合阅读和写书。第三，可评价。`movement.json`、`simulation.md` 和 `conversation.json` 可以作为实验数据，支撑派对传播、竞选扩散、关系形成等分析。本章聚焦八个问题：

1. 断点 checkpoint 保存了什么？
2. `compress.py` 如何生成 `movement.json`？
3. `simulation.md` 如何生成？
4. `movement.json` 的结构是什么？
5. `replay.py` 如何启动回放？
6. 前端模板如何展示角色？
7. 回放系统与实验评价有什么关系？
8. 当前回放系统有哪些边界和升级方向？

```mermaid
flowchart LR
    C["断点目录 results/checkpoints/name<br/>逐步状态 JSON"] --> CP["压缩脚本 compress.py"]
    Conv["对话文件 conversation.json"] --> CP
    Maze["世界地图 maze.json"] --> CP
    CP --> MD["时间线报告 simulation.md<br/>人类可读复盘"]
    CP --> MV["移动帧文件 movement.json<br/>前端回放数据"]
    MV --> RP["回放服务 replay.py"]
    RP --> UI["前端渲染引擎 Phaser"]
```

*图 23-1：断点 checkpoint -> 压缩脚本 compress.py -> 回放服务 replay.py 的数据流。原始 checkpoint 适合审计，压缩后的 `movement.json` 和 `simulation.md` 分别服务前端回放和人类复盘。*

本章的证据脚手架读取 `book-party-pair` 的真实压缩结果，并统计 checkpoint、回放帧、角色和 `simulation.md` 字符数：

```bash
python docs/book/scaffolds/part_03/ch17_23_part03_evidence.py
```

本章相关输出如下：

```text
chapter23 replay: checkpoints=6, movement_frames=361, agents=伊莎贝拉,阿伊莎, simulation_md_chars=4698
trace: docs/book/assets/chapter_23/ch23_replay_trace.json
figure: docs/book/assets/chapter_23/ch23_replay_dataflow.png
```

![图 23-2：从断点 checkpoint 到前端回放的一段真实轨迹](../../assets/chapter_23/ch23_replay_dataflow.png)

*图 23-2：从断点 checkpoint 到前端回放的一段真实轨迹。左侧是真实小镇地图上的两名角色路径，右侧同时展示移动回放文件 `movement.json`、时间线文件 `simulation.md` 和压缩统计。*

这行输出可以这样读：

| 输出片段 | 对应源码或文件 | 读法 |
| --- | --- | --- |
| `checkpoints=6` | `results/checkpoints/book-party-pair/simulate-*.json` | 这个实验有 6 个原始状态快照，适合审计完整角色状态。 |
| `movement_frames=361` | 压缩脚本 `compress.py` 与 `frames_per_step=60` | 回放不是一条 action 记录，而是被展开成前端逐帧播放的数据。 |
| `agents=伊莎贝拉,阿伊莎` | `movement.json` 的 `persona_init_pos` | 本次回放包含两个角色，前端初始位置和后续帧都按角色名索引。 |
| `simulation_md_chars=4698` | 时间线报告 `simulation.md` | 人类复盘读的是压缩后的时间线，不需要直接读庞大的 checkpoint JSON。 |

## 23.2 从仿真到回放的三阶段

整个流程分三阶段。下面三条命令都在项目运行目录 `generative_agents` 下执行；输入来自 `results/checkpoints/<name>`、`conversation.json` 和地图文件 `frontend/static/assets/village/maze.json`，输出写到 `results/compressed/<name>`。

第一阶段，运行仿真：

```bash
python start.py --name sim-test --start "20250213-09:30" --step 10 --stride 10
```

对应的输出结果应该类似这样：

```text
results/checkpoints/sim-test/
```

第二阶段，压缩结果：

```bash
python compress.py --name sim-test
```

对应的输出结果应该类似这样：

```text
results/compressed/sim-test/movement.json
results/compressed/sim-test/simulation.md
```

第三阶段，启动回放：

```bash
python replay.py
```

浏览器访问下面地址：

```text
http://127.0.0.1:5000/?name=sim-test
```

这三个阶段分别对应：

```text
生成数据
  -> 整理数据
  -> 展示数据
```

理解这条链路后，读者就能知道实验结果存在哪里。

## 23.3 checkpoint 是原始仿真状态

`start.py` 每个 step 写 checkpoint：

```text
results/checkpoints/<name>/simulate-<time>.json
```

同时还会保存下面内容：

```text
results/checkpoints/<name>/conversation.json
```

checkpoint JSON 保存的是仿真状态。包括：

- 当前 time。
- step。
- stride。
- maze path。
- agent_base。
- 每个 agent 的配置和状态。
- agent 当前 coord。
- status。
- schedule。
- associate memory ids。
- chats。
- currently。
- action。

阅读 checkpoint 时不要从第一行 JSON 机械往下看。更有效的顺序是先看顶层 `time`、`step` 和 `stride`，确认这是哪个仿真时刻；再进入目标 agent，查看 `coord`、`action`、`schedule` 和 `currently`；最后再看 `associate`、`chats` 等记忆相关状态。这样读，checkpoint 就是“某个时间点的小镇快照”，而不是一大团难读的 JSON。

它适合断点恢复和严谨审计。但它不适合直接给人读故事线。因此需要 `compress.py` 转换。

## 23.4 conversation.json

`conversation.json` 保存全局对话。真实结果中的一段对话可以整理成下面这种结构。下面片段来自 `example` 回放中 `20240213-06:00` 的山姆和詹妮弗对话，省略了部分字段外壳：

```json
{
  "20240213-06:00": [
    {
      "詹妮弗 -> 山姆 @ the Ville，摩尔家族的房子，主人房，床": [
        ["詹妮弗", "早上好，山姆。你今天打算去花园里忙活些什么呢？"],
        ["山姆", "早上好，詹妮弗。我打算去约翰逊公园打理一下花坛和修剪一些灌木。今天也是个宣传我的竞选计划的好机会。"]
      ]
    }
  ]
}
```

这个文件由 `_chat_with()` 写入内存，由 `SimulateServer.simulate()` 每步写盘。它是信息扩散实验的重要证据。如果我们要证明阿伊莎知道派对，是因为伊莎贝拉告诉她，不能只看阿伊莎后来说“我知道”。还要能在 `conversation.json` 或 `simulation.md` 中找到这次对话。

这个结构可以拆成三层阅读。第一层 key 是小镇时间。第二层 key 同时包含“谁对谁说话”和对话地点。第三层列表才是具体轮次。实验记录传播路径时，最重要的是前两层：谁在什么时间、什么地点，把信息传给了谁。具体话术可以再从第三层摘录。

## 23.5 compress.py 的两个输出

`compress.py` 定义：

```python
file_markdown = "simulation.md"
file_movement = "movement.json"
frames_per_step = 60
```

它有两个主要函数，需要结合源码查看：

```python
generate_report(...)
generate_movement(...)
```

`generate_report()` 生成 `simulation.md`。`generate_movement()` 生成 `movement.json`。这两个输出面向不同用途。`simulation.md` 面向人阅读。`movement.json` 面向前端回放。后续复现实验会同时使用二者。

## 23.6 generate_movement() 总览

`generate_movement()` 做几件事。第一，读取 conversation。第二，收集 checkpoint JSON 文件。第三，读取 stride。第四，加载 maze，用于计算移动路径。第五，为 step 1 插入第 0 帧初始状态。第六，遍历每个 checkpoint 中的 agent。第七，根据 source_coord 和 target_coord 计算 path。第八，把每个 step 展开成 60 帧。第九，把结果保存为 `movement.json`。这一步把离散 checkpoint 变成前端可播放帧数据。

## 23.7 movement.json 的顶层结构

`generate_movement()` 最终输出：

```python
result = {
    "start_datetime": "",
    "stride": stride,
    "sec_per_step": sec_per_step,
    "persona_init_pos": persona_init_pos,
    "all_movement": all_movement,
}
```

字段含义可以这样理解：

`start_datetime` 是回放起始时间。`stride` 是每个仿真 step 对应多少分钟。`sec_per_step` 是回放时每一帧对应秒数。`persona_init_pos` 保存每个角色初始位置。`all_movement` 保存每一帧的角色移动、位置和动作。其中 `all_movement` 还包含：

```text
description
conversation
```

用于展示角色基础信息和对话内容。

如果直接打开 `movement.json`，建议先读顶层字段，再抽样看关键帧。以第 12 章生成的 `book-smoke/movement.json` 为例，节选如下：

```json
{
  "start_datetime": "2024-02-13T09:30:00",
  "stride": 10,
  "persona_init_pos": {
    "阿伊莎": [118, 61],
    "克劳斯": [126, 46]
  },
  "all_movement": {
    "1": {
      "阿伊莎": {
        "location": "奥克山学院宿舍，阿伊莎的房间，书桌",
        "movement": [118, 61],
        "action": "前往 奥克山学院宿舍，阿伊莎的房间，书桌"
      }
    }
  }
}
```

这段数据说明三件事。第一，回放起点是 09:30。第二，本次只有两个角色，所以 `persona_init_pos` 只有两个名字。第三，第 1 帧中阿伊莎的 `movement` 仍是坐标，但 `location` 已经是人类可读地点。不要把 `movement.json` 当作最原始事实。它是从 checkpoint 和 maze 重新整理出的回放数据，适合播放和统计位置；如果要判断记忆、计划或 action 的完整上下文，仍然要回查 checkpoint。

## 23.8 第 0 帧：insert_frame0()

`insert_frame0()` 插入角色初始状态。它读取每个角色的 `agent.json`：

```python
json_path = f"frontend/static/assets/village/agents/{agent_name}/agent.json"
```

然后取出下面这些内容：

- living_area。
- 初始 coord。
- currently。
- scratch。

并写入下面这些内容：

```python
movement["0"][agent_name] = {
    "location": location,
    "movement": coord,
    "description": "正在睡觉",
}
```

第 0 帧主要给前端初始化角色位置和基础信息。注意这里 description 默认是“正在睡觉”，这是一种初始显示简化，不一定代表角色真实 action。

## 23.9 从 checkpoint 到 path

对每个 checkpoint，`generate_movement()` 取：

```python
source_coord = last_location.get(agent_name, all_movement["0"][agent_name])["movement"]
target_coord = agent_data["coord"]
location = get_location(agent_data["action"]["event"]["address"])
path = maze.find_path(source_coord, target_coord)
```

回放路径不是 checkpoint 直接保存的 path，而是根据上一位置和当前 checkpoint 坐标重新计算。这样可以让前端播放移动过程。如果找不到 location，则使用上一位置。这说明 movement.json 是派生数据，不是原始仿真状态。原始状态仍然在 checkpoint。

## 23.10 frames_per_step

`compress.py` 中：

```python
frames_per_step = 60
```

每个仿真 step 被展开成 60 帧。对于每一帧：

```python
step_key = "%d" % ((step-1) * frames_per_step + 1 + i)
```

如果 path 有剩余坐标，就每帧取一个点。如果 path 结束，movement 为 None。这让前端可以平滑播放角色移动。不过它也意味着：

```text
回放帧数不等于仿真认知步数。
```

agent 不是每一帧都思考。agent 每 step 思考一次，前端只是把 step 展开成动画。

## 23.11 action 文本

回放中每帧会保存 action。如果正在移动：

```python
action = f"前往 {location}"
```

角色没有移动时，回放帧直接使用当前行动描述：

```python
action = agent_data["action"]["event"]["describe"]
```

如果 describe 为空，就用：

```python
predicate + object
```

睡觉动作会增加睡眠图标：

```text
😴
```

如果该 step 有对话，会加：

```text
💬
```

这些 action 主要用于前端显示。它不一定包含完整行为上下文。完整上下文需要看 checkpoint 和 simulation.md。

## 23.12 对话如何进入 movement.json

`generate_movement()` 会读取 conversation。如果某个 step_time 有对话，会生成文本：

```python
step_conversation += f"\n地点：{...}\n\n"
for c in chat:
    step_conversation += f"{agent}：{text}\n"
```

然后写入下面这些内容：

```python
all_movement["conversation"][step_time] = step_conversation
```

前端可以根据时间显示对话。这让回放不仅是角色移动，也能看到聊天内容。

## 23.13 generate_report()：生成 simulation.md

`generate_report()` 生成 Markdown 报告。它首先写基础人设：

```markdown
# 基础人设

## 克劳斯

年龄：20岁
先天：善良、好奇、热情
后天：...
生活习惯：...
当前状态：...
```

然后遍历 checkpoint，提取活动变化。如果角色位置和 action 与上次一样，就跳过。这避免 Markdown 被重复状态刷屏。当有变化时，写入：

```markdown
# 20250213-10:20

## 活动记录：

### 克劳斯
位置：...
活动：...
```

如果该时间有对话，再写对话记录。

阅读 `simulation.md` 时，先看 `# 基础人设`，确认本次结果包含哪些角色；再看每个时间标题，确认仿真节奏；然后看 `活动记录`，判断角色行为是否连续；最后看对话记录，判断信息是否真的通过角色互动传播。这个阅读顺序和第 12 章的入门读法一致，只是这里进一步说明它由 `generate_report()` 从 checkpoint 和 conversation 派生出来。

## 23.14 simulation.md 的价值

`simulation.md` 是写书和实验的关键文件。它有三类价值。第一，人类可读。不用打开前端，也能按时间线阅读小镇发生了什么。第二，可引用。写实验报告时，可以引用某个时间点某个角色的活动和对话。第三，可审计。如果某个角色声称知道派对，可以回查时间线，看它什么时候听到。这使 Generative Agents 不只是演示项目，而是可分析项目。

## 23.15 replay.py：Flask 服务

回放服务入口可以定位到：

```text
generative_agents/replay.py
```

它创建 Flask app：

```python
app = Flask(
    __name__,
    template_folder="frontend/templates",
    static_folder="frontend/static",
    static_url_path="/static",
)
```

首页路由读取 query 参数：

```text
name
step
speed
zoom
```

可以看一个具体例子：

```text
http://127.0.0.1:5000/?name=sim-test&step=0&speed=2&zoom=0.8
```

它会加载下面这些内容：

```text
results/compressed/<name>/movement.json
```

然后渲染下面这些内容：

```text
frontend/templates/index.html
```

## 23.16 step、speed、zoom

`replay.py` 对参数做处理。`step` 决定从第几个仿真 step 开始回放。如果 step > 1，会调整 `start_datetime` 和 agent 初始位置。`speed` 限制在 0 到 5，然后转换为：

```python
speed = 2 ** speed
```

也就是指数级速度。`zoom` 控制画面缩放。这些参数让读者可以快速跳到某段仿真。例如派对实验中，可以直接跳到下午 5 点前后。

## 23.17 index.html 与角色面板

`frontend/templates/index.html` 继承 `base.html`。它包含：

- `game-container`：Phaser 游戏容器。
- 角色头像列表。
- 点击角色显示详情面板。

每个角色详情主要包含：

```text
当前活动
目标地址
```

脚本中引入 Phaser：

```html
<script src='https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.js'></script>
```

并 include：

```text
main_script.html
```

真正地图和角色动画逻辑在 `main_script.html` 中。本书不需要逐行讲 Phaser 细节，但要知道前端消费的是 `movement.json`。

## 23.18 回放与 checkpoint 的区别

回放数据不是原始真相。原始真相是 checkpoint 和 storage。`movement.json` 是为了前端展示而生成的派生数据。`simulation.md` 是为了人读而生成的摘要数据。如果要做严谨评价，应优先查：

```text
checkpoint
conversation.json
agent memory storage
```

如果要快速理解故事线，可以看：

```text
simulation.md
movement.json
```

这两类数据服务不同的复现实验环节。

## 23.19 回放系统如何服务复现实验

第四部分会大量用到回放系统。情人节派对实验需要看：

- 伊莎贝拉什么时候邀请谁。
- 被邀请者是否在正确时间到达咖啡馆。
- 对话中是否包含时间和地点。

镇长竞选实验需要看：

- 山姆是否谈到竞选。
- 谁听到了。
- 谁又告诉别人。

关系形成实验需要看：

- 克劳斯和玛丽亚是否相遇。
- 是否多次对话。
- 后续活动是否更接近。

这些都可以从 `simulation.md` 和 `conversation.json` 开始分析。如果要统计位置和到场，则看 `movement.json` 或 checkpoint 中 coord/action。

## 23.20 回放系统边界

当前回放系统有几个边界。第一，压缩是离线的。运行仿真后需要手动执行 `compress.py`。第二，movement 路径重新计算。它根据 checkpoint 坐标和 Maze 重新生成 path，不一定完全等同于运行时返回 path。第三，`simulation.md` 只记录变化。如果状态不变，会跳过，因此不是每一步完整日志。第四，对话显示依赖 conversation 时间匹配。如果时间 key 不一致，对话可能无法显示。第五，前端更偏回放，不是实时交互编辑器。第六，Phaser 从 CDN 加载。离线环境可能需要本地化依赖。这些边界不会影响基本使用，但做严谨实验时要知道。

## 23.21 可改进方向

回放系统可以从五个方向升级。第一，自动压缩。仿真结束后自动生成 compressed 结果。第二，交互式时间线。在前端按角色、地点、事件筛选。第三，证据链接。从 `simulation.md` 的某条对话跳到对应 checkpoint 和 memory node。第四，实验指标导出。自动统计信息传播、到场率、对话次数、关系网络。第五，本地化前端依赖。避免 CDN 影响离线教学。这些升级会让项目更适合写实验报告和教学。

## 23.22 第三部分总结

第三部分源码深读覆盖了从底层到上层的主要模块：

- 世界模型。
- 智能体初始化。
- 仿真循环。
- 感知。
- 记忆。
- 日程。
- 社交。
- 反思。
- 模型适配。
- 回放系统。

论文概念和源码模块已经可以对应起来。下一部分不再只读源码，而是设计实验。第四部分会用当前项目复现论文中的情人节派对传播、镇长竞选信息扩散、角色关系形成，并设计自己的小镇事件。

## 23.23 本章小结

回放系统把后台仿真变成可检查证据。断点 checkpoint、移动帧文件 `movement.json`、时间线报告 `simulation.md` 和前端回放各自服务不同目的，不能混成一种材料。

| 本章内容 | 核心结论 |
| --- | --- |
| 原始输出 | `start.py` 每步生成断点 checkpoint 和对话文件 `conversation.json`。 |
| 断点 checkpoint | 断点 checkpoint 适合断点恢复和严谨审计，但不适合直接阅读。 |
| 压缩入口 | 压缩脚本 `compress.py` 生成移动帧文件 `movement.json` 和时间线报告 `simulation.md`。 |
| 移动帧文件 `movement.json` | 面向前端渲染引擎 Phaser 回放，服务可视化。 |
| 时间线报告 `simulation.md` | 面向人类阅读和实验复盘。 |
| movement 生成 | `generate_movement()` 会把断点 checkpoint 展开成每个仿真步 step 60 帧。 |
| report 生成 | `generate_report()` 会写基础人设、活动记录和对话记录。 |
| 前端回放 | 回放服务 `replay.py` 和 `index.html` 加载压缩数据 compressed data 并展示画面。 |
| 评价边界 | 回放数据是派生数据，严谨评价仍要回查断点 checkpoint、对话 conversation 和记忆 memory。 |
| 后续用途 | 第四部分复现实验会把这些输出作为证据基础。 |

下一章进入复现实验：我们先复现论文中的情人节派对传播。

## 参考资料

- Local source: `generative_agents/compress.py`
- Local source: `generative_agents/replay.py`
- Local source: `generative_agents/frontend/templates/index.html`
- Local source: `generative_agents/frontend/templates/main_script.html`
- Local output: `generative_agents/results/checkpoints/`
- Local output: `generative_agents/results/compressed/`
- Local scaffold: `docs/book/scaffolds/part_03/ch17_23_part03_evidence.py`
- Local trace: `docs/book/assets/chapter_23/ch23_replay_trace.json`
