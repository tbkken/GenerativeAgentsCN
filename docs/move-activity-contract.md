# MOVE 的活动语义与位移事实

MOVE 继续是通用世界动作。公共 `world-act` 的 `predicate` 在 MOVE 中表示本次移动时实施的自然语言活动，例如“骑电动自行车前行”“步行”“推着自行车前行”。它是可选的、最多 240 字的单行短语，不填写地点、坐标、计划或到达宣称；系统不维护交通或移动方式编码字典，也不新增同义输入字段。

`world-act` 校验请求后仍只选择计划动作。只有 World Commit 实际提交位移后，活动才成为该 MOVE 的正式附属信息：

```json
{
  "movement_activity": {
    "text": "骑电动自行车前行",
    "source": "actor_declared"
  }
}
```

`source` 是系统写入的来源元数据，表示活动含义来自行动者选择，不是另一项用户配置。活动可由感知、摄像头 Skill 和回放使用，但不能据此改坐标、路径、碰撞或对象状态，不能自动判定交通违法或任务完成。用户 Skill 仍负责具体业务判断。

内核继续生成 `Event(subject, "移动到", 实际落点地址)`，校验实际坐标、路径端点和地址。结构化 payload 的 `reached_target` 只表示是否抵达**本次 MOVE 明确提交的目标**；途经点抵达不等于整个行程或任务完成。部分路径未走完时该值为 false。模型请求中的 `object`、`description` 保留在 `arguments.requested_*` 供审计，不能替代物理事实。

活动信息进入同一次 World Commit 生成的 Event、Agent 当前状态和 StepResult ActionSnapshot，并与结构化世界事实保持一致。检查点通过 Action/Event 序列化保留它。回放直接读取 StepResult；不读取模型原始文本、审计 `requested_*` 或重新解释记忆。未提供活动时保持空值，默认图标为中性的方向箭头；不擅自宣称步行，也不根据活动词语猜图标。

公开移动描述只包含本次活动和实际落点，不展开计划目标地址。对象 `observed_actions` 从正式事实读取同一个 `movement_activity`，只附带视野内实际执行过的轨迹片段，不返回完整起终点、计划路径、目的地、未裁剪描述或审计参数。即使行动者本步已离开对象视野，可见路径片段仍保留其本次移动活动。注意力与视野限制维持不变。

`world-navigate` 的 `next_coord` 仅是路径第一格，不是整段行程终点。返回的 `requested_target` 只回显调用者已经输入的坐标或地址，不额外暴露地址目标对应的远端落点坐标。要继续前往原目标，Skill 应在 MOVE 中使用相同目标；如果 Skill 明确把第一格作为目标，内核只执行这一格，不暗中换成远端目标。

相关回归见 `tests/runtime/test_move_activity_semantics.py`，覆盖 MCP、真实提交、普通 Agent/对象感知、路径裁剪、序列化恢复、StepResult 校验以及两种回放投影。工程回归不替代主 Agent 重启 Web 后在原实验和原 Run 上的浏览器验收；不改写已提交的历史帧或已封存实验。
