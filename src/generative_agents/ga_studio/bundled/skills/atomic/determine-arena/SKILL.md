---
name: determine-arena
description: "在已选定的区域内，为当前活动选择最合适的场地（arena）。"
example_input: "agent：简\ntarget_sector：咖啡馆\ntarget_arenas：[吧台、卡座、后厨]\ndaily_plan：上午在咖啡馆工作，下午去上课\ncomplete_plan：准备开始今天上午的排班\ndecomposed_plan：准备为客人制作拿铁"
---

# Determine Arena

## 使用时机

在已选定的区域内，为当前活动选择最合适的场地（arena）。

## 说明

将给定的角色、时间、记忆、位置或对话上下文代入下方提示中。严格遵循其返回要求，直接返回领域值或自然语言结论，以便其他 Skill 基于该结果继续。

<!-- PROMPT:START -->
在区域选项中，为当前任务选择一个合适的区域。

${agent} 正去往 <${target_sector}>，里面有 ${target_arenas}。
${daily_plan}
问题：
${agent} 正在 ${complete_plan}。为了 ${decomposed_plan}，${agent} 应该去 ${target_sector} 里面的哪个区域？

要求：
1. 必须在这个列表中选择一个区域，列表：[${target_arenas}]。
2. 如果现在正位于列表中的区域，并且计划的活动可以在这里进行，最好留在当前区域。
3. 不要选择列表以外的区域。
4. 直接输出选中的结果。

${agent} 应该去：
<!-- PROMPT:END -->
