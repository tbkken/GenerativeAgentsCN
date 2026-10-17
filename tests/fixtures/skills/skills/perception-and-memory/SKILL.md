---
name: perception-and-memory
description: "测试私有记忆接口的读写和自然语言续接。"
example_input: "记住简今天九点去咖啡馆上班。"
---

# Perception And Memory

用 `@world-perceive` 读取当前位置的事实，用 `@memory-stream-search` 检索已有记忆。
需要保留新事实时调用 `@memory-stream-append`，需要修正时调用 `@memory-stream-supersede`，
失效时调用 `@memory-stream-invalidate`。根据接口实际返回结果说明操作结论。
