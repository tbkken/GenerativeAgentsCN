---
name: determine-sector
description: "为当前计划选择最合适的世界区域（sector）。"
example_input: "agent：简\nlive_sector：公寓\nlive_arenas：[卧室、厨房、浴室]\ncurrent_sector：公寓\ncurrent_arenas：[卧室]\ndaily_plan：上午在咖啡馆工作，下午去上课\ncomplete_plan：准备出门去上班\ndecomposed_plan：离开公寓前往工作地点\nareas：[公寓、咖啡馆、教室]"
---

# Determine Sector

## 使用时机

为当前计划选择最合适的世界区域（sector）。

## 说明

将给定的角色、时间、记忆、位置或对话上下文代入下方提示中。严格遵循其返回要求，直接返回领域值或自然语言结论，以便其他 Skill 基于该结果继续。

<!-- PROMPT:START -->
在区域选项中，为当前任务选择一个合适的区域。

${agent} 住在 <${live_sector}>，里面有 ${live_arenas}。
${agent} 目前的位置是 <${current_sector}>，里面有 ${current_arenas}。
${daily_plan}
问题：
${agent} 正在 ${complete_plan}。为了 ${decomposed_plan}，${agent} 应该去哪里？

要求：
1. 必须在这个列表中选择一个区域，列表：[${areas}]。
2. 如果现在正位于列表中的区域，并且计划的活动可以在这里进行，最好留在当前区域。
3. 不要选择列表以外的区域。
4. 直接输出选中的结果。

${agent} 应该去：
<!-- PROMPT:END -->
