# 第 12 章 把 Generative Agents 跑起来

Generative Agents 是一个可以启动、观察和复盘的项目。第一次运行不要直接开 25 个智能体，先跑一个最小仿真，确认启动、模型调用、checkpoint、压缩和回放链路全部打通，有了手感之后，再把 25 个智能体启动起来。

推荐路径如下：

```mermaid
flowchart LR
    Repo["进入项目"] --> Example["先看内置回放"]
    Example --> Config["确认模型配置"]
    Config --> Run["运行最小仿真"]
    Run --> Compress["压缩结果"]
    Compress --> Replay["浏览器回放"]
    Replay --> Read["阅读 simulation.md"]
    Read --> Next["再进入功能体验"]
```

*图 12-1：第一次运行 Generative Agents 的推荐路径。先看现成结果，再跑小规模实验，最后再进入源码和功能拆解。*

## 12.1 先看一个现成回放

当前仓库已经包含一个示例回放：

```text
generative_agents/results/compressed/example/
```

这个目录里有两个最重要的文件：

| 文件 | 用途 | 重点观察 |
| --- | --- | --- |
| `movement.json` | 给前端回放使用的数据 | 角色位置、动作、对话、回放帧 |
| `simulation.md` | 给人阅读的仿真时间线 | 每个时间点谁在哪里、做了什么、说了什么 |

先进入项目运行目录：

```bash
cd generative_agents
```

后续命令都在 `generative_agents/` 目录下执行。本机如果没有全局 `python` 命令，先激活项目虚拟环境，或者把下面命令中的 `python` 替换成 `../.venv/bin/python`。

先启动本地回放服务：

```bash
python replay.py
```

然后在浏览器中打开示例回放地址：

```text
http://127.0.0.1:5000/?name=example&step=0&speed=2&zoom=0.6
```

![图 12-2：内置 example 回放页面](../../assets/chapter_12/fig-12-2-example-replay.png)

*图 12-2：内置 `example` 回放页面。页面已经加载出小镇地图、顶部控制按钮、对话记录和底部 25 个角色头像。*

这个页面展示的是 Phaser 前端回放。页面会呈现小镇地图、角色位置、移动路径和对话气泡。这里先建立一个直观印象：Generative Agents 不是聊天窗口，而是一个会随时间推进的虚拟小镇。

这张截图可以这样读：

| 界面区域 | 看到什么 | 说明什么 |
| --- | --- | --- |
| 顶部控制条 | 运行、暂停、显示对话、隐藏对话、当前小镇时间 | 回放不是静态图片，而是按仿真时间推进的动画 |
| 左上对话记录 | 角色之间的对话内容和地点 | 对话被压缩进回放数据，可以在前端展示 |
| 中央地图 | 房间、道路、树木、角色位置和行动标签 | 行动已经落到具体空间，不只是生成一句文本 |
| 底部角色栏 | 25 个角色头像和姓名 | `example` 是完整小镇回放，不是两三个角色的测试样例 |

同一份结果还可以直接读 Markdown：

```text
generative_agents/results/compressed/example/simulation.md
```

浏览器回放适合观察空间和移动，`simulation.md` 适合观察行为叙事和对话内容。两者合起来，构成最适合上手的入口。

## 12.2 确认环境和模型配置

看完现成回放后，再准备运行自己的小实验。项目的模型配置在：

```text
generative_agents/data/config.json
```

当前配置的核心结构如下：

| 配置项 | 当前含义 | 对运行的影响 |
| --- | --- | --- |
| `agent.think.llm.provider` | 思考模型 provider，当前是 `minimax` | 决定日程、对话、反思等 LLM 调用走哪里 |
| `agent.think.llm.model` | 思考模型名称，当前是 `MiniMax-M3` | 决定生成质量、速度和结构化输出稳定性 |
| `agent.think.llm.base_url` | MiniMax 或 OpenAI 兼容接口地址 | 地址不通时，仿真会在模型调用阶段失败 |
| `agent.associate.embedding.provider` | 向量嵌入 provider，当前是 `minimax` | 决定记忆检索用什么 embedding |
| `agent.associate.embedding.model` | 嵌入模型名称，当前是 `embo-01` | 决定 memory stream 检索能否运行 |
| `agent.percept.vision_r` | 角色视野半径 | 决定角色能看到附近多大范围 |
| `agent.percept.att_bandwidth` | 感知带宽 | 决定每步最多处理多少附近事件 |
| `agent.think.poignancy_max` | 反思触发阈值 | 重要性累计到阈值后才会触发 reflection |

