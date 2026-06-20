# 第 16 章 日程：从一天计划到具体动作

## 16.1 本章要解决的问题

记忆让智能体有过去。

日程让智能体有未来。

GenerativeAgentsCN 中，角色不是每一步都临时决定“现在做什么”。它会先生成一天计划，再把当前计划拆成细粒度动作，并在对话、等待等意外发生时修改计划。

这一章讲日程系统。

核心源码包括：

```text
generative_agents/modules/memory/schedule.py
generative_agents/modules/agent.py
generative_agents/modules/prompt/scratch.py
generative_agents/data/prompts/schedule_*.txt
```

本章要回答八个问题：

1. `Schedule` 数据结构如何表示一天计划？
2. `make_schedule()` 如何生成新一天日程？
3. 日程生成前为什么要更新 `currently`？
4. `wake_up`、`schedule_init`、`schedule_daily` 分别负责什么？
5. 粗粒度计划如何拆成子计划？
6. `current_plan()` 如何决定当前动作？
7. 对话和等待如何改写日程？
8. 当前日程系统有哪些失败模式和升级方向？

[图 16-1：日程生成、拆解与重规划流程]

## 16.2 为什么日程是核心模块

如果没有日程，agent 会变成即时反应系统。

它看到什么就回应什么。

这种系统可以聊天，但不像一个在小镇里生活的人。

人类生活通常有日程结构：

- 早上起床。
- 吃早餐。
- 上学或上班。
- 中午吃饭。
- 下午工作。
- 晚上社交或休息。
- 睡觉。

这个结构让行为连续、稳定、可预期。

在多智能体系统里，日程还有一个重要作用：制造相遇。

如果伊莎贝拉常在咖啡馆，克劳斯和玛丽亚也常去咖啡馆，他们就有机会相遇。

如果山姆在公共地点谈竞选，居民就可能听到。

所以，日程不是装饰。

它是社会互动发生的时间骨架。

## 16.3 Schedule 数据结构

`Schedule` 定义在：

```text
generative_agents/modules/memory/schedule.py
```

初始化：

```python
class Schedule:
    def __init__(self, create=None, daily_schedule=None, diversity=5, max_try=5):
        ...
```

核心字段是：

- `create`：日程创建日期。
- `daily_schedule`：当天计划列表。
- `diversity`：活动多样性要求。
- `max_try`：生成失败时最大尝试次数。

`daily_schedule` 中每个 plan 是 dict：

```python
{
    "idx": 0,
    "describe": "睡觉",
    "start": 0,
    "duration": 420,
    "decompose": {}
}
```

`start` 和 `duration` 都是分钟数。

`decompose` 保存子计划。

这说明 Schedule 是时间表，不是自然语言段落。

## 16.4 add_plan()

添加计划使用：

```python
add_plan(describe, duration, decompose=None)
```

它会根据上一条计划自动计算 start：

```python
if self.daily_schedule:
    last_plan = self.daily_schedule[-1]
    start = last_plan["start"] + last_plan["duration"]
else:
    start = 0
```

因此，daily_schedule 是连续排列的。

第一条从 0 分钟开始。

后一条从上一条结束时间开始。

这比每条 plan 都由模型生成 start/end 更稳定。

模型只需要提供每个小时活动，代码负责把它转成持续时间。

## 16.5 scheduled()：当天是否已有日程

`Schedule.scheduled()` 用来判断今天是否已经有日程：

```python
if not self.daily_schedule:
    return False
return utils.get_timer().daily_format() == self.create.strftime("%A %B %d")
```

如果日程为空，返回 False。

如果当前日期与 create 日期一致，返回 True。

这意味着到了新的一天，系统会重新生成日程。

这很重要。

角色不是整个仿真只生成一次计划，而是每天都可以基于记忆更新当天安排。

## 16.6 make_schedule() 总览

`Agent.make_schedule()` 是日程生成主入口。

简化流程：

```text
如果今天还没有日程：
  如果已有记忆，检索近期计划与重要事件，更新 currently
  生成起床时间
  生成初始日程
  生成小时级日程
  转成 Schedule.daily_schedule
  把今天计划写入 thought

取当前 plan
如果需要拆解：
  调用 schedule_decompose
返回 current_plan()
```

这条链路把角色设定、记忆和模型生成结合起来。

它不是静态日程表。

它会受昨天发生的事情影响。

## 16.7 新一天前更新 currently

如果已有记忆，`make_schedule()` 会先更新 `currently`：

```python
focus = [
    f"{self.name} 在 {utils.get_timer().daily_format_cn()} 的计划。",
    f"在 {self.name} 的生活中，重要的近期事件。",
]
retrieved = self.associate.retrieve_focus(focus)
```

