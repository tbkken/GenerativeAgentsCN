# 第 18 章 反思：重要事件如何变成长期认知

## 18.1 本章要解决的问题

第 6 章已经讲过 Reflection 的论文思想。

这一章只讲源码。

在 GenerativeAgentsCN 中，反思入口是：

```text
Agent.reflect()
```

相关 prompt：

```text
reflect_focus
reflect_insights
reflect_chat_planing
reflect_chat_memory
```

反思做的事情可以概括为：

```text
重要事件累积到阈值
  -> 选取近期 event 和 thought
  -> 生成反思焦点问题
  -> 围绕问题检索证据
  -> 生成 insights
  -> 写回 thought
  -> 处理聊天带来的计划和记忆影响
```

本章要回答八个问题：

1. `reflect()` 什么时候会触发？
2. 它读取哪些记忆？
3. 为什么要先生成 focus？
4. evidence 如何保存？
5. thought 如何写回 Associate？
6. 对话反思如何处理？
7. 状态如何清零？
8. 如何调试反思是否真正影响行为？

[图 18-1：`Agent.reflect()` 源码流程]

## 18.2 reflect() 在 think() 中的位置

`Agent.think()` 中，醒着时执行：

```python
self.percept()
self.make_plan(agents)
self.reflect()
```

反思发生在感知和计划之后。

这意味着当前 step 新感知到的重要事件，已经可能写入 memory stream，并累积了 poignancy。

然后 `reflect()` 可以根据最新状态决定是否触发。

反思不是行动前的必经步骤。

它更像每一步末尾的认知更新。

如果重要性不足，它会直接返回。

如果达到阈值，它会把一段时间内的经历压缩成高层 thought。

## 18.3 触发条件：poignancy_max

`reflect()` 开头：

```python
if self.status["poignancy"] < self.think_config["poignancy_max"]:
    return
```

`poignancy_max` 来自 `data/config.json`：

```json
"poignancy_max": 150
```

这表示累计重要性达到 150 才会反思。

累计来源主要是 `percept()`：

```python
self.status["poignancy"] += node.poignancy
```

对话、重要事件、异常行为都会提高 poignancy。

普通空闲事件通常不会显著增加。

这让反思成为稀缺操作。

它不会每步都执行。

## 18.4 为什么要用累计阈值

累计阈值有三个作用。

第一，控制成本。

反思需要多次 LLM 调用。每步反思会让 25 个 agent 的运行成本爆炸。

第二，模拟阶段性内省。

人不会每看到一件小事就总结人生。

第三，避免 thought 噪声。

如果每个小事件都生成 thought，memory stream 会充满浅层结论。

因此，`poignancy_max` 是反思频率的重要旋钮。

如果阈值太高，角色很久不反思。

如果阈值太低，角色过度反思。

## 18.5 反思输入：events + thoughts

达到阈值后，`reflect()` 取：

```python
nodes = self.associate.retrieve_events() + self.associate.retrieve_thoughts()
```

也就是说，反思基于两类记忆：

- event。
- thought。

不直接取 chat。

chat 有单独处理逻辑。

这里包括 thought 非常重要。

因为这允许反思递归。

过去的 thought 可以参与新一轮反思，形成更高层认知。

如果没有 nodes，直接返回：

```python
if not nodes:
    return
```

## 18.6 节点选择：max_importance

取得 nodes 后：

```python
nodes = sorted(nodes, key=lambda n: n.access, reverse=True)[
    : self.associate.max_importance
]
```

`max_importance` 名字有点容易误解。

这里不是按 importance 排序，而是按 access 时间排序后截断。

构造函数默认：

```python
max_importance=10
```

也就是说，反思先取最近访问过的若干 event/thought。

这一步控制输入规模。

如果把所有记忆都交给模型，prompt 会过长，成本和噪声都很高。

## 18.7 日志：反思触发可观察

反思触发时会记录日志：

```python
self.logger.info(
    "{} reflect(P{}/{}) with {} concepts...".format(
        self.name,
        self.status["poignancy"],
        self.think_config["poignancy_max"],
        len(nodes),
    )
)
```

这条日志非常适合调试。

它告诉你：

- 谁触发了反思。
- 当前 poignancy 是多少。
- 阈值是多少。
- 使用了多少 concepts。

如果你怀疑角色没有反思，先找这条日志。

## 18.8 第一步：生成反思焦点

反思不是直接总结 nodes。

代码先调用：

```python
focus = self.completion("reflect_focus", nodes, 3)
```

`reflect_focus` 根据给定记忆节点生成 3 个焦点问题。

例如：

```text
克劳斯今天与玛丽亚的互动说明了什么？
克劳斯近期的研究计划受到哪些影响？
克劳斯应该如何看待自己与咖啡馆居民的关系？
```

