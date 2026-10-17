---
name: action-and-space
description: "测试子技能只提供候选动作而不提交世界变化。"
example_input: "根据当前位置提出下一步行动。"
---

# Action And Space

调用 `@world-perceive` 获取当前位置事实；需要路线时调用 `@world-navigate`。
将候选行动和真实感知结果返回给调用者，由调用者完成动作提交。
