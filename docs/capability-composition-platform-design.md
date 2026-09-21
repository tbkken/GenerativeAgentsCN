# 可移植实验包与文件化 Run 架构

状态：实施基线
设计版本：3.0
日期：2026-09-03

## 1. 核心结论

实验不是数据库中的一组资源关联，而是一份可以独立运行的完整文件包。Studio 中的地图、空间素材、Agent、Crowd、Brain Skill、子 Skill、Game Object Skill、模型预设和评估器只是方便复用的作者资源；用户把它们加入实验时，Studio 立即把当时的完整内容和依赖闭包物理复制到实验工作目录。公共 Agent 只有用户创建这一种来源，不提供系统级、内置或只读 Agent 目录。

复制完成后，实验不再依赖公共资源：

- 公共资源继续修改、改名、归档或删除，不影响已有实验；
- 实验中的副本可以单独修改，不反向影响公共资源；
- 不存在“跟随最新”“锁定 Revision”“发布时再解析资源”或升级映射；
- 行为身份由整个实验包的内容哈希确定，不由资源版本字段确定；
- 不启动 Studio、不连接数据库、甚至手写同样的目录，也能校验和运行实验。

目录是可编辑工作形态，`.gaexp` 是同一内容的确定性 ZIP 交换形态。二者遵守同一协议。

## 2. 四个 Module

```text
ga_studio  ──写出──>  .gaexp / 实验目录
                           │
                           v
ga_runtime ──写出──>  .garun / Run 目录
                           │
                           v
ga_replay  ──读取──>  StepResult / Frame / Checkpoint

三者共同依赖：ga_protocol
```

| Module | 职责 | 可以依赖数据库 | 业务输入/输出 |
| --- | --- | --- | --- |
| `ga_protocol` | 清单 Schema、安全路径、规范 JSON、哈希、完整性校验、安全 ZIP | 否 | 目录或归档 |
| `ga_studio` | 公共作者资源、实验编辑、包构建、Web 导航目录 | 是，且是唯一允许者 | 写 `.gaexp`，索引 `.garun` |
| `ga_runtime` | 新跑、续跑、重跑、监督、检查点、文件状态 | 否 | 读 `.gaexp`/`.garun`，写 Run 目录 |
| `ga_replay` | 读取已提交事实、时间线、状态归约、Web 回放投影 | 否 | 只读 Run 目录或 `.garun` |

Module 之间不调用对方数据库、ORM、Repository 或业务 Service。`ga_runtime` 和 `ga_replay` 被复制到没有 Studio 的环境后仍须可用。

产品源码位于 `src/generative_agents/`，按四模块及 adapters 组织；具体归属与依赖门禁见[源码组织](source-organization-design.md)。

## 3. 身份与关联

身份写在清单里，绝不从目录名或压缩包名推断：

- `experiment_id`：实验内容容器的稳定 UUID；
- `run_id`：一次从头执行的稳定 UUID；
- `attempt_id`：同一 Run 的某次启动或续跑尝试 UUID；
- Replay 没有独立业务 ID，Replay 的身份就是 `run_id`。

目录名和归档名只用于展示，可以任意改名。Studio 数据库可以保存 `ID -> package_location` 以便 Web 导航，但这只是可丢弃、可扫描重建的目录索引。发生冲突时，包内清单是事实来源。

关系规则：

- 新跑：一个 `experiment_id` 产生一个新的 `run_id` 和第一个 `attempt_id`；
- 续跑：保持 `run_id`，从最新完整检查点创建新的 `attempt_id`；
- 重跑：读取 Run 内嵌实验，创建新的 `run_id`；新 Run 可记录 `origin_run_id` 作为来源信息；
- 回放：直接读取目标 `run_id` 包中的帧，不创建 Replay 记录或映射表。

## 4. `.gaexp` 实验协议

推荐目录：

```text
my-experiment/
├── manifest.json
├── integrity/sha256.json
├── world/world.json
├── world/semantic-index.json
├── agents/index.json
├── skills/
│   ├── registry.json
│   └── items/
│       ├── s0001/SKILL.md
│       ├── s0001/scripts/...
│       └── s0002/SKILL.md
├── models/models.json
├── runtime/simulation.json
├── runtime/engine.json
├── evaluation/evaluators.json
└── assets/...
```