本次实跑使用 `MiniMax-M3` 作为 LLM，使用 `embo-01` 作为 embedding。`config.json` 中的 `api_key` 可以留空，运行时通过 `MINIMAX_API_KEY` 环境变量传入密钥；如果把密钥写进 `config.json`，也能运行，但不要把带密钥的配置提交到公开仓库。

常见模型配置方式可以这样判断：

| 场景 | 配置方式 | 适合情况 |
| --- | --- | --- |
| 本机有 Ollama | `provider: "ollama"`，`base_url` 指向本机服务 | 想低成本反复实验 |
| 使用 OpenAI 兼容接口 | `provider: "openai"`，填写 `base_url`、`api_key`、`model` | 想用云端模型获得更稳定输出 |
| 使用 MiniMax | `provider: "minimax"` | 想测试 MiniMax-M 系列模型，并接受 provider 特殊处理 |

模型配置不能一笔带过。这个项目的大部分行为都由 LLM 调用驱动：起床时间、日程生成、地点选择、对话、重要性评分、反思、结构化输出都和模型有关。模型没配好，看到的不是“小镇效果差”，而是系统链路还没跑通。

## 12.3 运行一个最小仿真

第一次自己跑，不要直接开 25 个角色。最小实验只需要 2 个角色、2 个 step，就能验证启动、模型调用、checkpoint 写入和压缩回放链路是否正常。

```bash
python start.py \
  --name book-smoke \
  --start "20240213-09:30" \
  --step 2 \
  --stride 10 \
  --agent-count 2 \
  --verbose info
```

这条命令里的参数含义如下：

| 参数 | 含义 | 建议 |
| --- | --- | --- |
| `--name book-smoke` | 本次仿真的名称 | 名称不能和已有结果重复 |
| `--start "20240213-09:30"` | 小镇起始时间 | 使用固定时间方便复现实验 |
| `--step 2` | 运行 2 个仿真步 | 第一次只验证链路，不追求故事丰富 |
| `--stride 10` | 每一步推进 10 分钟 | 2 步会覆盖 20 分钟小镇时间 |
| `--agent-count 2` | 只取前 2 个角色运行 | 降低模型调用成本和等待时间 |
| `--verbose info` | 日志级别 | 避免 debug 日志干扰第一次排查 |

本次实际执行后，控制台先打印两个角色的初始化状态，然后进入两个仿真 step。日志中可以看到阿伊莎和克劳斯分别触发 `wake_up`、`schedule_init`、`schedule_daily`、`schedule_decompose`、`determine_sector`、`determine_arena`、`determine_object` 和 `describe_object` 等 LLM 驱动环节。MiniMax 调用过程中出现过一次临时的 `SSLEOFError`，但 `LLMModel.completion()` 会自动重试，最终 checkpoint 正常生成；判断运行是否成功，不看中间是否完全没有警告，而看最终是否写出 checkpoint 和压缩结果。

运行成功后，会出现 checkpoint 目录：

```text
generative_agents/results/checkpoints/book-smoke/
```

本次运行生成了下面这些结果文件：

| 文件 | 作用 |
| --- | --- |
| `simulate-20240213-0930.json` | 第 1 个 step 的完整仿真状态 |
| `simulate-20240213-0940.json` | 第 2 个 step 的完整仿真状态 |
| `conversation.json` | 仿真过程中产生的对话记录，本次没有触发对话，所以内容为空对象 |
| `storage/阿伊莎/`、`storage/克劳斯/` | 两个角色各自的记忆索引和持久化存储 |

此时只需要确认两件事：checkpoint 文件已经生成，并且命令没有在 LLM、embedding 或 JSON 解析阶段报错。只要这两件事成立，项目主链路就已经跑通。

## 12.4 压缩结果

checkpoint 适合系统恢复，不适合人类直接阅读。下一步要把 checkpoint 压缩成回放数据：

```bash
python compress.py --name book-smoke
```

命令成功时，控制台会输出：

```text
Compression completed.
```

压缩成功后，会生成：

```text
generative_agents/results/compressed/book-smoke/
```

压缩结果里重点看两个文件：