然后：

```python
plan = self.completion("retrieve_plan", retrieved)
thought = self.completion("retrieve_thought", retrieved)
self.scratch.currently = self.completion("retrieve_currently", plan, thought)
```

这是日程系统最容易被忽略的设计。

它让角色的新一天不是从原始 `agent.json` 重新开始，而是从过去经历接续。

例如：

- 伊莎贝拉昨天邀请了很多人，今天可能继续准备派对。
- 山姆昨天和居民谈过竞选，今天可能继续拜访居民。
- 克劳斯昨天遇到玛丽亚，今天可能有新的社交倾向。

如果没有这一步，仿真会每天重启人格状态。

## 16.8 wake_up：生成起床时间

接下来：

```python
wake_up = self.completion("wake_up")
```

`prompt_wake_up()` 使用：

- `base_desc`
- `lifestyle`
- `agent`

输出 schema 是：

```python
res: int
```

范围是 0 到 11。

callback 会把超过 11 的值截断到 11。

起床时间来自角色生活习惯。

这使不同角色一天开始时间不同。

如果所有角色都同一时间醒来，小镇会显得机械。

## 16.9 schedule_init：生成日程大纲

起床时间之后：

```python
init_schedule = self.completion("schedule_init", wake_up)
```

`schedule_init` 输出的是按时间顺序排列的活动列表。

它不是严格 24 小时时间表，而是一天大纲。

例如：

```text
早上起床并吃早餐。
上午去图书馆写论文。
中午在咖啡馆吃饭。
下午继续研究。
晚上回宿舍休息。
```

这个大纲会传给 `schedule_daily`。

它的作用是让小时级日程有整体方向。

## 16.10 schedule_daily：生成小时级日程

`make_schedule()` 构造小时模板：

```python
hours = [f"{i}:00" for i in range(24)]
seed = [(h, "睡觉") for h in hours[:wake_up]]
seed += [(h, "") for h in hours[wake_up:]]
```

起床前填睡觉。

起床后让模型填活动。

然后调用：

```python
self.completion("schedule_daily", wake_up, init_schedule)
```

`schedule_daily.txt` 要求返回：

```json
{
  "6:00": "起床并完成早晨的例行工作",
  "7:00": "吃早餐",
  "8:00": "读书"
}
```

这一步把自然语言大纲转成小时级结构。

代码还检查活动多样性：

```python
if len(set(schedule.values())) >= self.schedule.diversity:
    break
```

避免模型生成一整天重复同一活动。

## 16.11 转成分钟制 plan

模型返回的是时间字符串。

代码把它转成分钟数：

```python
def _to_duration(date_str):
    return utils.daily_duration(utils.to_date(date_str, "%H:%M"))
```

然后：

```python
schedule = {_to_duration(k): v for k, v in schedule.items()}
starts = list(sorted(schedule.keys()))
for idx, start in enumerate(starts):
    end = starts[idx + 1] if idx + 1 < len(starts) else 24 * 60
    self.schedule.add_plan(schedule[start], end - start)
```

如果时间点是：

```text
8:00 -> 读书
12:00 -> 吃午饭
13:00 -> 小睡
```

就会转成：

```text
8:00-12:00 读书
12:00-13:00 吃午饭
13:00-下一个时间点 小睡
```

模型负责活动，代码负责持续时间。

## 16.12 把当天计划写入 thought

日程生成后，系统会写入记忆：

```python
thought = "这是 {} 在 {} 的计划：{}".format(
    self.name, schedule_time, "；".join(init_schedule)
)
event = memory.Event(
    self.name,
    "计划",
    schedule_time,
    describe=thought,
    address=self.get_tile().get_address(),
)
self._add_concept("thought", event, expire=self.schedule.create + datetime.timedelta(days=30))
```

这一步让角色知道自己的计划。

计划不仅存在于 `Schedule`，也成为 memory stream 中的 thought。

后续对话、反思和新一天计划都可能检索到它。

这体现了论文思想：计划也是智能体经验的一部分。

## 16.13 current_plan()

`Schedule.current_plan()` 根据当前时间找计划。

流程：

```text
取当前 daily_duration
遍历 daily_schedule
  找到尚未结束的 plan
  如果 plan 有 decompose
    找到尚未结束的 de_plan
    返回 plan, de_plan
  否则返回 plan, plan
```

如果所有计划都结束，返回最后一个。

这个函数返回两个值：

```text
plan：粗粒度计划。
de_plan：当前细粒度子计划。
```

后续 `_determine_action()` 会同时使用两者。