`manifest.json` 只保存协议身份、`experiment_id`、展示元数据和入口文件路径。入口文件路径必须是安全的包内相对 POSIX 路径。

`skills/registry.json` 指定唯一 Brain、全部 Game Object Skill 根（`object_roots`，包内 kind 为 `object`）以及包内完整依赖图。每个 Skill 的 `SKILL.md`、脚本和相关内容都在包内；缺少任一依赖即为无效实验。Brain 是自然语言 SOP，决定调用哪些子 Skill、顺序、条件和停止方式，系统不重新固化成感知、计划、反思流水线。

`world/world.json` 包含完整四层地图 `World → Sector → Arena → Game Object`、空间语义、对象初态和资源包内路径。实验运行配置中禁止出现 `map_id`、`map_snapshot_hash`、`revision_id`、`brain_revision_id`、`skill_revision_id` 或 `secret_ref` 等外部活引用。

`world/semantic-index.json` 是构建实验时生成的包内四层节点与坐标索引。节点按稳定 ID 唯一化，Runtime 的 `world-perceive` 与 Replay 读取同一份索引；Tile 不承担公共感知合同，也不在每轮重复拼装语义树。

模型密钥不是实验行为内容，也不能复制进包。模型配置只声明运行环境变量名，例如 `GA_CHAT_API_KEY`；Runtime 在目标机器注入密钥。

`integrity/sha256.json` 列出清单和所有物理文件的 SHA-256，并计算排序后的包根哈希。包根哈希覆盖任何会改变行为的内容，包括地图、Agent、Skill Markdown、脚本、模型参数和资源文件。

## 5. `.garun` Run 协议

运行中的 Run 必须是可写目录，ZIP 不能被原地安全修改：

```text
one-run/
├── run.json
├── status.json
├── control.json
├── experiment/                 # 完整嵌入的 .gaexp 目录内容
├── attempts/<attempt_id>/
│   ├── attempt.json
│   ├── storage/...
│   └── runtime-storage/...
├── frames/step-000001.json.gz
├── checkpoints/
│   ├── LATEST
│   └── step-000001/...
├── traces/...
├── artifacts/...
└── projection.json
```

`run.json` 创建后不可变，包含 `run_id`、请求步数、内嵌 `experiment_id`、内嵌实验根哈希和可选来源 Run。`status.json` 是原子替换的可变状态投影，包含运行状态、当前 Attempt 和最后提交 Step。`attempt.json` 记录每次启动的边界与结局。

Run 状态明确区分 `CREATED`、`QUEUED`、`RUNNING`、`FINALIZING`、`PAUSED`、`CANCELLED`、`COMPLETED` 与 `FAILED`。最后一个 Step 提交后先进入 `FINALIZING`，质量报告与最终产物完成后才进入 `COMPLETED`。

Run 在暂停、取消、完成或显式导出时，可以复制到一致性暂存目录，生成完整性清单并封装为 `.garun`。续跑 `.garun` 时必须：

1. 安全解压到新的可写目录；
2. 校验 Run 与内嵌实验完整性；
3. 获取 `worker.lock`；
4. 查找最后完整检查点及相同的已提交帧边界；
5. 新建 `attempt_id`；
6. 从下一 Step 继续，保持 `run_id` 不变。

检查点、帧、记忆、对话和对象变化在 Step 边界保持幂等。不能生成零进展 Attempt，也不能重复已提交副作用。

## 6. 执行与事实边界

Scheduler 从 1 到请求步数确定性推进虚拟时间。每轮构造共享的 `IterationContext`，包含 Run、Attempt、Agent、Step、总步数、带时区虚拟时间、坐标、四层地址、空间语义和运行变量。

