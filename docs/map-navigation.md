# 地图通路与公共导航能力

入口沿用现有地图编辑器：世界工具栏中「语义」旁的「通路」，以及选中素材切片/画布后的「碰撞」。工具在原检查器中提供阻挡、可走、恢复素材结果和两点路径检查；空格拖动、缩放、撤销重做及地图自动保存沿用现有机制。窄窗口中的资源栏可通过页签右侧按钮展开。

地图的 `editor_v2.navigation` 保存导入的静态阻挡底层 `base_blocked` 和手工 `overrides`。阻挡格是内部 Tile 坐标，不是新增空间语义层。素材的 `collision_cells` 使用未旋转切片的局部网格索引；旋转和放置时按渲染坐标变换。素材画布继承组成切片的阻挡，再应用自身局部覆盖；地图合并阻挡，最后应用地图手工覆盖。图片透明度、视觉层显隐和层级名称都不改变通行。删除覆盖与设置可走是不同操作。

Studio 保存和生成实验包时，使用 `ga_protocol.navigation` 中的纯文件编译函数生成 `world.definition.tiles[*].collision`。原始底层与编译结果分开保存，重复编译不会把手工覆盖写回底层。实验包物理持有全部配置，完整性哈希覆盖通路；公共地图后续编辑不影响既有实验与 Run。

编辑器通过只读 `POST /api/studio/resources/map-editor/navigation` 校验当前未保存缓冲区，返回最终阻挡格、继承阻挡格和可选路径。请求按文档、选择范围、编辑序号和请求代次隔离；绘制或切换范围会撤销旧请求。路径检查与 Runtime 的 `Maze.find_path` 共用确定性的四方向 BFS：起终点必须可走，不允许对角穿墙。

`world-navigate` 是绑定当前 Agent/IterationContext 的只读 MCP：

- 传入 `target_coord: [x,y]`，目标必须处于当前 Agent 的配置视野内；或传入完整的已感知/已记忆 Arena、Game Object `target_address`，不允许同时传两者。
- 身份、起点与视野由系统注入，模型不能传其他 Agent 身份或扩大视野。
- 返回 `reachable`、`distance_tiles`、`next_coord`、`movement_required`；不会返回整张地图、沿途空间语义或其他 Agent 记忆。阻挡/断开返回不可达。
- 查询不推进时间、不占用世界动作次数、不改位置。真实移动始终通过 `world-act` 的 `MOVE` 提交，并再次校验通路；每 Agent 每 Step 最多一次世界动作。

本次能力处理静态通路与固定门洞。动态开关门仍须通过 Game Object 状态、合法动作及 World Commit 实现，不能让对象 Skill 直接修改碰撞数据。

回归：`pytest tests/foundation/test_navigation.py tests/test_portable_package_protocol.py tests/architecture/test_portable_module_boundaries.py`。浏览器验证可启动 `python -m uvicorn tests.frontend.navigation_fixture:app --port 8876`，再在已安装 Playwright 的 Node 环境执行 `node tests/frontend/map-navigation.browser.cjs`；它使用真实编辑器和只读 API，不修改公共作者资源。
