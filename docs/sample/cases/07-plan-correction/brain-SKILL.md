# 公告更新后修正旧计划 Brain Skill

读取旧地点并记忆；发布者用 SET_OBJECT_STATE 更新公告；读取者重新交互、supersede 旧记忆并调整行动。

系统约束：遵守当前 Agent 身份；每个 Step 最多提交一次 world-act；不能让自然语言直接改变回放状态。