焦点问题决定后续检索方向。

这比“请总结这些记忆”更可控。

它把反思变成问题驱动的推理。

## 18.9 第二步：围绕焦点检索证据

生成 focus 后：

```python
retrieved = self.associate.retrieve_focus(focus, reduce_all=False)
```

注意：

```text
reduce_all=False
```

这会保留：

```text
每个 focus -> 对应检索节点
```

而不是把所有结果合并。

这对 evidence 很重要。

每个 insight 应该基于某个焦点问题下的相关证据。

如果全部混在一起，模型更容易生成泛泛而谈的 thought。

## 18.10 第三步：生成 insights

对每组检索节点：

```python
for r_nodes in retrieved.values():
    thoughts = self.completion("reflect_insights", r_nodes, 5)
    for thought, evidence in thoughts:
        _add_thought(thought, evidence)
```

每组最多生成 5 个 insights。

`reflect_insights` 要求返回：

```text
洞察内容 + 相关节点编号
```

例如：

```text
("克劳斯认为玛丽亚愿意讨论开放性问题", "1,2,3")
```

`prompt_reflect_insights()` 会把编号转成 node_id：

```python
node_ids = [nodes[i].node_id for i in indices if i < len(nodes)]
```

这就是 evidence。

## 18.11 evidence 的意义

evidence 不是装饰。

它解决两个问题。

第一，可解释性。

我们可以追溯 thought 来自哪些记忆。

第二，抑制幻觉。

如果 insight 没有证据，它很可能只是模型编出来的性格判断。

例如：

```text
玛丽亚完全信任克劳斯。
```

如果 evidence 只是一次普通对话，这个 thought 就过度推断。

有 evidence 后，后续可以审查 thought 是否合理。

当前项目保存 evidence 到 `_add_thought()` 的 `filling` 参数，但需要注意，`Associate.add_node()` 当前 metadata 中没有显式保存 filling 字段。

也就是说，源码接口传了 evidence，但底层是否完整保留 evidence，需要进一步检查和改造。

这是一个值得改进的点。

## 18.12 _add_thought()

`reflect()` 内部定义：

```python
def _add_thought(thought, evidence=None):
    event = self.make_event(self.name, thought, self.get_tile().get_address())
    return self._add_concept("thought", event, filling=evidence)
```

它把 thought 包装成 Event。

subject 是 agent 自己。

address 是当前 tile 地址。

然后写入 Associate，类型是 `thought`。

这说明 thought 和 event 使用同一套 memory stream。

后续 retrieval 可以同时检索 event 和 thought。

这就是反思影响未来行为的关键。

## 18.13 thought 也会被评分

`_add_concept("thought", event, ...)` 会进入 `_add_concept()`。

由于 e_type 不是 chat，且通常不是空闲事件，所以会调用：

```python
poignancy_event
```

也就是说，thought 也会有 poignancy。

这有一个微妙效果：

反思生成的 thought 本身可能成为重要记忆。

后续 retrieval 更容易取到它。

但如果 thought 过多或评分过高，也可能让角色长期被某些反思主导。

这需要实验观察。

## 18.14 对话反思

普通 event/thought 反思之后，`reflect()` 还处理聊天：

```python
if self.chats:
    recorded, evidence = set(), []
    for name, _ in self.chats:
        if name == self.name or name in recorded:
            continue
        res = self.associate.retrieve_chats(name)
        if res and len(res) > 0:
            node = res[-1]
            evidence.append(node.node_id)
    thought = self.completion("reflect_chat_planing", self.chats)
    _add_thought(f"对于 {self.name} 的计划：{thought}", evidence)
    thought = self.completion("reflect_chat_memory", self.chats)
    _add_thought(f"{self.name} {thought}", evidence)
```

它生成两类 thought。

第一，对计划的影响。

第二，对记忆或关系的影响。

这让对话不仅保存为 chat summary，还能进入更高层认知。

## 18.15 chats 从哪里来

`self.chats` 主要来自：

```python
schedule_chat()
```

对话发生后：

```python
self.chats.extend(chats)
```

所以 `self.chats` 是近期待反思的原始对话片段。

它不是全部聊天历史。

全部聊天历史在 Associate 的 chat memory 中。

`self.chats` 更像一个反思缓冲区。

反思处理后会清空。

## 18.16 状态清零

反思末尾：

```python
self.status["poignancy"] = 0
self.chats = []
```

这很重要。

如果不清零，反思会连续触发。

清零后，agent 需要重新经历重要事件，才会再次反思。

`chats` 清空也说明对话反思是批处理。

已经反思过的聊天不会重复生成 thought。

## 18.17 反思如何影响后续行为

反思影响行为的路径是：

