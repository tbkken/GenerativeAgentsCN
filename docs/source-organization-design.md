# 源码组织与模块边界

本设计已落实到 `src/generative_agents/`。业务规则以 [AGENTS.md](../AGENTS.md) 和[文件包架构](capability-composition-platform-design.md)为准；具体入口见[代码导览](code-guide-cn.md)。

```text
src/generative_agents/
├── ga_protocol/
│   ├── schemas/        # 清单、实验、世界、Skill、StepResult 文件合同
│   ├── packages/       # 安全路径、归档、完整性与原子文件原语
│   ├── spatial/        # 空间索引、碰撞、纯导航计算
│   ├── skills/         # Skill 文档解析与依赖闭包
│   └── facts/          # 事实校验、恢复边界与质量格式
├── ga_studio/
│   ├── api.py          # 公开作者操作
│   ├── resources/      # 作者模型、地图、Agent、Skill、模型配置
│   ├── experiments/    # 选择导入、草稿编辑、预检与封存
│   ├── catalog/        # 可重建包索引与目录管理
│   ├── storage/        # 唯一数据库所有者、素材与凭据
│   └── bundled/        # 显式导入素材、可编辑 Skill 种子
├── ga_runtime/
│   ├── api.py          # 文件运行、控制、导出和试运行入口
│   ├── lifecycle/      # Run/Attempt 生命周期及装配
│   ├── engine/         # Scheduler、世界、ActorState、轮次与时间
│   ├── capabilities/   # 身份注入、感知、导航、记忆与动作验证
│   ├── skills/         # Brain、子 Skill、对象与脚本执行
│   ├── memory/         # 文件记忆与参与者空间知识
│   ├── models/         # 模型网关、重试与调用审计
│   ├── storage/        # 帧、提交、检查点、日志与导出
│   └── supervision/    # 进程槽位、控制和健康监督
├── ga_replay/
│   ├── api.py          # 只读公开入口
│   ├── reader.py       # 包与已提交帧读取
│   ├── projections/   # 对象、Agent、对话及页面投影
│   └── cache.py        # 可丢弃的校验与质量缓存
└── adapters/
    ├── cli/            # 参数、输出与退出码
    └── web/            # 唯一 app、按功能划分的 routes 与 static
```

## 依赖规则

| 模块 | 允许依赖的项目模块 | 边界 |
| --- | --- | --- |
| Protocol | 自身 | 无数据库、Web 或模型客户端 |
| Studio | 自身、Protocol | 作者数据库；输出自包含实验或试运行文件 |
| Runtime | 自身、Protocol | 只消费 Run 内嵌实验；独占世界提交与恢复写入 |
| Replay | 自身、Protocol | 只读已提交文件；不执行 Skill 或模型 |
| Adapters | 四模块公开 API、Protocol DTO | 调用服务并展示结果，不直接查询 ORM |

`tools/check_source_boundaries.py` 检查全部源码的直接和传递依赖，包括函数内导入和种子脚本。公开操作由 api.py 明确导出；包初始化不保留旧导入兼容壳。

## 状态、行为和事实

ActorState 保存身份、人物信息、位置、当前动作和剩余路径。Brain Skill 的 SOP 决定能力调用顺序；对象 Skill 用独立身份运行。固定排程、反思提示模板、旧被动对象引擎、SQLite MemoryStream 和重复向量记忆实现已删除。

协议事实类型只在 Protocol 定义；Runtime 构建和提交，Replay 读取和投影。所有世界动作经过 MCP 身份约束与校验，每个参与者每 Step 至多一次 world-act；对象回复进入目标 Agent 下一轮并仅投递一次。

地图几何、四层语义、渲染和碰撞结构归 Protocol；编辑状态及公共素材定位归 Studio。Skill 文本合同不包含数据库资源身份。实验导入复制物理闭包并移除作者元数据；凭据只声明环境变量，不写密钥。

## 分发与验证

维护一个 wheel，依赖按 `runtime`、`studio`、`web`、`dev` 声明。`ga studio serve` 与其余 ga 命令使用同一安装包，不依赖仓库当前目录。源码资源只有 Studio bundled 与 Web static 两个所有者，实验和 Run 继续保留各自物理副本。

门禁覆盖架构、完整 Python/Node 回归、跨平台原生文件系统安全及仓库外 wheel 安装。安装验收通过真实 CLI、Studio、文件协议、MCP、暂停恢复、对象响应和 Replay，外部模型由确定性 HTTP 服务替代。前端资源搬迁另做浏览器检查；具体检查命令见[测试指南](test-strategy.md)。

旧顶层目录、兼容转发、重复实现和无引用演示资源从工作树删除，不建立备份或历史目录。正式用户工作区与案例证据保持独立。
