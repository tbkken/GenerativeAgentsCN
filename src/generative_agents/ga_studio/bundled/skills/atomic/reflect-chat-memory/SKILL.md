---
name: reflect-chat-memory
description: "以某一智能体的视角，提炼对话中最值得记住的要点。"
example_input: "agent：简\nconversation：马克：简，你今天的拉花比上次好看多了！\n简：哈哈，最近一直在练。这次给你做个新的老虎试试。\n马克：哇，这只老虎比上次的可爱多了！"
---

# Reflect Chat Memory

## 使用时机

以某一智能体的视角，提炼对话中最值得记住的要点。

## 说明

将给定的角色、时间、记忆、位置或对话上下文代入下方提示中。严格遵循其返回要求，直接返回领域值或自然语言结论，以便其他 Skill 基于该结果继续。

<!-- PROMPT:START -->
对话记录：
"""
${conversation}
"""

以 ${agent} 的视角，用一句话描述对话中最有趣的地方。
<!-- PROMPT:END -->
