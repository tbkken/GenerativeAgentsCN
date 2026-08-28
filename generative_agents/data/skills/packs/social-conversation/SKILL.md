---
name: social-conversation
description: "判断、推进、结束并记住智能体之间的自然对话。"
example_input: "2026-08-19 09:30，咖啡馆里简正在吧台后做拿铁，马克站在柜台前等单。请判断简是否应该主动发起对话，并推进几轮对话直到自然结束，最后沉淀关系记忆。"
---

# Social Conversation

## 使用时机

当两个 Agent 相遇，需要判断是否交谈、生成下一句话、结束对话或沉淀关系记忆时使用。

## 可调用的 Skills

- `$decide-chat`：判断是否发起交谈。
- `$generate-chat`：生成下一句话。
- `$generate-chat-check-repeat`：避免重复表达。
- `$decide-chat-terminate`：判断对话是否结束。
- `$summarize-relation`：总结双方关系。
- `$reflect-chat-memory`：形成对话记忆。
- `$reflect-chat-planing`：提取需要记住的计划。

## 执行方法

先用 `world-perceive` MCP 确认对方确实可交互。保留完整对话历史作为自然语言上下文，每轮只选择当前需要的一个 Skill；要让话语进入仿真与回放，必须通过 `world-act` MCP 提交 `SPEAK`。对话总结可通过 `memory-stream-append` 保存。

## 返回结果

返回本轮话语、终止判断或需要写入记忆流的总结。