粗计划提供场景方向。

子计划提供具体动作。

## 16.14 decompose()：是否需要拆解

`Schedule.decompose(plan)` 判断当前 plan 是否要拆成子计划。

如果已有 decompose，返回 False。

如果是睡眠或床相关活动，通常不拆。

其他活动一般会拆。

这里源码逻辑有一些中英文兼容判断：

```python
if "sleep" not in describe and "bed" not in describe:
    return True
if "睡" not in describe and "床" not in describe:
    return True
...
```

本质目标是：

```text
睡觉不必细拆，其他长活动需要细拆。
```

例如：

```text
上午写论文
```

可以拆成：

```text
整理资料
阅读文献
写段落
修改草稿
```

## 16.15 schedule_decompose：粗计划变子计划

如果需要拆解：

```python
decompose_schedule = self.completion(
    "schedule_decompose", plan, self.schedule
)
```

`prompt_schedule_decompose()` 会把当前 plan 前后相邻计划也传入 prompt。

这样模型知道上下文：

```text
上一段做什么
当前段做什么
下一段做什么
```

它还计算一个 increment：

```python
increment = max(int(plan["duration"] / 100) * 5, 5)
```

用于控制子任务时间粒度。

返回结构是：

```python
List[Tuple[str, int]]
```

每项是：

```text
活动描述 + 持续分钟数
```

代码会补足剩余时间：

```python
left = plan["duration"] - sum([s[1] for s in response])
if left > 0:
    response.append((plan["describe"], left))
```

确保子计划总时长覆盖粗计划。

## 16.16 子计划如何写回 plan

`make_schedule()` 把模型返回的子计划转成 dict：

```python
decompose, start = [], plan["start"]
for describe, duration in decompose_schedule:
    decompose.append(
        {
            "idx": len(decompose),
            "describe": describe,
            "start": start,
            "duration": duration,
        }
    )
    start += duration
plan["decompose"] = decompose
```

每个子计划也有：

- idx。
- describe。
- start。
- duration。

注意，子计划的 start 是当天分钟数，不是相对粗计划的偏移。

这使 `current_plan()` 可以直接用当前 daily_duration 判断子计划是否结束。

## 16.17 _determine_action()：日程落到动作

日程本身还不是 action。

当当前 action 结束时，`make_plan()` 会调用：

```python
self.action = self._determine_action()
```

`_determine_action()` 取：

```python
plan, de_plan = self.schedule.current_plan()
describes = [plan["describe"], de_plan["describe"]]
```

然后决定地点、对象、角色事件和对象事件。

最终返回：

```python
memory.Action(
    event,
    obj_event,
    duration=de_plan["duration"],
    start=utils.get_timer().daily_time(de_plan["start"]),
)
```

也就是说：

```text
Schedule 决定做什么和持续多久。
_determine_action() 决定在哪里做，并生成可写入世界的事件。
```

## 16.18 revise_schedule()：意外插入当前计划

日程不是不能改。

当聊天或等待发生时，系统会调用：

```python
revise_schedule(event, start, duration)
```

它先把当前 action 改为意外事件：

```python
self.action = memory.Action(event, start=start, duration=duration)
```

然后取当前 plan：

```python
plan, _ = self.schedule.current_plan()
```

如果该 plan 有 decompose，就调用：

```python
plan["decompose"] = self.completion(
    "schedule_revise", self.action, self.schedule
)
```

这会重写当前计划的子计划。

例如，本来 10:00-11:00 写论文。

10:20-10:35 和玛丽亚聊天。

修订后可能是：

```text
10:00-10:20 写论文
10:20-10:35 与玛丽亚对话
10:35-11:00 继续写论文
```

这就是对话和计划的闭环。

## 16.19 schedule_chat()

对话结束后调用：

```python
self.schedule_chat(chats, chat_summary, start, duration, other)
```

它会：

1. 把 chats 加入 `self.chats`。
2. 创建对话 Event。
3. 调用 `revise_schedule()`。

对话 Event：

```python
memory.Event(
    self.name,
    "对话",
    other.name,
    describe=chats_summary,
    address=address or self.get_tile().get_address(),
    emoji=f"💬",
)
```

这说明对话不是只写日志。

它会变成当前 action，并占用日程时间。

同时，`self.chats` 后续会进入 reflection，用于生成对话后的计划影响和记忆影响。

## 16.20 等待也会改写日程

`_wait_other()` 决定等待后，也调用：

```python
self.revise_schedule(event, start, duration)
```

等待事件：

```python
memory.Event(
    self.name,
    "waiting to start",
    self.get_event().get_describe(False),
    address=self.get_event().address,
    emoji=f"⌛",
)
```

