# Brain 资源创建说明

1. 在 Studio 的 Brain/Skill 工作区新建一个 Brain，名称填写“案例 1：醒来后的晨间工作启动”，稳定 `skill_key` 使用 `book-case01-morning-routine`。
2. 将 [brain/book-case01-morning-routine/SKILL.md](brain/book-case01-morning-routine/SKILL.md) 全文粘贴或作为本地 Skill 内容保存。保留文件开头的 `name`、`description`、`example_input` frontmatter。
3. 在实验中把该 Brain 选为陈明远的 Brain。依赖检查应看到 `world-perceive`、`world-navigate`、`world-act`、`memory-stream-search`、`memory-stream-append` 五个 MCP，子 Skill 列表为空。
4. `brain-definition.json` 是旧版历史记录，不作为当前导入文件或自检清单。学员按 Studio 的普通创建、保存和选择流程录入本目录 `SKILL.md`。

运行配置为 1 个 Agent、32 步、每步 3 分钟。公共 Brain 已在浏览器保存（hash `8ea4885f4eea`），本地 `SKILL.md` 按该 UI 正文同步；实验和 Run 的当前验证状态见 `../settings/verification-record.md`。

Brain 每轮先读取自己的最新“晨间进度便签”（search 的 `query=""`、`limit=20`），核对上一条待确认动作，再将累计已确认事项和本轮待确认动作 append。便签的 `poignancy` 固定为 1。append 必须在 `world-act` 前：成功提交世界动作会立即结束本轮，下一轮再按真实活动、坐标和对象状态确认，不能把拟执行动作提前记成完成。

导航查询和 MOVE 都使用同一个最终可走 `target_coord`，省略 `target_address`；`next_coord` 只是路径第一格，不能当最终目标。普通 ACT 不填写目标位置，发生在当前真实站位。记忆便签只用于生活进度，回放仍由系统提交的 Event 和 structured_payload 驱动。
