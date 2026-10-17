# 本地经典 25 人基准与参数来源

这是作者资料准备记录，尚未通过系统界面配置或运行。基准选用用户本地中文项目扩展前 `example` 示例的人物快照。它不是论文官方原始实验包；本记录未通过外网核对论文或官方仓库。

源项目根：`C:/Users/D304/Downloads/GenerativeAgentsCN-main/GenerativeAgentsCN-main/`。下文源路径均相对此根；人物和图片映射见 [人物目录](../agents/README.md)。源文件没有被修改。

## 经典示例的可核对参数

| 项目 | 原值或证据 | 来源 |
| --- | --- | --- |
| 人物数量与顺序 | 25 人，不含林晓 | `generative_agents/results/compressed/example/movement.json` 的 `persona_init_pos` |
| 起始时间 | `2024-02-13T06:00:00`，没有时区 | 同文件 `start_datetime` |
| 每逻辑步时间 | 10 分钟 | 同文件 `stride=10`；`start.py` 使用 `timer.forward(stride)`，`Timer.forward()` 按分钟推进 |
| 逻辑步数 | 120 | 同文件对话时刻键共 120 项，从 `20240213-06:00` 到 `20240214-01:50`；以下展示帧证据相互印证 |
| 展示帧 | 数字键 0 至 7200，共 7201 帧，0 为初始帧 | 同文件 `all_movement`；另外还有 `description`、`conversation` 两个非数字键 |
| 逻辑步与展示帧关系 | 每逻辑步生成 60 帧；7200÷60=120 | `generative_agents/compress.py` 的 `frames_per_step=60`，帧号公式 `(step-1)*60+1+i` |
| 虚拟覆盖区间 | 120×10 分钟=20 小时；最后执行时刻为次日 01:50，推进后区间边界为次日 02:00 | 起点、stride、120 逻辑步及 `start.py` 在保存每步后推进时间共同推导 |
| 原始头像和精灵 | 头像 32×32，行走图 96×128，每帧 32×32 | 逐文件 PNG 头校验；副本与源文件 SHA256 全部相同 |
| 初始坐标与地址 | 所有 25 人坐标与当前源 agent.json 相同，均在各自四层床位地址 | example `persona_init_pos`、源 `frontend/static/assets/village/maze.json` 对应 tile |
| 初始人设 | age、innate、learned、lifestyle、daily_plan、currently | example `all_movement.description.<中文名>`；全部保存在各人 `source-reference.json` |

7200 是回放展示帧上限，不能作为新系统仿真步数。`sec_per_step=10` 是压缩回放参数，不能解释为原仿真每步 10 秒。`simulation.md` 只记状态变化，因此最后一条变化时间不能代替最后逻辑步时刻。

## 当前中文项目参数与历史示例的边界

`generative_agents/data/config.json` 当前共享参数为：方框感知 `mode=box`，`vision_r=8`，`att_bandwidth=8`；排程 `max_try=5`、`diversity=5`；`think.interval=1000`、`poignancy_max=150`；`chat_iter=4`；记忆检索 `retention=8`。历史 example 未嵌入完整共享配置，不能证明这些值就是 example 当时的全部值。

当前模型配置为 MiniMax-M3（`https://api.minimaxi.com/v1`，`max_tokens=8192`），嵌入为 embo-01（`https://api.minimax.chat/v1`）。README 的 2026-01-15 更新记录却记载 qwen3:4b-instruct-2507 与 qwen3-embedding:0.6b；example 本身未保留模型身份。模型实际绑定应在新系统界面显式选择本机可用配置，不能把当前文件或 README 的任一模型冒称历史示例实测模型。凭据不复制到本资料。

`start.py` 当前 CLI 默认起点是 `20240213-09:30`、10 步、步长 10 分钟；`get_config()` 函数自己的 stride 默认是 15，但 CLI 实际传入 10。README 启动示例使用 `20250213-09:30`。本次经典示例基准使用上述 example 的 2024-02-13 06:00、120 步、10 分钟，不混用这几组默认值。

源时间由无时区 `datetime.strptime` 构造，没有 UTC 偏移；新系统需要明确时区，选择 Asia/Shanghai 属于新的运行装配决定。源运行配置没有随机种子，相关代码调用 Python random 但没有设置固定 seed。源 Agent 没有仿真移动速度参数；前端 `movement_speed` 是回放动画速度，不能套成新系统的移动速度。

共享记忆类默认衰减和权重（`modules/memory/associate.py`）：recency_decay=0.995、recency_weight=0.5、relevance_weight=3、importance_weight=2、max_importance=10。它们是中文项目实现的默认参数；新系统接口是否采用同一检索实现需另行说明，不是 UI 填写同名参数即可实现等价。

## 排除后加内容的证据

书稿 `docs/book/manuscript/part_04/chapter_27.md` 明确新增记者林晓、咖啡馆社区公告板及三人对记者的关系。人物快照与当前 agent.json 相比仅 5 个基础字段不同：汤姆 currently；伊莎贝拉 learned/currently；山姆 learned/currently。本资料均采用 example 值。

三人的空间记忆删除后加“社区公告板”，其余空间记忆保留当前源值并明确没有完整历史快照。第 27 章保留 `[74,20]` 原地址为 `[霍布斯咖啡馆,咖啡馆]` 的变更前片段；经典地图应按该证据排除后加对象。

## 来源摘要

| 文件 | SHA256 |
| --- | --- |
| `generative_agents/results/compressed/example/movement.json` | `9b3480cb0b6eb4c2b31c0667c44377c3251091d0d64937c8ba50d1d8163f5989` |
| `generative_agents/results/compressed/example/simulation.md` | `21c44d15f9bb0b8bd207c06482061679cc5fc003c99422cd913154a738b8d1f8` |
| `generative_agents/data/config.json` | `7eae281c32224d43e921a4cacb4f1cf6dfbfc630e7c16bd0bd75d5876396e783` |

逐人源 agent.json、maze 及每张图片摘要见 [映射清单](../agents/roster-reference.json) 和人物 `source-reference.json`。
