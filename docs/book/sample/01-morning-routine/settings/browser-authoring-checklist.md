# 浏览器作者配置清单

这是学员创建自己实验实例的唯一入口。不要把目录里的旧 JSON 当作系统导入包，也不要复用其他人的实验 ID。以下配置已完成一次 32 步晨间行为验证，跨 Attempt 质量报告与浏览器导出已复验归档；结果和已知改进点见 [验证记录](verification-record.md)。普通结果 ZIP 供审计和查看嵌入素材，复现仍需完成以下页面配置。

1. 创建 32×24、Tile 32 的地图，按 [地图配置步骤](../map/README.md) 建立 11 个节点、碰撞矩形并检查四段路线。
2. 导入 [住宅背景](../map/assets/home-background.png)、[整理前桌面源图](../map/assets/desk-scattered-source.png)、[整洁桌面源图](../map/assets/desk-tidy-source.png)，按地图配置步骤中的切片值绑定 World 和工作书桌的两个状态。
3. 按 [人物录入说明](../agents/陈明远/agent-definition.txt) 创建一个 Agent，上传 [头像](../agents/陈明远/assets/portrait.png)（32×32）和 [行走图](../agents/陈明远/assets/texture.png)（96×128，3 列×4 行）；视野半径设为 `10`、注意力带宽为 `6`，保存后应用到单 Agent 人群。对本案例将“地图显示大小”设为 `3` 格；其他地图按自身角色比例调整。
4. 创建 Brain，粘贴 [SKILL.md](../skill/brain/book-case01-morning-routine/SKILL.md)，启用 `world-perceive`、`world-navigate`、`world-act`、`memory-stream-search`、`memory-stream-append` 五个 MCP。每轮 search（`query=""`、`limit=20`）找回自己的最新晨间进度；先 append 累计便签（`poignancy=1`、本轮动作待确认），最后提交 world-act；下一轮依据真实活动、坐标和状态确认。整理后的书桌状态单独设为顶层 `state=tidy`。
5. 创建实验，选择上述地图、人群和 Brain，以及本机可用的模型配置；模型服务和 `credential_env` 对应环境变量需自行配置。设置 `2026-09-09 07:00`、Asia/Shanghai、3 分钟步长、32 步、随机种子 42；检查点间隔 `1` 步、保留 `2` 个，Agent Step 投影间隔 `1` 步，开启模型 Payload 记录。在实验内确认头像、行走图、视野 10、注意力 6 和 3 格显示已复制，设置出生点 `[9,5]`，并按地图说明保存卧室、书房、厨房、餐区和家中通道的已知空间。实验 ID、Run ID 和 SEALED 状态由学员自己的 Studio 实例生成。
6. 先做预检，再运行并检查角色比例、路线、ACT、桌面状态和 StepResult；遇到比例或状态问题应暂停并记录，不把预检通过当作运行成功。

导航检查：对床旁 `[9,5]`、书桌旁 `[23,7]`、料理台前 `[10,15]`、餐桌旁 `[22,17]` 的最终可走站位查询路径，MOVE 继续使用同一个最终 `target_coord`，不填 `target_address`，不把 `next_coord` 当目的地；普通 ACT 不填目标位置。当前移动预算为最多 12 格/步。