| 文件 | 适合谁看 | 说明 |
| --- | --- | --- |
| `movement.json` | 前端和数据分析脚本 | 保存角色位置、动作、对话和回放帧 |
| `simulation.md` | 文本阅读和审稿 | 按时间线呈现每个角色的行动和对话 |

第一次压缩后的 `movement.json` 包含 123 帧，角色列表只有阿伊莎和克劳斯，起始时间是 `2024-02-13T09:30:00`，步长是 10 分钟。这一步很关键。Generative Agents 的价值不只是“模型生成了文本”，而是仿真结果能被复盘。没有压缩结果，只能看零散日志；有了 `movement.json` 和 `simulation.md`，才能判断角色是否真的在小镇中持续行动。

## 12.5 回放自己的仿真

如果 `replay.py` 已经在运行，可以直接打开：

```text
http://127.0.0.1:5000/?name=book-smoke&step=0&speed=2&zoom=0.8
```

![图 12-3：book-smoke 最小仿真的回放页面](../../assets/chapter_12/fig-12-4-book-smoke-replay.png)

*图 12-3：`book-smoke` 最小仿真的回放页面。底部只剩阿伊莎和克劳斯两个角色，说明 `--agent-count 2` 已经生效。*

如果服务还没启动，先运行：

```bash
python replay.py
```

回放页面有几个常用参数：

| 参数 | 含义 | 示例 |
| --- | --- | --- |
| `name` | 压缩结果名称 | `book-smoke` |
| `step` | 从第几个仿真步开始播放 | `0` 表示从头开始 |
| `speed` | 播放速度，0 最慢，5 最快 | `2` 适合观察 |
| `zoom` | 地图缩放比例 | `0.8` 适合初看 |

如果浏览器提示找不到数据文件，通常说明还没有执行 `compress.py --name book-smoke`，或者 `name` 参数和压缩目录不一致。

图 12-3 需要重点看三个地方。第一，顶部时间显示为 2024 年 2 月 13 日上午 9 点 30 分后的小镇时间，说明回放读取了 `movement.json` 中的仿真起点。第二，底部角色栏只有两个头像，说明本次不是完整小镇，而是最小仿真。第三，画面仍然是同一张 Smallville 地图，说明缩小 agent 数量不会改变世界模型，只是减少参与仿真的角色。

## 12.6 直接阅读 simulation.md

回放页面呈现空间，`simulation.md` 呈现行为。第一次跑完项目后，立刻打开：

```text
generative_agents/results/compressed/book-smoke/simulation.md
```

本次 `simulation.md` 中的关键活动可以整理成下面这张表：

| 小镇时间 | 阿伊莎 | 克劳斯 |
| --- | --- | --- |
| `20240213-09:30` | 在奥克山学院宿舍房间的书桌前，写下对哈姆雷特独白的初步思考 | 在奥克山学院图书馆的图书馆桌子前，整理文献中的关键观点和数据 |
| `20240213-09:40` | 继续围绕莎士比亚语言运用做笔记，行为保持在毕业论文研究线上 | 撰写研究论文新段落的草稿 |

阅读时重点看四类信息：

| 观察点 | 判断问题 |
| --- | --- |
| 时间线 | 仿真是否按 `start` 和 `stride` 正常推进 |
| 角色行动 | 每个角色是否有清楚的行动描述 |
| 对话内容 | 对话是否符合角色身份和当前场景 |
| 地点变化 | 角色是否真的在地图上移动，而不是只生成文本 |

如果只跑 2 个 step，故事不会很精彩，这是正常的。最小仿真不用于复现论文里的派对传播，而是用于确认项目链路完整：启动、思考、保存、压缩、回放、阅读。

## 12.7 断点恢复

项目支持从已有 checkpoint 继续运行。假设 `book-smoke` 已经运行过，可以继续追加 2 个 step：

```bash
python start.py \
  --name book-smoke \
  --resume \
  --step 2 \
  --stride 10 \
  --verbose info
```

断点恢复会读取下面这个 checkpoint 目录：

```text
generative_agents/results/checkpoints/book-smoke/
```

恢复运行后，还需要重新压缩：

```bash
python compress.py --name book-smoke
```

重新压缩后，再打开从第 3 个 step 开始的回放地址：

```text
http://127.0.0.1:5000/?name=book-smoke&step=3&speed=2&zoom=0.8
```

![图 12-4：book-smoke 断点恢复后的回放页面](../../assets/chapter_12/fig-12-6-book-smoke-resume.png)