等待的 duration 取决于对方 action 剩余时间。

这让空间冲突也进入日程。

例如，浴室被占用，角色等待 10 分钟。

原计划会被切开，等待占用其中一段。

## 16.21 日程如何影响可信行为

日程系统让行为更可信，主要体现在五点。

第一，角色有作息。

不会全天随机活动。

第二，角色有目标连续性。

当前行为来自当天计划，而不是每步临时生成。

第三，角色有时间约束。

对话、等待和行动都会占用时间。

第四，角色有空间落地。

计划最终会变成 action 地址。

第五，角色有可解释性。

我们可以打开 schedule，看它为什么此刻去某处。

这也是论文中 planning 模块的重要价值。

## 16.22 日程失败模式

日程系统常见失败有六类。

第一，日程过于普通。

角色明明有当前目标，但一天计划像通用模板。

原因可能是 `currently` 太弱或 prompt 没抓住关键目标。

第二，时间不合理。

模型返回缺失时间点、重复时间点或奇怪顺序。

结构化输出能减少格式错误，但不能完全保证语义合理。

第三，活动多样性不足。

项目用 `diversity` 做简单检查，但仍可能生成高度相似活动。

第四，拆解过细或过粗。

子计划太碎会增加抖动，太粗会降低生活感。

第五，重规划失败。

对话插入后，剩余计划没有合理调整。

第六，计划与空间不匹配。

计划合理，但 `_determine_action()` 选错地点。

调试时要分清是 schedule 问题，还是 spatial grounding 问题。

## 16.23 日程实验怎么设计

可以设计几个小实验观察日程系统。

实验一：不同角色作息。

运行多个角色，比较 wake_up 和 daily_schedule。

看是否符合 `lifestyle`。

实验二：currently 对日程影响。

修改伊莎贝拉的 `currently`，观察是否增加派对准备活动。

实验三：对话插入。

让两个角色相遇并聊天，检查 `schedule_revise` 是否把对话插入当前计划。

实验四：反思后第二天日程。

运行跨天仿真，观察新一天 `currently` 是否根据记忆更新。

实验五：本地模型对日程稳定性影响。

比较不同模型生成的 schedule 是否格式稳定、活动合理。

这些实验会在第四部分复现实验中进一步展开。

## 16.24 可改进方向

日程系统可以从五个方向升级。

第一，引入目标层级。

把 daily goal、hourly plan、minute action 分层管理。

第二，引入约束求解。

例如活动必须符合地点开放时间、对象容量、角色承诺。

第三，引入事件邀请机制。

如果收到派对邀请，自动在日程中创建候选事件。

第四，引入计划置信度。

不是所有计划都必须执行，可以有意愿和优先级。

第五，引入失败恢复。

如果去错地点或错过活动，角色应能重新计划。

这些升级能让当前项目更接近 2026 年前沿 agent planning 系统。

## 16.25 本章小结

本章讲清了 GenerativeAgentsCN 的日程系统：

1. `Schedule` 保存当天计划列表，每个 plan 有 start、duration、describe、decompose。
2. `scheduled()` 判断当天是否已有日程。
3. `make_schedule()` 在新一天生成日程，并在必要时更新 `currently`。
4. `wake_up` 生成起床时间。
5. `schedule_init` 生成日程大纲。
6. `schedule_daily` 生成小时级日程。
7. 代码把小时级日程转成连续分钟制 plan。
8. 当天计划会写入 thought，成为角色记忆。
9. `current_plan()` 返回当前粗计划和子计划。
10. `schedule_decompose` 把粗计划拆成细粒度动作。
11. `_determine_action()` 把当前子计划落到空间 action。
12. `revise_schedule()` 让对话和等待改写当前计划。
13. 日程系统支撑作息、目标连续性、时间约束和行为可解释性。

下一章讲社交。我们会深入 `_reaction()`、`_chat_with()`、`_wait_other()`，看智能体如何决定聊天、生成多轮对话、保存对话摘要，并让信息在小镇中传播。

## 参考资料

- Local source: `generative_agents/modules/memory/schedule.py`
- Local source: `generative_agents/modules/agent.py`
- Local source: `generative_agents/modules/prompt/scratch.py`
- Local prompts: `generative_agents/data/prompts/wake_up.txt`
- Local prompts: `generative_agents/data/prompts/schedule_init.txt`
- Local prompts: `generative_agents/data/prompts/schedule_daily.txt`
- Local prompts: `generative_agents/data/prompts/schedule_decompose.txt`
- Local prompts: `generative_agents/data/prompts/schedule_revise.txt`
