# 使用本地 vLLM 模型

项目的聊天大模型默认连接本机的 vLLM OpenAI 兼容接口：

```json
{
  "provider": "vllm",
  "model": "auto",
  "base_url": "http://127.0.0.1:5001",
  "api_key": "",
  "timeout": 300,
  "max_tokens": 2048,
  "enable_thinking": false
}
```

- `model` 为 `auto` 时，项目会读取 `/v1/models` 并使用返回的第一个模型；也可以填写明确的模型 ID。
- `base_url` 可以写服务根地址（如 `http://127.0.0.1:5001`），也可以写到 `/v1`。
- `enable_thinking` 会作为 Qwen 的 `chat_template_kwargs.enable_thinking` 发送。默认关闭，避免思考内容干扰 JSON 结构化输出。
- 本地服务如启用了鉴权，请在 `api_key` 中填写密钥。

启动项目前可先确认以下两个接口正常：

```text
GET  http://127.0.0.1:5001/health
GET  http://127.0.0.1:5001/v1/models
```

聊天大模型与记忆检索使用的 embedding 模型相互独立。embedding 配置见 [embedding.md](embedding.md)。