Brain 可以多次调用只读感知与 Agent 私有记忆，但每个 Agent 每 Step 最多提交一次 `world-act`。动作原语保持通用：`MOVE`、`ACT`、`WAIT`、`SPEAK`、`INTERACT`、`SET_OBJECT_STATE`。Game Object 绑定一个根 Skill 后默认每轮自主运行并响应交互；根 Skill 使用大模型和公共 MCP，不要求脚本或触发器配置。对象有独立身份与隔离记忆，每轮最多提交一次 world-act。ACT、WAIT 和自身 SET_OBJECT_STATE 及附带的请求回复经 World Commit 保存。详见 [对象 Skill 运行合同](game-object-skill-runtime.md)。

每条已提交世界变化同时包含：

```text
Event(subject, predicate, object)
structured_payload: 非空、足以确定性归约的对象
```

StepResult 是帧、检查点、查询投影、质量报告和回放的共同事实来源。Skill Thought、计划、反思和原始 LLM 文本只进入 Trace，不能直接驱动画面。

MOVE 的内核位移事实与自然语言移动活动分别保存：`Event(subject, "移动到", 实际地址)` 和实际路径由内核保证；公共 MCP 的活动 predicate 经实际提交后成为正式 `movement_activity`，供当前状态、感知与回放共同读取。活动文字不能代替实际抵达判断，也不形成业务活动字典。详见 [MOVE 活动与位移合同](move-activity-contract.md)。

运行记忆也是 Run 内普通文件，不使用 Studio 数据库。记忆可以被替代或失效并保留历史，但回放不重新解释记忆文本。

## 7. Replay

Replay 接受活动 Run 目录或封存 `.garun`，完成以下只读操作：

- 校验 `run_id`、内嵌实验哈希、帧连续性和帧哈希；
- 读取内嵌地图和渲染资源；
- 按 Step 返回 Agent 状态、Event、Effect、对话和对象状态变化；
- 从已提交事实归约任意 Step 的 Web 状态；
- 按 `L1 World → L2 Sector → L3 Arena → L4 Game Object` 绘制。

Replay 不加载 Brain，不调用模型，不读取 Studio 数据库，也不从记忆或 LLM 文本猜测世界状态。Web 回放页面只是 `ga_replay` 输出的一个客户端。

## 8. Studio 数据库的严格范围

Studio 数据库只保存：

- 可变公共作者资源及编辑状态；
- Web 工作区、权限和展示设置；
- `experiment_id/run_id -> package_location` 可重建导航目录；
- 可选的包摘要缓存。

它不保存 Runtime 或 Replay 必须读取的唯一事实。删除 Studio 数据库后，现有 `.gaexp` 和 `.garun` 仍可运行、续跑、重跑和回放；重新扫描清单即可恢复 Web 导航目录。

## 9. 安全与原子性

- ZIP 解压拒绝绝对路径、`..`、重复成员、符号链接、超量文件和超量展开体积；
- 目录包拒绝符号链接，防止内容逃逸；
- JSON 使用 UTF-8、排序键和规范分隔符；
- 清单、状态、控制和记忆文件通过同目录临时文件、`fsync` 与原子替换提交；
- 帧先落盘，检查点随后发布，最后推进可见投影和 `status.committed_step`；
- `.gaexp` 与 `.garun` 使用排序成员和固定 ZIP 时间戳，保证同内容得到相同归档字节；
- 活动 Run 使用文件锁串行化执行和封存。

## 10. 命令行合同

不启动 Web 即可使用：

```text
ga experiment validate <目录或.gaexp>
ga experiment seal <目录> <输出.gaexp>

ga run create <实验包> <Run目录> [--steps N]
ga run start <实验包> <Run目录> [--steps N]
ga run resume <Run目录>
ga run resume <输入.garun> --destination <可写目录>
ga run rerun <Run目录或.garun> <新Run目录> [--steps N]
ga run pause|cancel <Run目录>
ga run seal <Run目录> <输出.garun>

ga replay summary <Run目录或.garun>
ga replay timeline <Run目录或.garun>
ga replay state <Run目录或.garun> <Step>
```

这些命令与 Web 使用同一协议和实现。Studio 不是另一个运行事实源，只是更方便的实验 IDE。
