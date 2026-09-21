---
name: summarize-relation
description: "根据共同上下文，总结两个智能体之间的关系。"
example_input: "agent：简\nanother：马克\ncontext：马克几乎每天早上都来咖啡馆点拿铁，简记得他喜欢多加一份奶泡。上次马克赶时间忘了带伞，简追到街上把伞递给了他。"
---

# Summarize Relation

## 使用时机

根据共同上下文，总结两个智能体之间的关系。

## 说明

将给定的角色、时间、记忆、位置或对话上下文代入下方提示中。严格遵循其返回要求，直接返回领域值或自然语言结论，以便其他 Skill 基于该结果继续。

<!-- PROMPT:START -->
背景描述：
"""
${context}
"""

输出示例1：乔和汤姆是朋友
输出示例2：艾琳和约翰在玩游戏

参考上述背景描述和输出示例，用一句话总结 ${agent} 和 ${another} 之间的关系：
<!-- PROMPT:END -->
