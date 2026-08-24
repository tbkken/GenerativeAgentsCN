---
name: perception-and-memory
description: "评估观察到的事件，检索相关记忆，并更新智能体的记忆状态。"
example_input: "2026-08-19 10:15，简在吧台后听到马克说：他下个月要去别的城市工作，以后可能来不了咖啡馆了。请判断这件事对简的重要程度，并检索与此相关的记忆。"
---

# Perception And Memory

## 使用时机

当 Agent 看见事件、经历对话、需要回忆相关经历或更新当前状态时使用。

## 可调用的 Skills

- `$poignancy-event`：判断事件的重要程度。
- `$poignancy-chat`：判断对话的重要程度。
- `$retrieve-plan`：从记忆中恢复计划。
- `$retrieve-thought`：从记忆中恢复想法和感受。
- `$retrieve-currently`：把回忆更新为当前状态。
- `$summarize-chats`：把完整对话压缩成长期记忆。

## Scripts 与 MCP

持久化记忆通过公共 `memory-stream` MCP 工具追加和检索。Skill 只传递待存储或待检索的自然语言，不定义业务对象端口。

## 返回结果

返回与当前情境最相关的记忆、重要度或状态更新。