*图 12-4：`book-smoke` 断点恢复后的回放页面。顶部时间已经推进到 2024 年 2 月 13 日 09:50 后，说明回放从追加后的第 3 个 step 开始。*

断点恢复后的 checkpoint 目录新增了两个文件：

| 新增文件 | 含义 |
| --- | --- |
| `simulate-20240213-0950.json` | 恢复运行后的第 3 个 step |
| `simulate-20240213-1000.json` | 恢复运行后的第 4 个 step |

重新压缩后，`movement.json` 从 123 帧扩展到 243 帧，`simulation.md` 也新增了 `20240213-09:50` 和 `20240213-10:00` 两段记录。后续活动可以这样读：

| 小镇时间 | 阿伊莎 | 克劳斯 |
| --- | --- | --- |
| `20240213-09:50` | 整理所标记的段落并归纳主题 | 继续围绕论文草稿和资料整理推进 |
| `20240213-10:00` | 收拾学习用品，准备前往图书馆 | 在图书馆找座位并准备工作空间 |

断点恢复的意义是让长时间实验可控：先跑很短的片段，确认效果，再逐步延长，而不是一次性跑完整天。图 12-4 的顶部时间和新增 checkpoint 文件，是判断恢复成功的两个最直接证据。

## 12.8 第一次运行的排错表

第一次启动项目时，问题通常集中在环境、模型、结果目录和回放数据四类。

| 现象 | 常见原因 | 处理方式 |
| --- | --- | --- |
| `name already exists` | 仿真名称已经有 checkpoint | 换一个 `--name`，或使用 `--resume` |
| LLM 调用失败 | `config.json` 中 provider、model、base_url 或 api_key 不正确 | 先确认模型服务能被访问，再跑最小仿真 |
| embedding 失败 | 本地 embedding 模型没有拉取或接口地址不对 | 检查 `agent.associate.embedding` 配置 |
| MiniMax 调用中出现临时 `SSLEOFError` | 远端连接偶发中断 | 如果最终 checkpoint 正常生成，可以忽略；如果反复出现，重新运行或检查网络 |
| 回放页面提示数据不存在 | 只运行了 `start.py`，还没执行 `compress.py` | 先运行 `python compress.py --name <name>` |
| 回放能打开但没有预期角色 | `--agent-count` 或 `--agents` 限制了运行角色 | 检查启动命令中的角色参数 |
| `simulation.md` 很短 | step 太少 | 先确认链路，再逐步增加 step |

这张表不覆盖所有异常，只处理第一轮启动最常见的问题。Generative Agents 是多模块系统，运行失败时不要立刻怀疑论文思想，先确认项目链路是否完整。

## 12.9 小结

到这里，项目已经完成一次最小闭环：看到示例回放，跑出自己的 checkpoint，再把结果压缩成可回放、可阅读的材料。

| 已完成动作 | 意义 |
| --- | --- |
| 打开 `example` 回放 | 不需要模型也能先看到项目效果 |
| 检查 `config.json` | 知道 LLM 和 embedding 是运行前提 |
| 运行 `book-smoke` 最小仿真 | 验证启动、模型调用和 checkpoint 链路 |
| 执行 `compress.py` | 把系统状态转成回放和阅读材料 |
| 打开 `replay.py` 页面 | 观察小镇地图、角色移动和对话 |
| 阅读 `simulation.md` | 用文本方式复盘角色行为 |
| 尝试 `--resume` | 理解长时间仿真如何分段运行 |

这些操作结果已经落到三个地方：`results/checkpoints/book-smoke/` 保存可恢复的系统状态，`results/compressed/book-smoke/movement.json` 支持浏览器回放，`results/compressed/book-smoke/simulation.md` 支持文本复盘。只要这三个产物同时存在，Generative Agents 的最小运行闭环就成立。

下一章沿着已经跑通的项目，一项项体验它提供的核心能力：角色定义、日程、感知、记忆、对话、反思、回放和模型适配。先知道每个功能长什么样，第三部分再深入看这些功能是怎么写出来的。

## 参考资料

- Local README: `README.md`
- Local config: `generative_agents/data/config.json`
- Run entry: `generative_agents/start.py`
- Compression entry: `generative_agents/compress.py`
- Replay entry: `generative_agents/replay.py`
- Example replay: `generative_agents/results/compressed/example/`
