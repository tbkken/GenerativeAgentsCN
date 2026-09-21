---
name: reflect-chat-planing
description: "从对话中提炼智能体应当记住的未来计划。"
example_input: "agent：简\nconversation：马克：简，我下午10点的课别忘了带课本，落在公寓里了。\n简：好！我9点半从咖啡馆过去的时候帮你顺路带上。"
---

# Reflect Chat Planing

## 使用时机

从对话中提炼智能体应当记住的未来计划。

## 说明

将给定的角色、时间、记忆、位置或对话上下文代入下方提示中。严格遵循其返回要求，直接返回领域值或自然语言结论，以便其他 Skill 基于该结果继续。

<!-- PROMPT:START -->
对话记录：
"""
${conversation}
"""

根据以上对话记录，以 ${agent} 的视角，用一句话描述 ${agent} 是否需要记住自己的计划。
<!-- PROMPT:END -->
