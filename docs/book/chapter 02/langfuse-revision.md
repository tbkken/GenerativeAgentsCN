# Langfuse可观测性专题增补核查

日期：2026-09-26。保留第二章原编号，将2.13扩为“可观测性、评估、可靠性与成本”；在2.13.7增加Langfuse实操，原章末练习移到2.13.8。此记录只覆盖新增内容，原2026-09-25核查与先前图解检查仍保留各自日期和范围。

## 交付内容

| 文件或入口 | 增补 |
| --- | --- |
| [2.13.7正文](13-evaluation-and-reliability.md#langfuse-practice) | 日志／Trace／指标／评价，Observation与Session，SDK接入、页面读图、评分、成本、WorkBuddy范围和数据边界 |
| [langfuse_trace.py](examples/langfuse_trace.py) | 默认离线fixture，显式`--live`才请求模型并导出遥测；一次生成、独立校验、新目录保存 |
| [可选依赖](examples/requirements-langfuse.txt) | Langfuse v4与OpenAI客户端，独立于产品依赖 |
| [示例运行说明](examples/README.md#langfuse) | 环境、参数、产物、退出码和实际验证 |
| [新增测试](examples/tests/test_langfuse.py) | 离线反例、失败保留、缺配置早停、Session隔离与真实SDK合同 |
| [配图索引](figures/README.md) | 3组新增Mermaid＋对应PNG/SVG静态参考 |
| [术语](glossary.md)、[练习记录](practice-record.md)、[参考资料](references.md) | 可观测概念、Trace核对字段及当前官方来源 |

2.14增加可观测性选择与交付关联，全书导航和图数同步。本章当前39个图号：38组Mermaid与静态参考、1幅独立输入图片；全书54篇正文共156个图号、146个Mermaid块。原153幅图片继续保留，新增3幅图标为“静态参考（本次新增图）”，没有虚构转换前的原图。

## 程序检查

| 检查 | 实际结果 |
| --- | --- |
| 核验环境 | Python 3.13.9、Langfuse 4.15.6、OpenAI 2.54.0；使用临时虚拟环境 |
| 新增测试 | 12项全部通过，包含11项行为检查与1项真实SDK合同检查 |
| 第二章全部示例回归 | 47项中46项通过，1项因未安装MCP v2依赖明确跳过；跳过不算通过 |
| 默认CLI | 实际读取人工有效fixture，退出0，Trace ID为空且不构造SDK |
| 无效fixture CLI | 退出1，保留失败候选与校验报告，不生成成功schedule |
| 缺少配置的真实模式 | 退出2，在构造SDK、创建输出目录和网络请求前停止 |
| 不完整响应与异常 | 保留已收到的模型响应；未完成校验时评分为null，不伪造通过 |
| Session与尝试 | 可用同一`--session-id`分组；每次`example_id`、输出目录及真实Trace仍独立 |
| SDK嵌套 | 实际SDK＋HTTP MockTransport＋内存OTel exporter；唯一generation的parent是根span，根与子span的Session／metadata一致 |
| 请求边界 | Langfuse专属参数未透传给OpenAI请求体；模型SDK重试为0；合同测试禁止socket连接 |
| 遥测与业务 | `create_score`使用`business_valid` BOOLEAN；score／flush异常不会将已经测得的业务结果改写为另一结果 |

真实SDK合同测试使用人工HTTP响应与内存导出器；其中Token、响应ID和模型名都是测试输入，不是测得的模型能力、延迟或消耗。

`execution.json`记录实际SDK版本、材料／Schema／程序摘要、本次标识、阶段、产物摘要和遥测状态。`server_delivery_verified`固定为false，因为脚本没有查询服务端确认数据入库；`flush_returned`仅表示该方法正常返回。

## 图形检查

3图采用正文内Mermaid作为唯一编辑源，以Mermaid 11.12.0＋Chrome实际渲染并逐张查看。三幅均无语法错误、文字越出画布或明显遮挡。

| 图 | 渲染尺寸 | 表达边界 |
| --- | --- | --- |
| 2.13-4 Trace层级 | 723×433 | Session分组、Trace内父子关系、评分关联；不表示耗时或必经业务流程 |
| 2.13-5 两条路径 | 546×618 | 业务处理与遥测导出分开；Langfuse不代替应用执行模型请求 |
| 2.13-6 WorkBuddy范围 | 508×570 | 仅观察埋点脚本；宿主内部模型与积分标为本例未观测 |

新增PNG与SVG来自这些当前图源。原143幅Mermaid和原非Mermaid教学代码围栏保持不变；全书当前图源、静态参考与标识配对已检查，正文每个新增图均紧接同名PNG。

## 尚未执行

- 没有请求真实模型、上传Langfuse观测记录或验收远端项目页面。
- 没有配置／运行WorkBuddy客户端，没有验证原生Langfuse集成。
- 没有创建GenerativeAgentsCN实验、Run或回放事实，也没有改变产品依赖。
- 没有完成生产自部署、权限体系、保留策略或最终出版排版。

正式练习应分别核对业务结果、遥测接收和页面展示。仿真事实仍来自Run文件、StepResult与已提交帧；本节不让外部追踪服务成为恢复或Replay的依赖。

[返回第二章](README.md) · [进入实操](13-evaluation-and-reliability.md#langfuse-practice)
