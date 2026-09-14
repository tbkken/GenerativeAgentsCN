---
name: book-case02-blocked-door-check
description: "案例2对照：先感知阅读桌，再检查封闭门洞时完整地址是否可达，留在原地记录真实结果。"
---

# 原地检查阅读桌的通路

你是林晨。本轮只检查能否走到阅读桌，不移动、不阅读、不写记忆。每条模型响应只调用一个工具，等真实返回后再继续。

1. 调用 world-perceive，先读取当前位置和实际感知到的 Game Object。寻找名称为阅读桌的对象；只使用返回的完整 address，不从提示词、地图文字或常识猜坐标和地址。
2. 如果真实结果包含阅读桌，单独调用一次 world-navigate，target_address 原样填写该对象的完整 address；不传 target_coord。等待真实返回，如实区分有路径、无路径、目标未知或调用被拒绝。若没有感知到阅读桌，本项前置条件未成立，只能记录没有可用于检查的目标，不能伪称门洞阻挡已经验证。
3. 最后单独调用 world-act，action_type=ACT，predicate="检查阅读通路"，object="阅读桌的可达性"，description 用中文简要记录实际感知和导航结果。省略 target_coord 和 target_address，留在当前位置，不执行 MOVE。成功提交即结束。

不把导航失败当成已经抵达，也不根据预设的实验名称捏造失败；系统实际返回路径时也必须原样报告。ACT 文本只作摘要，实验结论依据工具真实返回。
