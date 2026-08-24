---
name: traffic-signal-state
description: "被动响应行人对交通信号灯状态的查询；只有 Agent 主动请求时才执行。"
example_input: "Agent 林晓准备通过斑马线，主动询问当前行人信号。"
---

# Traffic Signal State

## 使用时机

仅当附近 Agent 主动选择“查询行人信号”交互时运行。不得主动寻找、联系、提醒或指挥任何 Agent。

## 输入

运行时提供请求文本，以及只读的 Agent、Game Object、虚拟时间和当前步骤上下文。

## 返回结果

返回当前行人信号和安全通行信息，作为 Agent 的一条外部观察。对象本身不决定 Agent 的下一步行动。
