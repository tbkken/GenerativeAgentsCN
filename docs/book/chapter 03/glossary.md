# 第三章术语速查

| 术语 | 本章含义 | 对应正文 |
| --- | --- | --- |
| 作者资源 | 在 Studio 中按稳定 ID 直接编辑的地图、人物、Skill、模型等内容 | [3.2](02-studio-and-resources.md) |
| Crowd | 组织一组人物选择的资源，不等于新增一种运行期人物 | [3.2](02-studio-and-resources.md) |
| World / Sector / Arena / Game Object | 固定四层空间语义；本例对应青禾社区、学习中心、房间、物品 | [3.3](03-map-and-space.md) |
| Tile | 绘制、碰撞与寻路使用的格子，不是公共感知的语义层级 | [3.3](03-map-and-space.md) |
| Brain | 实验唯一的大脑根 Skill；按 SOP 决定能力调用的目的、条件、次序与停止 | [3.4](04-brain-and-skills.md) |
| atomic / pack / brain | Skill 的固有类型；子技能和对象根技能则是装配后的使用角色 | [3.4](04-brain-and-skills.md) |
| skill_key | 包内引用 Skill 的键；同键异内容必须诊断，不能按名称默默覆盖 | [3.4](04-brain-and-skills.md) |
| 闭包 | 一个根资源运行所需的全部递归依赖与附件 | [3.4](04-brain-and-skills.md) |
| IterationContext | 当前身份、Step、虚拟时间、位置、地址和运行变量等本轮上下文 | [3.5](05-perception-navigation-actions.md) |
| world-perceive | 读取受视野与注意力约束的当前可感知事实 | [3.5](05-perception-navigation-actions.md) |
| world-navigate | 查询到合法已知目标的路线；返回路线不表示已经移动 | [3.5](05-perception-navigation-actions.md) |
| world-act | 世界动作的唯一入口；成功接受动作后还需要整步持久提交 | [3.5](05-perception-navigation-actions.md) |
| MOVE / ACT / WAIT | 分别表达移动、普通活动与真实等待，不能互相替代证明 | [3.5](05-perception-navigation-actions.md) |
| memory-stream | 每名参与者隔离的持久记忆，可追加、检索、替代或失效 | [3.6](06-memory-and-progress.md) |
| SPEAK / conversation_id | 人物对话动作及稳定会话身份；同会话回复延续已有身份 | [3.7](07-conversation-and-objects.md) |
| INTERACT / request_id | 向绑定 Skill 的对象发起交互及内核记录的真实请求身份 | [3.7](07-conversation-and-objects.md) |
| SET_OBJECT_STATE | 按身份与权限校验改变对象状态的动作 | [3.7](07-conversation-and-objects.md) |
| DRAFT / SEALED | 可编辑草稿与不可变封存实验；修改封存内容需建立独立副本 | [3.8](08-experiment-lifecycle.md) |
| Run | 一次运行的业务身份，完整内嵌它使用的实验 | [3.9](09-running-and-cost.md) |
| Attempt | Run 的一次启动或恢复尝试；续跑保持 Run 身份并创建新 Attempt | [3.10](10-pause-resume-rerun.md) |
| StepResult | 一步已提交的动作、事件、对象变化等共同事实来源 | [3.11](11-replay-and-diagnosis.md) |
| Checkpoint | 与已提交边界一致的恢复状态，不能靠页面进度猜测 | [3.10](10-pause-resume-rerun.md) |
| Trace | 过程审计信息；模型想法和中间输出不能直接驱动画面 | [3.11](11-replay-and-diagnosis.md) |
| Evaluator | 评价资源或方法；资源能装配不代表当前运行链已经执行该业务评价 | [3.12](12-quality-and-evaluation.md) |
| Bundle Hash | 对内容计算的包摘要，用于核对行为材料，不是资源业务 Revision | [3.13](13-packages-and-reproduction.md) |
| config / exp / run | ga-package v2 的资源集合、实验与运行三类用途 | [3.13](13-packages-and-reproduction.md) |
| Replay | 只读已提交事实并重建状态的模块，不重新运行 Brain 或调用模型 | [3.14](14-architecture-and-delivery.md) |

这些解释用于快速定位，精确字段、校验与当前实现以正文链接及[参考文件](references.md)为准。

[返回本章](README.md)
