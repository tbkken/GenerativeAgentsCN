# 第 19 章 模型适配：Ollama、MiniMax、OpenAI 与结构化输出

## 19.1 本章要解决的问题

GenerativeAgentsCN 的智能体行为最终由大语言模型生成。

但项目不是简单调用一个 ChatGPT 接口。

它需要处理：

- 本地 Ollama 模型。
- OpenAI 兼容接口。
- MiniMax 推理模型。
- embedding 模型。
- Pydantic 结构化输出。
- `<think>` 思考过程过滤。
- 失败重试和 failsafe。

这就是模型适配层。

核心源码在：

```text
generative_agents/modules/model/llm_model.py
generative_agents/modules/storage/index.py
generative_agents/modules/prompt/scratch.py
generative_agents/data/config.json
```

本章要回答八个问题：

1. Agent 如何调用模型？
2. `Scratch` 如何定义结构化输出 schema？
3. `LLMModel.completion()` 做了哪些统一处理？
4. Ollama、OpenAI、MiniMax 三种 provider 有何差异？
5. `<think>` 标签为什么要过滤？
6. embedding 模型如何接入记忆系统？
7. 当前配置如何影响运行成本和质量？
8. 模型适配层有哪些风险和升级方向？

[图 19-1：Agent -> Scratch -> LLMModel -> provider 的调用链]

## 19.2 模型调用总链路

一个模型调用通常从 `Agent.completion()` 开始。

例如：

```python
self.completion("wake_up")
```

调用链是：

```text
Agent.completion("wake_up")
  -> Scratch.prompt_wake_up()
  -> Result(prompt, callback, failsafe, return_type)
  -> LLMModel.completion()
  -> provider._completion()
  -> parse_structured_output()
  -> callback
  -> 返回业务结果
```

这里有几个关键角色。

`Agent` 决定要执行哪个认知任务。

`Scratch` 构造 prompt，并定义输出结构。

`LLMModel` 负责重试、统计、失败兜底。

provider 子类负责具体 API 调用。

这种分层很重要。

它让业务逻辑不用关心模型 API 细节。

## 19.3 Result：prompt 调用契约

`Scratch` 中定义：

```python
Result = namedtuple("Result", ["prompt", "callback", "failsafe", "return_type"])
```

每个 `prompt_*` 方法都返回 Result。

四个字段分别是：

- `prompt`：最终发给模型的文本。
- `callback`：模型输出后的业务校验或转换。
- `failsafe`：失败时的兜底结果。
- `return_type`：Pydantic schema。

例如 `prompt_wake_up()` 返回：

```python
class wakeupResponse(BaseModel):
    res: int
```

并提供 callback：

```python
if value > 11:
    value = 11
```

这说明结构化输出不仅靠 prompt，还靠 schema 和 callback 共同约束。

## 19.4 Pydantic schema 的作用

项目大量使用 Pydantic response model。

例如：

```python
class schedule_dailyResponse(BaseModel):
    res: dict[str, str]
```

又如：

```python
class reflect_insightsResponse(BaseModel):
    res: List[Tuple[str, str]]
```

这些 schema 解决一个核心问题：

```text
agent 系统需要的不是漂亮文本，而是可执行数据。
```

起床时间必须是 int。

是否聊天必须是 bool。

日程必须是 dict。

反思洞察必须能解析成列表。

如果模型输出格式不稳定，整个仿真会断。

因此，Pydantic schema 是项目工程化的关键。

## 19.5 LLMModel 基类

`LLMModel` 位于：

```text
generative_agents/modules/model/llm_model.py
```

初始化保存：

```python
self._api_key
self._base_url
self._model
self._summary
self._handle
self._enabled
```

`_summary` 记录调用统计。

格式大致是：

```text
S:<success>,F:<failure>/R:<request>
```

`get_summary()` 会返回每类 caller 的成功失败情况。

这对长时间仿真很重要。

如果某个模型频繁解析失败，可以从 summary 看出来。

## 19.6 LLMModel.completion()

统一调用入口是：

```python
def completion(self, prompt, retry=10, callback=None, failsafe=None, return_type=None, caller="llm_normal", **kwargs)
```

它做几件事。

第一，最多重试 10 次。

第二，调用 `_completion()` 获取模型输出。

第三，如果有 callback，就执行 callback。

第四，如果结果为 None，继续重试。

第五，最终失败时返回 failsafe。

第六，记录成功失败统计。

这让业务层可以比较放心地调用模型。

但也有一个要注意的地方。

failsafe 会让仿真继续运行，但也可能隐藏模型质量问题。

所以实验时不能只看仿真是否跑完，还要看 LLM summary 中失败次数。

## 19.7 OpenAI provider

`OpenAILLMModel` 使用 `magentic.OpenaiChatModel`：

```python
return OpenaiChatModel(self._model, api_key=self._api_key, base_url=self._base_url)
```

然后通过 `@prompt` 装饰器定义返回类型：

```python
@prompt("{_prompt}", model=self._handle)
def response(_prompt: str) -> return_type: ...
output = response(_prompt).res
```

这个 provider 适用于 OpenAI 兼容接口。

