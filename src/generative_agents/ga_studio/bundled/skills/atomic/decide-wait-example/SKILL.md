---
name: decide-wait-example
description: "为最终的等待决策构建具体的“等待”或“继续”示例。"
example_input: "context：镇上的咖啡馆里，简正在柜台后做拿铁，马克在前台等单。\ndate：2026-08-19 09:30\nstatus：简刚刚把牛奶放进牛奶机开始蒸奶。\nagent：简\nanother_status：马克站在柜台前等着他的拿铁。\nanother：马克\nanother_action：等他的拿铁做好\naction：把拿铁递给马克\nreason：简必须先完成蒸奶，才能把拿铁递给马克。\nanswer：选项A"
---

# Decide Wait Example

## 使用时机

为最终的等待决策构建具体的“等待”或“继续”示例。

## 说明

将给定的角色、时间、记忆、位置或对话上下文代入下方提示中。严格遵循其返回要求，直接返回领域值或自然语言结论，以便其他 Skill 基于该结果继续。

<!-- PROMPT:START -->
背景：
"""
${context}
现在是 ${date}
${status}
${agent} 看到 ${another_status}
"""
问题：一步一步思考，在以下两个选项中，${agent} 应该怎么做？
选项A：等待 ${another} 完成 ${another_action}，然后再 ${action}
选项B：现在继续 ${action}
${reason}${answer}
<!-- PROMPT:END -->
