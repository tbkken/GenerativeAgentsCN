# Game Object Skill 行人过街系统地图证据

本目录中的两张 PNG 于 2026-08-23 通过产品的地图工作台“导入原图”入口上传，地图、素材切片、World 层级、Game Object Skill 和实验均通过浏览器界面创建与配置；没有使用 seed 脚本或直接写入地图 JSON。

## 系统地图

- 名称：`Game Object Skill：行人过街演示（系统构建版）`
- Map ID：`8f3f17dc-05b3-498a-8ff2-36fbf143992c`
- 稳定键：`pedestrian-crossing-skill-system`
- Published Revision：`601e6c0d-d7e6-402b-b381-ab0051fc32a9`（v1）
- World hash：`979b8d12e45d62ff63de032558b8fd8f0e06cd1a87f775e6215655dbc2bc2e20`
- 尺寸：9 × 7，Tile 32 px
- 地图内容：63 个已绘制格子、中央纵向道路、中心斑马线、行人信号灯、西侧等候区、东侧出口
- 红绿灯绑定：`traffic-signal-state` / `query-pedestrian-signal`，交互半径 2.5

## 上传素材

- `pedestrian_crossing_tiles_2x2_32px.png`
  - SHA-256：`a6c618591eac65b706a6e53f584a5262a9ee3c0127a822de36f42cfe38ce7e6a`
  - 系统 Asset ID：`652a1f5b-78d8-4cc9-ab93-b874c52558f8`
  - 切片：草地、道路、斑马线
- `pedestrian_traffic_light_32x64.png`
  - SHA-256：`a09f63ac332187711e68309feee2c5c05ee2760ea63658c2bd952144178adb18`
  - 系统 Asset ID：`86511666-6098-4f77-9431-e8d0a48a0d3a`
  - 切片：行人信号灯（Game Object）

## 端到端实验

- 实验：`Game Object Skill 端到端实验：行人过街（系统地图版）`
- Experiment ID：`d4c51092-f0f8-429a-9c5a-97a99a32a38e`
- 成功 Revision：`c1e6f832-eb9b-41a0-9eca-90ce3a9df13e`（revision 003）
- 成功 Run：`8ac4a498-52ce-4621-86a9-e60e156b98a5`
- 运行结果：`COMPLETED`，3/3 步
- Step 1：Agent 主动查询；红灯 Skill 被动响应；决策 `WAIT`；坐标保持 `(2, 3)`
- Step 2：Agent 再次主动查询；绿灯 Skill 被动响应；决策 `CONTINUE`；沿 `(2,3) → (3,3) → (4,3) → (5,3)` 穿过斑马线到达东侧出口
- 模型：本地 `Qwen3.8-27B-UD-Q4_K_XL`；Embedding `qwen3-embedding-0.6b`

首次真实运行暴露了系统地图 `object` 与旧运行时 `game_object` 的层级命名差异；第二次运行进一步暴露了编辑器完整路径含 World 根节点、旧运行时又重复添加根节点的问题。两次失败均保留在实验运行历史中，修复后 revision 003 完整通过。
