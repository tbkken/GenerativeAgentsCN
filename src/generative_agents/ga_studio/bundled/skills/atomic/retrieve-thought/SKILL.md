---
name: retrieve-thought
description: "根据检索到的记忆，总结智能体此刻的想法和感受。"
example_input: "agent：简\ndescription：简今天上午一直在吧台后忙着做拿铁，收到了不少客人的夸奖。下午还要去教室考试，课本还落在公寓里。"
---

# Retrieve Thought

## 使用时机

根据检索到的记忆，总结智能体此刻的想法和感受。

## 说明

将给定的角色、时间、记忆、位置或对话上下文代入下方提示中。严格遵循其返回要求，直接返回领域值或自然语言结论，以便其他 Skill 基于该结果继续。

<!-- PROMPT:START -->
"""
${description}
"""

根据以上内容，以 ${agent} 的视角，用一句话总结 ${agent} 此刻的想法和感受：
<!-- PROMPT:END -->
