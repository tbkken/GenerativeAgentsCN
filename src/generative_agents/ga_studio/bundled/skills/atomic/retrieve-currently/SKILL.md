---
name: retrieve-currently
description: "根据记忆中的计划、想法和经过的时间，更新智能体的当前状态。"
example_input: "agent：简\ntime：09:00 至 10:00\ncurrently：简今天上午一直在吧台后忙着做拿铁。\nplan：简计划在上午排班结束后去教室上课。\nthought：简对上午的拉花练习很满意，觉得比赛有希望。\ncurrent_time：10:30"
---

# Retrieve Currently

## 使用时机

根据记忆中的计划、想法和经过的时间，更新智能体的当前状态。

## 说明

将给定的角色、时间、记忆、位置或对话上下文代入下方提示中。严格遵循其返回要求，直接返回领域值或自然语言结论，以便其他 Skill 基于该结果继续。

<!-- PROMPT:START -->
${agent} 在 ${time} 的状态：
${currently}

${agent} 在 ${time} 结束时记得这些事情：
${plan}

${agent} 在 ${time} 结束时的想法和感受：
${thought}

现在是 ${current_time}。根据上述情况，以第三人称，用一句话描述 ${agent} 在 ${current_time} 的状态，以反映 ${agent} 在 ${time} 结束时的想法和感受。
<!-- PROMPT:END -->