```text
insight
  -> _add_thought()
  -> Associate memory["thought"]
  -> retrieve_focus() / retrieve_thoughts()
  -> planning / dialogue / relation summary / future reflection
```

例如：

```text
克劳斯认为玛丽亚愿意讨论开放性问题。
```

这条 thought 后续可能影响：

- `summarize_relation()`。
- `generate_chat()`。
- 新一天 currently。
- 反思焦点。
- 计划选择。

如果 thought 写入但从不被检索，反思就没有行为效果。

所以评价反思时不能只看有没有生成 thought。

还要看后续是否使用。

## 18.18 调试反思的检查点

调试反思时按下面顺序。

第一，看 `status["poignancy"]` 是否累积。

如果一直很低，反思不会触发。

第二，看日志是否出现：

```text
<agent> reflect(P.../...) with ... concepts
```

第三，看 `reflect_focus` 输出是否具体。

如果 focus 很泛，后续 insights 也会泛。

第四，看 `retrieve_focus()` 是否检索到相关记忆。

第五，看 `reflect_insights` 输出是否有证据编号。

第六，看 `Associate.memory["thought"]` 是否新增 node。

第七，看后续对话或计划 prompt 是否检索到这些 thought。

第八，看行为是否改变。

这一整条链才叫反思闭环。

## 18.19 反思失败模式

反思常见失败有七类。

第一，阈值过高。

长时间不反思。

第二，阈值过低。

小事过度反思。

第三，输入节点不相关。

`nodes` 选择偏离重要经历。

第四，focus 过泛。

问题没有指向具体关系、计划或事件。

第五，检索证据不准。

insight 建立在无关记忆上。

第六，洞察过度推断。

从弱证据推出强关系或强承诺。

第七，thought 没有被后续使用。

存在于 memory stream，但不影响行为。

这些失败在复现实验中都可能出现。

## 18.20 反思与 Klaus/Maria 实验

Klaus/Maria 是观察反思的好案例。

可以运行两个版本：

```text
reflection on
reflection off
```

然后比较：

- 两人是否相遇。
- 是否对话。
- 对话摘要是否写入 chat memory。
- 反思后是否生成关于彼此的 thought。
- 下次相遇时关系摘要是否更具体。
- 后续是否更容易主动聊天。

如果有 reflection，理想结果是：

```text
克劳斯不只是记得和玛丽亚聊过。
他还形成对玛丽亚兴趣、性格或关系可能性的高层理解。
```

这就是 reflection 的行为价值。

## 18.21 可改进方向

反思模块可以从几个方向增强。

第一，保存完整 evidence graph。

当前 evidence 参数传入 `_add_concept()`，但底层 metadata 未完整显式保留，需要增强。

第二，区分 thought 类型。

例如 self、relation、goal、world、norm。

第三，增加反思质量评估。

用模型或规则检查 insight 是否被证据支持。

第四，引入反思遗忘。

不正确或过时 thought 应该能被修正。

第五，引入主动反思。

不只被 poignancy 触发，也可以在日末、睡前或计划失败后触发。

第六，与前沿 Reflexion 区分。

当前 reflection 主要是经验归纳，Reflexion 更偏任务失败后的自我改进。第五部分会展开。

## 18.22 本章小结

本章讲清了 GenerativeAgentsCN 的反思实现：

1. `reflect()` 在醒着 agent 的每步思考末尾调用。
2. 它通过 `status["poignancy"]` 与 `poignancy_max` 控制触发。
3. 反思输入是近期 event 和 thought。
4. 节点按 access 选择，数量受 `max_importance` 限制。
5. `reflect_focus` 先生成反思问题。
6. `retrieve_focus(..., reduce_all=False)` 为每个问题保留证据集合。
7. `reflect_insights` 生成 thought 和 evidence。
8. `_add_thought()` 把 thought 写回 Associate。
9. 对话反思会生成计划影响和记忆影响两类 thought。
10. 反思结束后清零 poignancy 和 chats。
11. 反思的真正价值要看 thought 是否影响后续计划、对话和关系。

下一章讲模型适配。我们会深入 `LLMModel`、Ollama、OpenAI、MiniMax、Pydantic schema，以及为什么结构化输出是这个项目能稳定运行的关键。

## 参考资料

- Local source: `generative_agents/modules/agent.py`
- Local source: `generative_agents/modules/prompt/scratch.py`
- Local source: `generative_agents/modules/memory/associate.py`
- Local prompts: `generative_agents/data/prompts/reflect_focus.txt`
- Local prompts: `generative_agents/data/prompts/reflect_insights.txt`
- Local prompts: `generative_agents/data/prompts/reflect_chat_planing.txt`
- Local prompts: `generative_agents/data/prompts/reflect_chat_memory.txt`
