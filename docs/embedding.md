# 使用本地 embedding 模型

项目的记忆检索默认连接本机的 OpenAI 兼容 embedding 接口：

```json
{
  "provider": "openai_compatible",
  "model": "auto",
  "base_url": "http://127.0.0.1:5002",
  "api_key": "unused",
  "timeout": 120,
  "max_retries": 3
}
```

- `model` 为 `auto` 时，项目会读取 `/v1/models` 并使用返回的第一个模型；也可以填写明确的模型 ID。
- `base_url` 可以写服务根地址（如 `http://127.0.0.1:5002`），也可以写到 `/v1`。
- 未启用鉴权的 OpenAI 兼容服务仍需要一个非空密钥占位值，默认使用 `unused`。
- 当前本地服务返回的模型为 `qwen3-embedding-0.6b`，向量维度为 1024。

启动项目前可先确认以下两个接口正常：

```text
GET  http://127.0.0.1:5002/health
GET  http://127.0.0.1:5002/v1/models
POST http://127.0.0.1:5002/v1/embeddings
```

聊天大模型与 embedding 模型相互独立，分别在 `agent.think.llm` 和 `agent.associate.embedding` 中配置。