README 中也说明，如果要调用其他 OpenAI 兼容 API，可以把 `provider` 改为 `openai`，并配置：

- model。
- api_key。
- base_url。

OpenAI provider 的优势是接口成熟。

风险是成本、网络和供应商差异。

## 19.8 Ollama provider

当前配置默认使用 Ollama。

`OllamaLLMModel` 直接调用：

```text
/api/chat
```

请求参数包括：

```python
{
    "model": self._model,
    "messages": messages,
    "stream": False,
    "think": False,
    "options": {"temperature": temperature}
}
```

如果有 return_type，会生成 JSON schema：

```python
schema = return_type.model_json_schema()
response_format = schema
```

然后放到 Ollama 请求的 `format` 字段：

```python
params["format"] = response_format
```

这让 Ollama 模型尽量输出符合 schema 的 JSON。

对于本地实验来说，这是非常关键的能力。

## 19.9 `<think>` 标签过滤

Ollama 和 MiniMax provider 都会过滤：

```python
re.sub(r"<think>.*?</think>", "", ret, flags=re.DOTALL).strip()
```

原因是一些推理模型会在输出中包含：

```text
<think>...</think>
```

如果不去掉，后续 JSON 解析会失败，或者对话内容会混入思考过程。

对于 agent 系统来说，模型内部思考不应该进入：

- 对话文本。
- 日程。
- 反思 thought。
- 结构化 JSON。

因此过滤 `<think>` 是必要的工程处理。

## 19.10 MiniMax provider

`MiniMaxLLMModel` 适配 MiniMax OpenAI 兼容接口。

README 和源码都说明，MiniMax-M 系列不支持严格的 `json_schema` response format。

因此项目采用折中方案。

如果有 return_type：

```python
schema = return_type.model_json_schema()
prompt = f"{prompt}\n\n请只输出一个符合下面 JSON Schema 的 JSON 对象..."
response_format = {"type": "json_object"}
```

也就是说：

```text
把 schema 写进 prompt
  + 启用 json_object
```

然后再用 `parse_structured_output()` 解析。

这是一种现实工程做法。

不同 provider 对结构化输出支持不同，适配层必须分别处理。

## 19.11 parse_structured_output()

结构化解析函数是：

```python
parse_structured_output(ret, return_type, context)
```

逻辑是：

1. 如果没有 return_type，直接返回原始文本。
2. 尝试 `json.loads(ret)`。
3. 如果失败，用正则从文本中提取 `{...}`。
4. 再尝试解析。
5. 如果 parsed 不是 dict 或没有 `res`，包成 `{"res": parsed}`。
6. 用 Pydantic 校验并返回 `.res`。
7. 如果都失败，返回原始文本。

这个函数增强了容错性。

模型多输出一点解释，仍然可能被解析。

但也有风险。

如果解析失败返回原始文本，而业务 callback 预期的是 list 或 dict，就可能在 callback 阶段失败，并触发重试或 failsafe。

所以结构化输出稳定性仍然取决于模型能力和 prompt 约束。

## 19.12 create_llm_model()

provider 创建入口是：

```python
create_llm_model(llm_config)
```

支持：

```python
if provider == "ollama":
    return OllamaLLMModel(llm_config)
elif provider == "minimax":
    return MiniMaxLLMModel(llm_config)
elif provider == "openai":
    return OpenAILLMModel(llm_config)
```

不支持的 provider 会抛：

```python
NotImplementedError
```

这让新增 provider 比较清晰。

如果要增加 Qwen API、DeepSeek API、Azure OpenAI、Claude，需要新增对应子类或复用 OpenAI 兼容接口。

## 19.13 embedding provider

记忆检索还需要 embedding。

embedding 由 `storage/index.py` 中的 `LlamaIndex` 初始化。

支持：

- `hugging_face`
- `ollama`
- `openai`

当前配置使用 Ollama embedding：

```json
"embedding": {
    "provider": "ollama",
    "model": "qwen3-embedding:0.6b-q8_0",
    "base_url": "http://127.0.0.1:11434"
}
```

如果使用 MiniMax LLM，因为 MiniMax OpenAI 兼容接口不提供 embedding，README 建议 embedding 使用 HuggingFace 本地模型。

这说明 LLM provider 和 embedding provider 可以分离。

模型负责生成行为。

embedding 负责记忆检索。

两者不一定来自同一供应商。

## 19.14 OllamaHttpEmbedding

项目自己实现了 `OllamaHttpEmbedding`。

它调用：

```text
/api/embed
```

请求：

```python
{
    "model": self.model_name,
    "input": text
}
```

然后返回：

```python
response.json()["embeddings"]
```

这个类让 LlamaIndex 可以使用 Ollama 本地 embedding。

这对完全本地部署很关键。

如果 LLM 本地化，但 embedding 还走远端 API，成本和隐私仍然没有完全解决。

## 19.15 当前配置与 README 的关系

README 记录了项目更新，例如默认语言模型和 embedding 模型的变化。

当前工作区实际配置以：

```text
generative_agents/data/config.json
```

为准。

在当前文件中，LLM model 是：

