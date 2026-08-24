---
name: generate-chat
description: "根据上下文、记忆和对话历史，生成下一句符合角色设定的自然话语。"
example_input: "agent：简\nbase_desc：姓名：简\n年龄：17岁\n先天特质：开朗、喜欢社交，做事认真\n后天特质：在镇上咖啡馆兼职，擅长拉花\nmemory：上周马克说喜欢简做的拉花虎。\naddress：咖啡馆吧台\ncurrent_time：2026-08-19 09:30\nprevious_context：（无）\ncurrent_context：马克站在柜台前等单，想和简聊几句。\nanother：马克\nconversation：马克：简，你今天的拉花比上次好看多了！"
---

# Generate Chat

## 使用时机

根据上下文、记忆和对话历史，生成下一句符合角色设定的自然话语。

## 说明

将给定的角色、时间、记忆、位置或对话上下文代入下方提示中。严格遵循其返回要求，直接返回领域值或自然语言结论，以便其他 Skill 基于该结果继续。

<!-- PROMPT:START -->
以下是对 ${agent} 的简要描述：
${base_desc}

以下是 ${agent} 的记忆：
${memory}

当前位置：${address}
当前时间：${current_time}

${previous_context}${current_context}
${agent} 开始和 ${another} 对话。以下是他们的对话记录：
<对话记录>
${conversation}
</对话记录>

<对话原则>
- ${agent} 不会重复<对话记录>中已有的内容
- 对话内容要符合智能体的性格和当前情境
- 语言自然流畅，符合日常交流习惯
- 长度控制在1-3句话内
- 直接输出 ${agent} 的对话内容，不要补充其他信息
</对话原则>

基于以上<对话记录>和<对话原则>，现在 ${agent} 会对 ${another} 说：
<!-- PROMPT:END -->
