---
name: generate-chat-check-repeat
description: "检查拟说的话是否重复了对话中已经出现的内容。"
example_input: "agent：简\nconversation：简：马克，你的拿铁好了，请慢用。\n马克：谢谢简！拉花真不错。\n简：不客气，欢迎随时来！\ncontent：不客气，欢迎随时来！"
---

# Generate Chat Check Repeat

## 使用时机

检查拟说的话是否重复了对话中已经出现的内容。

## 说明

将给定的角色、时间、记忆、位置或对话上下文代入下方提示中。严格遵循其返回要求，直接返回领域值或自然语言结论，以便其他 Skill 基于该结果继续。

<!-- PROMPT:START -->
<对话记录>
${conversation}
</对话记录>

<新对话>
${content}
</新对话>

${agent} 在<新对话>中所说的内容，是否在<对话记录>中出现过？只用“是”或“否”回答：
<!-- PROMPT:END -->