```text
qwen3.5:4b-q4_K_M
```

embedding model 是：

```text
qwen3-embedding:0.6b-q8_0
```

读者运行时应检查自己的 `config.json`，不要只看 README 描述。

开源项目在 fork、PR 和本地修改后，README 与配置可能存在时间差。

本书源码分析以当前工作区文件为准。

## 19.16 模型质量对仿真的影响

模型质量会影响几乎所有模块。

日程生成需要模型能输出合理 24 小时表。

对话生成需要模型能结合角色、场景和记忆。

反思需要模型能从证据中生成不过度推断的 insight。

结构化输出需要模型遵守 schema。

重要性评分需要模型稳定给 1 到 10 的整数。

如果模型太弱，可能出现：

- JSON 格式错误。
- 日程重复。
- 对话复读。
- 角色语气不一致。
- 反思泛泛而谈。
- 派对时间地点丢失。
- 对话过度礼貌。

因此，本地小模型能降低成本，但不一定保证质量。

实验时要记录模型名称和量化版本。

## 19.17 成本与速度

多智能体仿真模型调用很多。

每个 agent 可能调用：

- wake_up。
- schedule_init。
- schedule_daily。
- schedule_decompose。
- poignancy_event。
- decide_chat。
- generate_chat。
- summarize_chats。
- reflect_focus。
- reflect_insights。

25 个 agent 运行多个 step，调用量会迅速增长。

本地模型的优势是成本低。

缺点是速度受硬件限制。

远端大模型的优势是质量和速度可能更稳定。

缺点是成本和网络。

读者做实验时可以采用策略：

```text
小模型 + 少量 agent 调试
大模型 + 关键实验验证
```

## 19.18 失败与 failsafe

每个 prompt 通常有 failsafe。

例如 wake_up 失败时返回 8。

schedule_init 失败时返回默认日程。

decide_chat 失败时返回 False。

failsafe 保证仿真继续。

但它也会改变行为。

如果 decide_chat 经常失败返回 False，小镇会变得不社交。

如果 schedule_daily 失败返回默认读书日程，角色个性会消失。

所以评估模型时，要关注：

- LLM summary 中失败数。
- 日志中 `LLMModel.completion()` 错误。
- 结果中是否出现大量 failsafe 行为。

## 19.19 模型适配的边界

当前模型适配层有几个边界。

第一，provider 数量有限。

只内置 Ollama、MiniMax、OpenAI。

第二，并发能力有限。

当前 agent 按顺序调用模型，没有异步并发。

第三，schema 支持依赖 provider。

Ollama 和 MiniMax 的结构化输出处理不同，其他 provider 可能还要适配。

第四，parse_structured_output 容错有限。

复杂错误 JSON 仍然可能解析失败。

第五，模型输出质量不做二次评审。

例如反思是否被证据支持，当前没有自动 judge。

这些边界是后续升级方向。

## 19.20 可改进方向

模型层可以从六个方向升级。

第一，增加 provider 抽象。

统一 OpenAI compatible、Ollama native、Anthropic、DeepSeek、Qwen API 等。

第二，引入异步并发。

在不破坏仿真顺序的前提下并发非相互依赖调用。

第三，引入模型路由。

简单任务用小模型，反思和对话用强模型。

第四，引入输出修复。

结构化解析失败时，用专门 repair prompt 修 JSON。

第五，引入质量评审。

对反思、对话摘要、计划进行自动校验。

第六，引入缓存。

对重复 prompt 或低风险 prompt 做缓存，降低成本。

这些升级会让 GenerativeAgentsCN 更适合长期实验。

## 19.21 本章小结

本章讲清了 GenerativeAgentsCN 的模型适配：

1. 模型调用链路是 `Agent.completion()` -> `Scratch.prompt_*()` -> `LLMModel.completion()` -> provider。
2. `Result` 包含 prompt、callback、failsafe、return_type。
3. Pydantic schema 是结构化输出的核心。
4. `LLMModel.completion()` 负责 retry、callback、failsafe 和统计。
5. OpenAI provider 使用 magentic 的 OpenAI chat model。
6. Ollama provider 调用 `/api/chat`，并通过 `format` 传 JSON schema。
7. MiniMax provider 把 JSON schema 写进 prompt，并启用 `json_object`。
8. Ollama 和 MiniMax provider 都会过滤 `<think>` 标签。
9. `parse_structured_output()` 负责解析 JSON、提取 JSON 片段并用 Pydantic 校验。
10. embedding provider 与 LLM provider 分离，支持 HuggingFace、Ollama、OpenAI。
11. 模型质量会直接影响日程、对话、反思、检索和评价。

下一章讲回放系统。我们会看 checkpoint 如何压缩成 `movement.json` 和 `simulation.md`，以及 Phaser 前端如何展示小镇。

## 参考资料

- Local source: `generative_agents/modules/model/llm_model.py`
- Local source: `generative_agents/modules/storage/index.py`
- Local source: `generative_agents/modules/prompt/scratch.py`
- Local config: `generative_agents/data/config.json`
- Local docs: `docs/ollama.md`
- Local README: `README.md`
