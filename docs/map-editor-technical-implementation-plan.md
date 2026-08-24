# 地图编辑器技术实现方案

> 状态：Implementation Ready
> 目标版本：地图工作区 V2
> 交互基线：`docs/prototypes/map-design-interaction.html`
> 适用仓库：GenerativeAgentsCN
> 主要技术栈：FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、原生 HTML/CSS/JavaScript、Canvas 2D
> 文档用途：直接交给 Claude Code / Qwen2.8 27B（256K 上下文）编码模型实施

---

## 1. 结论先行

本次不新建独立前端工程，也不把地图编辑器重写成 React/Vue 应用。正式实现继续嵌入现有实验控制台，沿用下列基础能力：

- `WorldMap` / `WorldMapRevision` 的草稿、乐观锁、发布和不可变 Revision；
- `/api/v1/maps/...` 现有生命周期接口；
- `/api/v1/assets` 内容寻址资源上传与 `/content` 读取；
- `WorldConfig.definition.tiles` 作为 `Maze` 运行时输入；
- 现有控制台的原生 JavaScript、CSS 变量、Toast、错误事件和页面路由；
- Canvas 2D 绘制、DPR 适配、缩放、平移、撤销/重做的实现基础。

正式地图编辑器由三个顶部主工作区组成：

1. **地图**：第一层基座，负责底图绘制和底图素材摆放；
2. **世界**：管理 `World → Sector → Arena → Game Object` 四层地址树和第 2～4 层叠加内容；
3. **素材**：管理原图、精确切片、复合配方以及它们的应用关系。

四层“显示至”下拉框不是可编辑图层面板，而是累计可见深度：

| 显示深度 | 画布内容 |
| --- | --- |
| 第 1 层 · 地图底图 | 仅地图基座 |
| 第 2 层 · Sector | 地图底图 + Sector |
| 第 3 层 · Arena | 地图底图 + Sector + Arena |
| 第 4 层 · Game Object | 地图底图 + Sector + Arena + Game Object |

`World` 是四层地址树的根，不额外占据一个显示层，因此“地址有四层”和“画布显示有四层”并不冲突。

---

## 2. 不可回退的产品决策

以下内容是多轮高保真确认后的最终约束。实现模型不得根据旧代码、旧原型函数名或历史需求重新引入这些界面。

### 2.1 必须保留

- 顶部主 Tab 只保留「地图 / 世界 / 素材」。
- 世界左侧只使用一棵四层地址树。
- 地址严格为 `World → Sector → Arena → Game Object`，不得增加第五层。
- 选中树节点即完成选择和画布定位。
- 子节点只能从左侧树标题区的新增按钮创建。
- 对象检查器只编辑当前选中节点，底部只保留“保存”主操作。
- 节点编辑至少包含：当前地址、X、Y、宽度、高度、空间语义。
- 素材以“原图 → 切片”的树结构组织，复合配方作为独立根节点。
- 点击原图时，中间画布显示完整原图和该原图的全部切片框。
- 点击切片时，中间画布居中显示切片本身。
- 手动切片支持移动范围和调整宽高，坐标字段与画布双向同步。
- 原图/切片的应用情况只出现在右侧检查器，不与来源树混排。
- 树节点展开/收起和滚动位置必须稳定；点击节点不得把滚动条重置到顶部。
- 语义是画布叠加状态，不是另一棵树。开启语义后，左侧仍保持当前工作区原有内容。
- 在“地图”工作区开启语义时，也必须把语义范围叠加在底图之上。
- 未开启语义时，画布不得自行绘制“山本百合子的房子”等地址文字。
- 跨层语义区域允许嵌套重叠；同层重叠给出警告但不在草稿阶段强行阻断。
- 字号使用正式控制台可读尺寸：正文和控件建议 14px，辅助信息不得低于 12px。

### 2.2 必须删除或不得新增

- 不提供独立 `Game Object 对象库`、对象库抽屉或对象库 Tab。
- 不提供“视觉变体”这一用户概念、字段名、步骤条或第五层关系。
- 不提供独立的 World / Sector / Arena / Game Object 四个管理 Tab。
- 不提供“图层”按钮和可编辑图层列表。
- 不提供“空间资产”按钮或独立空间资产工作区入口。
- 地图配置中不出现 Agent 名册、不直接绑定 Agent。
- 不提供独立“四层语义树”。
- 对象检查器不放“定位节点”和“新建子节点”按钮。
- 不显示“数据源 tilemap.json + maze.json”“运行时 Maze / Spatial”等实现说明。
- 不显示对象关系步骤条，例如“Game Object → 对象图形 → 地图实例”。
- 不显示素材覆盖率卡片，例如“100%”“1,272 / 1,272 个 Ville 基础 Tile 已准确索引”。
- 不显示大段“为什么这样设计”“这里用于什么”等说明性文案。
- 不在地图左下角长期显示操作说明。
- 不为了区分实例而把 `壁橱 B` 拼成第五级地址。

### 2.3 空间感知与 Agent 的边界

旧讨论中曾定义门禁、红绿灯等物体的感知能力：物体感知附近事件，满足条件时向 Agent 输出自然语言参数，Agent 再调整行为。该方向仍然成立，但**不属于本轮地图菜单 V2 的可见交互范围**。

本轮只保证数据结构可扩展：Game Object 节点可保留 `extensions` 扩展字段，未来可以挂载感知规则；当前 UI 不出现 Agent、不出现能力绑定、不恢复旧“空间资产”按钮。

---

## 3. 现有工程基线

实施前必须先阅读并保护以下现有文件：

| 文件 | 当前职责 | 本次策略 |
| --- | --- | --- |
| `CLAUDE.md` | 仓库约束、运行和测试命令 | 必须遵守 |
| `generative_agents/web/static/experiment-console.html` | 控制台地图页面 DOM | 替换地图编辑区域，保留控制台外壳 |
| `generative_agents/web/static/map-workspace.js` | 地图列表、GridEditor、保存发布 | 演进为 V2 编排器，保留 `window.MapWorkspace` 入口 |
| `generative_agents/web/static/map-workspace.css` | 地图页面样式 | 在同一文件内升级，避免引入构建链 |
| `generative_agents/services/maps.py` | 地图草稿、发布、校验、Revision | 增加 V2 规范化、编译和校验 |
| `generative_agents/web/app.py` | `/api/v1/maps` 路由 | 尽量复用既有接口，只增加资源解析所必需接口 |
| `generative_agents/config/schema.py` | `WorldConfig` | 保持外层兼容，新建专用 Editor 契约 |
| `generative_agents/config/spatial_assets.py` | 旧空间资产契约 | 保留兼容，不作为新 UI 主模型 |
| `generative_agents/runtime/replay_v2.py` | 回放地图资源描述 | 后期接入地图 Revision 自带渲染清单 |
| `generative_agents/modules/maze.py` | 运行时地址、碰撞、寻路 | 继续消费编译后的 `definition.tiles` |
| `generative_agents/frontend/static/assets/village/tilemap/tilemap.json` | Ville Tiled 图层与 Tileset 元数据 | 导入器真值来源 |
| `generative_agents/frontend/static/assets/village/maze.json` | Ville 地址与碰撞数据 | 导入器语义真值来源 |

现有生命周期接口继续作为正式主链路：

```text
GET  /api/v1/maps/{map_id}
GET  /api/v1/maps/{map_id}/draft
PUT  /api/v1/maps/{map_id}/draft
POST /api/v1/maps/{map_id}/draft/publish
POST /api/v1/maps/{map_id}/revisions/{revision_id}/fork
POST /api/v1/assets
GET  /api/v1/assets/{asset_id}/content
```

不要绕开 `WorldMapRevision.lock_version`；不要在前端直接更新数据库；不要修改已发布 Revision。

### 3.1 Ville 导入的固定回归事实

以下数据由当前真实 `tilemap.json` 与 `maze.json` 计算得出，用于测试导入器，不作为 UI 覆盖率文案显示：

| 指标 | 期望值 |
| --- | ---: |
| 地图尺寸 | 140 × 100 |
| Tile 尺寸 | 32 × 32 px |
| Tiled 图层 | 17 |
| Tileset | 18 |
| 视觉 Tileset | 15 |
| 被 Ville 使用的视觉 Tileset | 13 |
| 视觉层非空 Tile 摆放 | 26,414 |
| 唯一视觉 GID | 1,272 |
| Maze 稀疏 Tile | 4,201 |
| Sector | 19 |
| Arena | 63 |
| Game Object 地址路径 | 222 |

这些值必须由算法算出，禁止在实现中硬编码成展示数据。

---

## 4. 最终交互规格

### 4.1 页面骨架

页面沿用三栏工作台：

```text
┌──────────────────────────────── 顶部地图信息 / 保存 / 发布 ────────────────────────────────┐
├────────────────────────────── 地图 | 世界 | 素材 ─────────────────────────────────────────┤
├───────────────┬──────────────────────────────────────────────┬────────────────────────────┤
│ 左侧导航/素材 │ 中央 Canvas + 工具条                         │ 右侧对象检查器             │
│               │ 撤销 重做 缩放 适配 显示至 语义             │ 标题、预览、字段、保存     │
├───────────────┴──────────────────────────────────────────────┴────────────────────────────┤
│ 轻量状态栏：保存状态 / 光标坐标 / 当前选择（不放教学说明）                                 │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

右侧检查器顶部使用固定预览列和弹性文字列：

```css
.selection-summary-top {
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
}
.selection-copy { min-width: 0; overflow: hidden; }
.selection-copy strong { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
```

任何通用样式不得用 `.source-health > span` 之类宽泛选择器同时命中图标和标题容器。图标样式必须限定 `:first-child` 或专用 class。

### 4.2 公共画布工具

- 撤销：`Ctrl/Cmd + Z`；
- 重做：`Ctrl/Cmd + Shift + Z` 和 `Ctrl/Cmd + Y`；
- 缩小、当前缩放百分比、放大；
- 适配；
- “显示至”累计层级下拉框；
- “语义”开关；
- 鼠标滚轮以指针为锚点缩放；
- 空格 + 拖动或平移工具移动画布；
- 右键不得触发浏览器菜单影响拖动画布；
- Canvas 使用设备像素比绘制，坐标换算始终使用 CSS 像素。

语义开关与工作区正交：

- 在“地图”中开启：底图仍可见，语义框覆盖其上，左栏仍是底图工具；
- 在“世界”中开启：左栏仍是四层地址树，画布显示语义范围；
- 在“素材”中隐藏语义开关，因为素材画布不是地图坐标系。

### 4.3 地图 Tab

职责：编辑第 1 层地图基座。

左栏：

- 选择、画笔、橡皮、平移；
- 底图可用素材/配方列表；
- 不显示图层管理、空间资产和教学说明。

中央画布：

- 绘制真实 Tile 图形，不用色块代替 Ville 素材；
- 画笔放置选中的基础切片或复合配方；
- 橡皮只删除当前底图单元，不删除上层 World 节点；
- 默认可见深度为第 4 层，但进入“地图”仍只编辑第 1 层；显示深度只影响观察，不改变写入目标；
- 关闭语义时不画地址名称。

右侧检查器：

- 选中空白格：坐标和空状态；
- 选中底图 Tile：素材来源、坐标、占格尺寸、碰撞/通行编译结果；
- 选中复合配方：配方名称、占格尺寸、来源切片摘要；
- 只提供与当前选择相关的字段和保存/删除操作，不显示来源实现说明。

### 4.4 世界 Tab

#### 左侧四层地址树

- 根节点固定为一个 World；
- World 下只能新增 Sector；
- Sector 下只能新增 Arena；
- Arena 下只能新增 Game Object；
- Game Object 是叶节点；
- 箭头只控制展开/收起；
- 点击行主体选择节点并定位画布；
- 新增按钮文案随当前层级变化：`＋ Sector`、`＋ Arena`、`＋ Game Object`；
- 删除、复制等低频操作放在树行的 `…` 菜单，不放检查器底部；
- 删除父节点必须二次确认，并明确级联子节点数量；
- 删除操作只允许草稿 Revision；
- 不显示对象库抽屉。

#### 节点检查器

所有层级统一使用“选中节点编辑器”：

- 当前地址；
- 可修改的节点名称；
- 对允许换父级的节点提供 Sector / Arena 下拉；
- X、Y、宽度、高度；
- 空间语义；
- 单一“保存”按钮。

新建节点后：

1. 在树中插入“未命名 …”草稿节点；
2. 自动选中但不改变树的滚动位置；
3. 画布定位到父范围中心；
4. 右侧输入框聚焦名称；
5. 保存时校验边界和父子关系。

Game Object 直接归属于 Arena。Game Object 的图形通过 `render_recipe_id` 引用素材配方；该引用是技术字段，UI 使用“对象图形”或直接显示配方名称，绝不出现“视觉变体”。

#### 点击和定位

- 点击树节点后，画布缩放到节点范围，保留最小上下文边距；
- 点击画布中的语义框时，反向选中树节点并展开祖先；
- 多层框重叠时，默认命中最深层；按住 `Alt` 点击可在命中候选间循环，第一版也可用小型候选菜单替代；
- 点击节点不调用全页 `innerHTML` 重建；如暂时重建，必须在重建前后按稳定 key 保存并恢复滚动位置。

### 4.5 素材 Tab

#### 左侧来源树

树有两个顶级类型：

1. 复合配方；
2. 原图节点，每个原图节点下是精确切片和手动切片。

交互：

- 点击根节点的箭头或行可收起/展开；
- 展开原图时仍能看到其他原图节点；
- 子树内部滚动与左栏整体滚动分离；
- 选择任意子节点后保持两个滚动条原位置；
- 导入原图与手动切片是主要操作；
- 不显示覆盖率卡片和长篇说明。

#### 中央素材画布

原图模式：

- 完整原图在可用区域内等比居中；
- 显示该原图的所有切片框；
- 普通切片为绿色半透明框，当前切片为橙色框；
- 点击框选择对应切片；
- 图片像素不做平滑，保证像素素材清晰；
- 大图只绘制视口可见范围和可见切片标注。

切片模式：

- 只把当前切片居中放大展示；
- 保持透明背景棋盘格；
- 显示占用 Tile 宽高，不把整张原图伪装成切片；
- “在原图中编辑范围”切换到范围编辑模式。

范围编辑模式：

- 橙色矩形整体拖动；
- 右下角拖拽点调整宽高；
- 默认吸附 Tile 网格，也允许切换像素级切片；
- `col / row / w / h` 或 `x_px / y_px / width_px / height_px` 与画布双向同步；
- 范围必须裁剪在原图内；
- 保存后返回居中切片模式；
- 从 `tilemap.json` 自动索引的基础 Tile 默认只读，用户复制为手动切片后才能调整，防止破坏 GID 映射。

#### 右侧检查器

右栏第一部分是选择摘要；随后是“应用情况”，再是当前来源或切片字段。

应用情况由反向索引实时计算，不在树里复制：

- 原图应用：哪些切片引用它；
- 切片应用：哪些复合配方、底图单元或 Game Object 引用它；
- 配方应用：哪些地址树节点引用它。

应用列表项点击后可跳转到对应工作区和节点，但不得加入新的长期说明卡片。

---

## 5. 术语与身份模型

### 5.1 三种身份不可混用

| 身份 | 用途 | 示例 |
| --- | --- | --- |
| 稳定技术 ID | 数据关联、重命名后保持引用 | UUID / 确定性导入 ID |
| 四层语义地址 | Agent/Maze 理解空间 | `the Ville → 亚瑟的公寓 → 主人房 → 壁橱` |
| 素材引用 ID | 复用同一图形来源 | `recipe:closet-...` |

节点重命名不改变技术 ID。复制 Game Object 时生成新节点 ID，但可以继续引用同一 `render_recipe_id`。不得通过给名称加 `B` 来制造数据身份。

### 5.2 Game Object 不是对象库定义

V2 中每个 Game Object 树叶节点就是一个地图中的空间节点，包含自己的父级、范围、空间语义和图形引用。重复图形通过共享素材配方实现，不需要独立对象库，也不需要视觉变体层。

允许不同 Arena 下存在同名 Game Object。若同一 Arena 下出现同名节点：

- 技术 ID 仍然唯一；
- 语义地址可以指向多个空间范围，运行时地址索引本来就是一个地址映射到多个 Tile；
- 发布时给出“同级同名”的 warning，不自动改名、不增加第五层。

### 5.3 语义重叠规则

- World 覆盖 Sector、Sector 覆盖 Arena、Arena 覆盖 Game Object：正常嵌套，不告警；
- 同级区域部分重叠：草稿允许，发布 warning；
- 子级完全越出父级：发布 error；
- 多个同级节点覆盖同一 Tile：编译时按 `sort_order`、再按稳定 ID 决定优先项，同时产生 warning；
- 命中测试优先最深层，再按面积最小优先，再按 `sort_order`。

---

## 6. V2 数据契约

### 6.1 存储位置

不新增一套地图主表。V2 作者数据存储在当前 `WorldMapRevision.world_json`：

```text
WorldConfig
└─ definition
   ├─ world / size / tile_size / tile_address_keys
   ├─ tiles                 # 编译产物，Maze 运行时消费
   ├─ editor                # V2 作者数据，地图编辑器消费
   └─ render_manifest       # 渲染资源描述，编辑器/回放消费
```

推荐新增 `generative_agents/config/map_editor.py`，使用 `StrictModel(extra="forbid")` 定义专用 Pydantic 契约。`WorldConfig.definition` 暂时仍为 dict，以避免一次性破坏旧 Revision；地图服务在保存和发布时显式解析 `MapEditorDocumentV2`。

### 6.2 核心类型

以下是建议的 Python 类型轮廓，字段名可按仓库风格微调，但语义不得改变：

```python
MapDisplayLevel = Literal["MAP", "SECTOR", "ARENA", "GAME_OBJECT"]
HierarchyNodeKind = Literal["WORLD", "SECTOR", "ARENA", "GAME_OBJECT"]
MaterialSourceKind = Literal["BUNDLED", "UPLOADED", "GENERATED_COLOR"]
MaterialSliceKind = Literal["TILE", "STAMP", "PIXEL"]
RecipeEntryTransform = Literal[
    "NONE", "FLIP_H", "FLIP_V", "FLIP_D",
    "FLIP_HV", "FLIP_HD", "FLIP_VD", "FLIP_HVD",
]

class GridRect(StrictModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)

class MaterialSource(StrictModel):
    id: str
    name: str
    kind: MaterialSourceKind
    asset_id: str | None
    asset_hash: str | None
    bundled_path: str | None
    generated_color: str | None
    media_type: str
    width_px: int
    height_px: int
    tile_width: int
    tile_height: int
    columns: int
    rows: int
    tile_count: int
    margin: int = 0
    spacing: int = 0
    first_gid: int | None = None

class MaterialSlice(StrictModel):
    id: str
    source_id: str
    name: str
    kind: MaterialSliceKind
    grid_rect: GridRect | None
    pixel_rect: GridRect
    trim_transparent: bool = True
    indexed_gid: int | None = None
    local_tile_id: int | None = None
    readonly_indexed: bool = False

class RecipeEntry(StrictModel):
    slice_id: str
    x: int
    y: int
    z_index: int
    transform: RecipeEntryTransform = "NONE"
    source_raw_gid: int | None = None

class RenderRecipe(StrictModel):
    id: str
    name: str
    width_tiles: int
    height_tiles: int
    anchor_x: int = 0
    anchor_y: int = 0
    entries: list[RecipeEntry]
    imported: bool = False
    fingerprint: str

class LayerCellOverride(StrictModel):
    index: int
    slice_id: str | None          # null 表示显式擦除导入基线
    transform: RecipeEntryTransform = "NONE"
    collision_override: bool | None = None

class LayerRecipePlacement(StrictModel):
    id: str
    recipe_id: str
    x: int
    y: int
    rotation_degrees: int = 0

class VisualLayer(StrictModel):
    id: str
    name: str
    display_level: MapDisplayLevel
    z_index: int
    width: int
    height: int
    raw_gids: list[int]            # 导入基线；Tiled raw GID，保留 flip bit
    cell_overrides: list[LayerCellOverride]
    recipe_placements: list[LayerRecipePlacement]
    visible: bool = True
    opacity: float = 1.0

class HierarchyNode(StrictModel):
    id: str
    kind: HierarchyNodeKind
    parent_id: str | None
    name: str
    sort_order: int
    bounds: GridRect
    semantic: str
    render_recipe_id: str | None = None
    render_mode: Literal["LAYER_BACKED", "PLACED_RECIPE"] = "LAYER_BACKED"
    extensions: dict[str, Any] = Field(default_factory=dict)

class MapEditorDocumentV2(StrictModel):
    schema_version: Literal["ga-map-editor/v2"]
    root_node_id: str
    material_sources: list[MaterialSource]
    material_slices: list[MaterialSlice]
    render_recipes: list[RenderRecipe]
    visual_layers: list[VisualLayer]
    hierarchy_nodes: list[HierarchyNode]
    import_metadata: dict[str, Any]
```

实现时可以把大数组改为紧凑编码或稀疏单元，但 API JSON 必须稳定、可验证、可确定性哈希。140×100×10 的视觉层规模当前可以接受，第一版优先正确性。

`raw_gids` 保留原始 Tiled 地图，`cell_overrides` 表达用户对单格底图的覆盖/擦除，`recipe_placements` 表达多格配方放置。这样既不需要为上传素材伪造 GID，也不需要每次画笔操作改写整层数组。

导入节点使用 `render_mode=LAYER_BACKED`：画布由所属 `VisualLayer.raw_gids` 绘制，节点的 `render_recipe_id` 只用于局部预览、复制和素材反向引用，不能再次叠画造成双重渲染。新建并从素材放置的节点使用 `PLACED_RECIPE`，由对应层的 `recipe_placements` 绘制。

### 6.3 不持久化的 UI 状态

以下状态属于浏览器会话，不写入地图 Revision：

- 当前顶部 Tab；
- 当前选中节点；
- 树展开集合；
- 树和子树 `scrollTop`；
- 画布缩放与偏移；
- 当前语义开关；
- 当前“显示至”；
- 素材树展开节点；
- 临时拖动状态、Hover、Toast。

可以按 `map_id + revision_id` 写入 `sessionStorage`，但不得污染地图内容哈希。

### 6.4 编译产物

`definition.tiles` 必须由 V2 文档确定性编译，而不是由 UI 在多个位置手工同步。

对地图中每个坐标生成：

```json
{
  "coord": [86, 12],
  "collision": true,
  "address": ["乔治的公寓", "浴室", "花洒"],
  "tile": "slice-or-recipe-id"
}
```

注意：运行时 `address` 不保存 World 名称。`tile_address_keys` 使用：

```json
["world", "sector", "arena", "game_object"]
```

UI 显示时再把 `definition.world` 放在地址最前面。

`definition.render_manifest` 建议结构：

```jsonc
{
  "schema_version": "ga-map-render/v1",
  "renderer": "TILED_V2",
  "tile_width": 32,
  "tile_height": 32,
  "width": 140,
  "height": 100,
  "sources": [
    {
      "source_id": "source:cute-rpg-field-b",
      "asset_id": null,
      "asset_hash": null,
      "bundled_path": "tilemap/CuteRPG_Field_B.png"
    }
  ],
  "layer_ids": ["layer:bottom-ground", "layer:exterior-ground"]
}
```

URL 由服务端响应时解析，不把 `http://127.0.0.1...` 或绝对文件路径写入 Revision。

---

## 7. Tiled / Maze 导入算法

### 7.1 总体原则

`tilemap.json` 决定视觉图层、Tileset、GID 和翻转；`maze.json` 决定 World/Sector/Arena/Game Object 名称、空间地址和碰撞。两者联合生成 V2 文档。

不得通过手写花洒、床、冰箱列表还原 Ville；不得通过抽象色块替代素材；不得把 `maze.json` 的语义边界当成图片裁切坐标。

建议新增：

```text
generative_agents/services/map_importer.py
generative_agents/services/map_compiler.py
generative_agents/config/map_editor.py
```

导入器和编译器均应是无网络、确定性纯逻辑，便于单元测试。

### 7.2 图层归属映射

导入时先 `trim()` 图层名，解决 `Interior Furniture L2 ` 尾随空格。

| Tiled 图层 | V2 显示层 |
| --- | --- |
| Bottom Ground | MAP |
| Exterior Ground | MAP |
| Exterior Decoration L1 | SECTOR |
| Exterior Decoration L2 | SECTOR |
| Interior Ground | ARENA |
| Wall | ARENA |
| Interior Furniture L1 | GAME_OBJECT |
| Interior Furniture L2 | GAME_OBJECT |
| Foreground L1 | GAME_OBJECT |
| Foreground L2 | GAME_OBJECT |

下列图层不作为视觉素材画到普通画布：

- Collisions：编译碰撞；
- Object Interaction Blocks / Arena Blocks / Sector Blocks / World Blocks：旧语义辅助，V2 以 Maze 地址树为准；
- Spawning Blocks / Special Blocks Registry：运行时扩展，保留导入元数据，不作为用户可编辑图层。

### 7.3 GID 和精确切片

Tiled raw GID 的高三位是翻转标记。算法必须使用无符号 32 位：

```text
raw = uint32(layer.data[index])
gid = raw & 0x1fffffff
flip_h = (raw & 0x80000000) != 0
flip_v = (raw & 0x40000000) != 0
flip_d = (raw & 0x20000000) != 0
```

Tileset 解析：

```text
tileset = first tileset from descending(firstgid) where gid >= firstgid
local_id = gid - tileset.firstgid
col = local_id % tileset.columns
row = floor(local_id / tileset.columns)
x_px = margin + col * (tile_width + spacing)
y_px = margin + row * (tile_height + spacing)
```

必须校验：

- `local_id >= 0` 且 `< tile_count`；
- 像素范围不超过原图；
- imagewidth / imageheight 与真实图片一致；
- `interiors_pt3` 已知底部非 Tile 像素问题沿用回放的受控规范化，不擅自修改其他 Tileset；
- 自动基础切片唯一键为 `(source_id, local_id)`；
- raw GID 和翻转组合保存在 `RecipeEntry`，不能只保存已去标志的 gid。

### 7.4 地址树生成

遍历 `maze.tiles`：

- `address[0]` 聚合为 Sector；
- `address[0:2]` 聚合为 Arena；
- `address[0:3]` 聚合为 Game Object；
- 每个聚合组记录坐标 mask、最小包围矩形和 collision 数；
- root World 使用完整地图范围；
- 导入 ID 使用规范化路径哈希，例如 `sha256(kind + "\0" + path)` 截断后编码，保证重复导入 ID 稳定。

不要直接把名称当主键。名称可以重复和重命名。

### 7.5 Game Object 图形识别

这是避免“原图很具体、实现却很抽象”和“切片范围不对”的关键算法。

1. 只扫描 `GAME_OBJECT` 归属的视觉层。
2. 对每个非空 Tile 建立 `{layer, x, y, raw_gid, tileset, source_col, source_row}`。
3. 在每个视觉层内做 8 邻域连通分量。
4. 两个相邻地图 Tile 只有在以下条件同时满足时才归为同一图形分量：
   - Tileset 相同；
   - 原图 `source_col/source_row` 的位移与地图位移一致；
   - 二者不是被不同翻转规则破坏的连续纹理。
5. 在同一 Arena 内，将视觉分量分配给 Manhattan 距离最近的 Game Object 语义 mask。
6. 只接受距离不大于 4 的唯一最近项；并列最近不猜测，记录 unresolved warning。
7. 同一个对象取得所有最小距离分量，按真实图层 z-index 生成复合配方。
8. 对配方条目做位置归一化，使用 `(layer, dx, dy, raw_gid)` 生成 fingerprint。
9. fingerprint 相同只说明素材配方可复用，不生成“视觉变体”UI，也不合并地址树节点。

对未识别图形：

- 地址节点仍然创建；
- `render_recipe_id = null`；
- 右侧显示“尚未设置对象图形”的简短空态；
- 用户可从素材配方中选择；
- 发布时根据产品策略报 warning 或 error，第一版建议 Game Object 无图形为 warning，缺少运行时语义范围才是 error。

### 7.6 反向引用索引

反向引用不持久化，每次文档变化后增量重建：

```text
source_id -> slice_ids
slice_id  -> recipe_ids + base-map cells
recipe_id -> hierarchy_node_ids + base-map cells
node_id   -> parent/children
```

索引构建结果用于素材右栏“应用情况”和删除保护：

- 有引用的 source 不允许直接删除；
- 有引用的 slice / recipe 删除前必须选择替换或级联清除；
- published Revision 中所有引用不可变。

---

## 8. 前端架构

### 8.1 无构建链拆分

为降低 27B 模型一次修改超大文件的风险，建议保持原生脚本但按职责拆分。所有脚本通过 `window.MapEditorV2` 命名空间协作，并在 `map-workspace.js` 中编排。

```text
generative_agents/web/static/
├─ map-workspace.js              # 地图目录、打开、保存、发布、总编排
├─ map-editor-contract.js        # normalize/migrate/validate client helpers
├─ map-editor-store.js           # 状态、选择、命令历史、dirty
├─ map-editor-canvas.js          # 地图/世界画布渲染和命中测试
├─ map-editor-materials.js       # 原图/切片画布、范围编辑、反向引用
├─ map-editor-tree.js            # 四层树、展开、滚动保持、CRUD
├─ map-editor-inspector.js       # 右栏表单
└─ map-workspace.css             # 保持单一 CSS 入口
```

如果不拆文件，也必须在 `map-workspace.js` 内形成等价类边界，禁止继续使用数百个可变全局函数相互覆盖。

保留：

```javascript
window.MapWorkspace = manager;
```

现有控制台依赖该入口激活地图页面。

### 8.2 Store

建议状态分为三类：

```javascript
state = {
  document,       // MapEditorDocumentV2，唯一作者数据
  revision,       // id, lock_version, state
  selection,      // workspace, nodeId, sourceId, sliceId, recipeId, cell
  viewport,       // zoom, offsetX, offsetY, visibleThrough, semanticVisible
  treeUi,         // expandedIds, scrollByKey
  materialUi,     // expandedSourceId, mode, editDraft
  history,        // command stack
  dirty,
};
```

不要把 `scrollTop`、Canvas context、Image 对象写进 JSON 文档。

### 8.3 命令式撤销/重做

现有 GridEditor 每次快照整个 world；V2 包含大图层数组后成本过高。建议改成命令记录：

```javascript
{
  type: 'UPDATE_NODE_BOUNDS',
  before: { nodeId, bounds },
  after:  { nodeId, bounds }
}
```

首批命令：

- `PAINT_CELLS`；
- `ERASE_CELLS`；
- `CREATE_NODE`；
- `UPDATE_NODE`；
- `DELETE_SUBTREE`；
- `MOVE_NODE`；
- `CREATE_SLICE`；
- `UPDATE_SLICE_RECT`；
- `DELETE_SLICE`；
- `ASSIGN_RECIPE`。

拖动画笔一次 pointer session 合并为一条命令；拖动切片范围一次 pointer session 也合并为一条命令。

### 8.4 Canvas 渲染管线

地图 Canvas：

```text
clear background
→ draw MAP layers
→ if depth >= SECTOR: draw SECTOR layers/nodes
→ if depth >= ARENA: draw ARENA layers/nodes
→ if depth >= GAME_OBJECT: draw GAME_OBJECT layers/nodes
→ if semanticVisible: draw semantic regions
→ draw current selection / hover / handles
```

性能策略：

- 图片加载后缓存 `HTMLImageElement` 或 `ImageBitmap`；
- 每个视觉层使用离屏 Canvas 缓存；
- 单元修改只使相关层的相关脏矩形失效；
- 主绘制由单一 `requestAnimationFrame` 调度，连续事件只标记 dirty；
- 关闭 `imageSmoothingEnabled`；
- 只绘制可见 Tile 范围；
- 文字标签只在语义开启且缩放达到阈值时绘制；
- 大素材原图画布只绘制可见范围，不生成数千个 DOM 切片框。

### 8.5 树滚动稳定性

首选方案：更新选中态时只切换 class，不重建树 DOM。

必须重建时：

```javascript
const key = stableTreeKey(container);
scrollPositions.set(key, container.scrollTop);
renderTree();
requestAnimationFrame(() => {
  container.scrollTop = clamp(saved, 0, container.scrollHeight - container.clientHeight);
});
```

不得在选择节点后调用 `scrollIntoView()`，除非目标节点当前完全不在可见区域；即使调用，也只能使用 `block: 'nearest'`。

原图子树 key 必须包含稳定 `source_id`，不能使用数组下标。

### 8.6 文件上传

导入原图流程：

1. 前端检查扩展名/MIME 并读取图片尺寸；
2. `POST /api/v1/assets` 上传二进制；
3. 服务端返回 `asset_id`、sha256、MIME、size；
4. 前端创建 `MaterialSource` 草稿并把 `asset_hash` 写入 `WorldConfig.assets` 对应引用；
5. 保存地图草稿时由服务端重新校验 Asset 存在、hash 和 MIME 匹配；
6. Revision 只存引用，不存 base64 data URL。

允许：PNG、JPEG、WebP。Tileset 像素图建议 PNG。第一版上限沿用 AssetService 50 MB，同时增加图片最大边长和总像素限制，防止图片解码炸内存。

`WorldConfig.assets` 中的引用必须符合现有 `AssetReference`，例如：

```json
{
  "logical_path": "map/materials/source-2f1c.png",
  "asset_hash": "sha256:<AssetService 返回的 64 位 sha256>",
  "media_type": "image/png",
  "size": 182734
}
```

`MaterialSource.asset_id` 用于当前数据库中的内容读取，`asset_hash` 才是 Revision 的内容身份；服务端保存时必须证明两者指向同一 Asset。

---

## 9. 服务端与 API

### 9.1 保存路径

第一版继续使用整个 World 草稿的乐观锁 PUT：

```http
PUT /api/v1/maps/{map_id}/draft
Content-Type: application/json

{
  "lock_version": 7,
  "world": { ... }
}
```

服务端顺序：

1. `WorldConfig.model_validate`；
2. 若存在 V2 editor，`MapEditorDocumentV2.model_validate`；
3. 规范化 ID、顺序和可选字段；
4. 编译 `definition.tiles` 和 `definition.render_manifest`；
5. 做草稿级校验；
6. 计算 world hash；
7. 使用 `lock_version` 条件更新；
8. 返回新 lock version 和规范化 world。

这样保证浏览器提交的 `definition.tiles` 不能与 editor 文档不一致。服务端编译结果覆盖客户端同名字段。

### 9.2 建议新增资源解析接口

为了不在 Revision 中持久化 URL，建议新增只读接口：

```http
GET /api/v1/maps/{map_id}/draft/editor-resources
```

返回：

```jsonc
{
  "revision_id": "...",
  "lock_version": 7,
  "sources": {
    "source:cute-rpg-field-b": {
      "url": "/generative_agents/frontend/static/assets/village/tilemap/CuteRPG_Field_B.png",
      "etag": "..."
    },
    "source:uploaded-123": {
      "url": "/api/v1/assets/<asset_id>/content",
      "etag": "<sha256>"
    }
  }
}
```

该接口不得修改地图，不需要新数据库表。

### 9.3 发布校验

`publish_draft` 在既有 `_validate_world_definition` 后增加 `_validate_map_editor_v2`：

| code | 级别 | 触发条件 |
| --- | --- | --- |
| `MAP_EDITOR_SCHEMA_INVALID` | ERROR | V2 文档不符合契约 |
| `MAP_EDITOR_ROOT_INVALID` | ERROR | 不是唯一 World 根 |
| `MAP_EDITOR_PARENT_INVALID` | ERROR | 父子层级不合法或存在环 |
| `MAP_EDITOR_NODE_OUT_OF_BOUNDS` | ERROR | 节点超出地图 |
| `MAP_EDITOR_CHILD_OUTSIDE_PARENT` | ERROR | 子节点完全或部分越出父级 |
| `MAP_EDITOR_SIBLING_NAME_DUPLICATED` | WARNING | 同父级同名 |
| `MAP_EDITOR_SAME_LEVEL_OVERLAP` | WARNING | 同层空间范围重叠 |
| `MAP_MATERIAL_SOURCE_MISSING` | ERROR | source 的 Asset/内置文件不存在 |
| `MAP_MATERIAL_HASH_MISMATCH` | ERROR | Asset hash 不匹配 |
| `MAP_MATERIAL_SLICE_OUT_OF_BOUNDS` | ERROR | 切片越界 |
| `MAP_MATERIAL_RECIPE_REFERENCE_MISSING` | ERROR | 配方引用不存在切片 |
| `MAP_NODE_RECIPE_REFERENCE_MISSING` | ERROR | 节点引用不存在配方 |
| `MAP_TILED_GID_UNRESOLVED` | ERROR | raw GID 找不到 Tileset/切片 |
| `MAP_RUNTIME_GRID_INCOMPLETE` | ERROR | 编译后不是 width × height 唯一格 |
| `MAP_GAME_OBJECT_GRAPHIC_MISSING` | WARNING | GO 有语义但无图形 |
| `MAP_IMPORT_VISUAL_UNRESOLVED` | WARNING | 导入视觉分量无法唯一分配 |

草稿保存允许 warning；发布允许 warning 但响应中必须保留。Error 阻止发布。

### 9.4 Revision 与冲突处理

- 每次保存必须发送当前 lock version；
- 409 时前端停止自动覆盖，显示“草稿已被其他请求修改”；
- 提供“重新载入”主操作；
- 若本地 dirty，允许先下载本地 V2 JSON 备份；
- 不实现静默 last-write-wins；
- 发布成功后编辑器只读，继续修改必须 fork 新修订；
- 发布 Revision 的 source、slice、recipe、node 和编译 tiles 一起进入 world hash。

---

## 10. 运行时与回放闭环

### 10.1 Maze

`Maze` 不直接理解 V2 editor。它继续读取编译后的：

- `definition.world`；
- `definition.size`；
- `definition.tile_size`；
- `definition.tile_address_keys`；
- `definition.tiles[].coord/address/collision`。

因此地图编辑功能不能只保存视觉 UI 状态，必须在服务端编译出完整运行时网格。

地址编译优先级：

1. 找到覆盖坐标的 Sector；
2. 在该 Sector 中找覆盖坐标的 Arena；
3. 在该 Arena 中找覆盖坐标的 Game Object；
4. 按连续前缀输出地址；不允许缺 Sector 但存在 Arena；
5. 多个同级命中按确定性顺序选择并产生 overlap warning。

### 10.2 回放

当前 `replay_v2._world_descriptor` 只对 `world_key == "the-ville"` 返回完整 Tiled 资源，其他地图标为 `WORLD_RENDER_ASSET_UNRESOLVED`。V2 后期必须调整为：

1. 若 Revision 有 `render_manifest`，解析其中的 bundled/source Asset；
2. 返回 `renderer: TILED_V2`、图层、Tileset URL 和 hash；
3. replay player 不再依赖固定 `TILESET_NAMES`，从 manifest 动态加载；
4. 无 render manifest 的旧地图继续使用 `SPATIAL_GRID` 或旧 fallback；
5. 回放和地图编辑器必须使用同一 Revision 的渲染身份，不能读取“当前最新地图”；
6. 发布后的素材内容用 hash/ETag 不可变缓存。

本轮若工期必须分段，编辑器 UI、编译 `definition.tiles` 是 P0；自定义 Revision 的 Phaser 回放渲染是 P1，但文档和数据契约必须预留，不能把 URL 写死为 Ville。

---

## 11. 迁移与兼容策略

### 11.1 不需要立即新增业务表

V2 作者文档存入 `world_map_revisions.world_json`，素材二进制复用 `assets` 表，因此核心实现不要求新增表。

只有在后续证实单个 world JSON 过大、需要服务器协同编辑时，才考虑把可变图层分块拆表。当前 140×100 规模不要提前引入复杂分片存储。

### 11.2 Editor v1 到 v2

新增幂等迁移函数：

```python
def migrate_editor_document(world: WorldConfig) -> WorldConfig:
    ...
```

规则：

- 已是 `ga-map-editor/v2`：深拷贝规范化后返回；
- Ville 内置地图：从包内 `tilemap.json + maze.json` 确定性导入；
- 旧 `editor.palette/cells` 地图：每个 palette 项生成颜色型素材源/切片或兼容配方，cells 转为 MAP 层；
- 旧 `spatial_scene.placements`：不删除，尽可能转换为 Game Object 节点；无法转换的放入 `extensions.legacy_spatial_scene` 并给 warning；
- 原 Revision 读取不自动落库；只有用户首次保存草稿时写入 V2；
- 已发布旧 Revision 永远不原地改写。

### 11.3 旧空间资产能力

- 保留 `/api/v1/spatial-assets`、数据库表和相关运行时兼容，避免破坏其他页面或历史 Revision；
- 新地图页面不加载 `spatial-asset-workspace.js` 的 UI；
- 不在本任务中删除旧后端，除非有独立清理任务和完整依赖证明；
- 旧接口存在不等于新 UI 必须展示。

---

## 12. 实施阶段

每阶段完成后运行定向测试并提交可审查的小范围变更。不要把所有逻辑一次性塞进一个 5,000 行脚本。

### Phase 0：冻结现状与契约测试

目标：先把最终产品约束写成失败测试。

任务：

- 新增 `tests/foundation/test_map_editor_v2_contract.py`；
- 新增 `tests/foundation/test_map_editor_ville_import.py`；
- 扩展 `test_map_workspace_frontend.py`；
- 添加源文件静态断言：三个 Tab 存在；对象库、视觉变体、图层按钮、空间资产按钮不存在；
- 记录 Ville 固定统计值；
- 保留现有地图生命周期测试。

退出条件：测试能准确描述新需求，并且旧实现只因未实现 V2 而失败。

### Phase 1：契约、导入器、编译器

任务：

- 新建 `config/map_editor.py`；
- 实现 Tiled GID 解析和 flip bit；
- 实现 Ville material source/slice 索引；
- 实现四层树导入；
- 实现 Game Object 视觉分量和配方识别；
- 实现 V2 → runtime tiles 编译；
- 实现所有服务端校验 code；
- 为导入结果生成确定性 hash 测试。

退出条件：无浏览器也能从真实两份 JSON 得到可验证 V2 文档和完整 14,000 Tile 运行时网格。

### Phase 2：地图服务和资源解析

任务：

- 在 `WorldMapService.update_draft` 中解析、规范化、编译 V2；
- 在发布链路加入 V2 校验；
- 增加 editor-resources 只读接口；
- 复用 AssetService 上传原图；
- 校验 hash、MIME、尺寸、越界；
- 保持 lock conflict 合同。

退出条件：API 测试覆盖保存、冲突、发布、缺资源、切片越界和不可变 Revision。

### Phase 3：三 Tab 外壳和状态管理

任务：

- 把旧地图编辑区域替换为高保真三栏 DOM；
- 顶部只保留地图/世界/素材；
- 建立 Store、Selection、Viewport、dirty；
- 接通保存/发布；
- 实现统一字号和检查器 header 布局；
- 删除说明性 UI。

退出条件：页面能打开真实草稿，三个工作区切换不丢失选择和滚动位置，无控制台错误。

### Phase 4：素材工作区

任务：

- 原图/切片树；
- 复合配方根；
- 原图总览与全部切片框；
- 切片居中预览；
- 手动范围移动/缩放；
- 导入原图；
- 反向应用列表；
- 自动索引基础切片只读；
- 滚动条回归测试。

退出条件：可正确核对 Ville 1,272 个唯一 GID，抽查长图 interiors_pt1～5 的裁切坐标无偏移。

### Phase 5：世界地址树

任务：

- 四层树渲染和展开；
- 当前层级新增；
- 选中定位；
- 节点检查器编辑地址、bounds、semantic；
- 节点重挂父级；
- 复制/删除菜单；
- 同层重叠和父级越界提示；
- Game Object 直接引用 render recipe。

退出条件：World/Sector/Arena/GO CRUD 闭环，不出现对象库、视觉变体或第五层。

### Phase 6：地图 Canvas

任务：

- 真实 Tileset 图形渲染；
- 底图画笔/橡皮；
- 4 级累计显示；
- 语义覆盖在地图和世界 Tab 均可用；
- 语义关闭时无地址文字；
- 选择命中、DPR、缩放、平移、适配；
- 命令式撤销/重做；
- 离屏层缓存和可见范围裁剪。

退出条件：140×100 Ville 操作流畅，视觉与原始 Tiled 地图一致，无明显抽象色块替代。

### Phase 7：保存、发布、回放资源

任务：

- 保存前前端轻校验；
- 服务端权威编译；
- 409 冲突处理；
- 发布 warning/error；
- fork 新 Revision；
- render_manifest 接入 Replay V2；
- 动态 Tileset 预加载；
- 旧地图 fallback。

退出条件：编辑 → 保存 → 刷新 → 发布 → 运行/回放使用同一 Revision，语义和图形身份不漂移。

### Phase 8：浏览器验收和清理

任务：

- Playwright 真实浏览器用例；
- 1280×720、1440×900、1920×1080 布局；
- 无横向遮挡；
- 长标题省略；
- 键盘操作和焦点；
- 清理旧 DOM、死代码和重复 CSS；
- wheel 包含新增静态脚本和素材。

退出条件：定向测试、全量测试、wheel 安装后脱离源码启动测试全部通过。

---

## 13. 测试方案

### 13.1 单元测试

- raw GID 去 flip bit；
- 8 种翻转组合；
- firstgid 边界；
- local ID → col/row/pixel rect；
- spacing/margin；
- 越界切片；
- 地址聚合和稳定 ID；
- 节点父子合法性和环检测；
- 语义命中优先级；
- 同层 overlap warning；
- recipe fingerprint 确定性；
- V2 编译 14,000 个唯一坐标；
- 重命名父节点后地址派生正确；
- 同一配方被多个 GO 节点复用。

### 13.2 服务测试

- 打开 Ville 草稿返回 V2；
- 保存后 lock version +1；
- 旧 lock version 返回 409；
- published Revision 不可更新；
- 上传相同原图去重；
- 伪造 asset hash 被拒绝；
- source 缺失阻止发布；
- warning 不阻止保存；
- error 阻止发布；
- fork 后原发布版本 hash 不变；
- 旧 editor v1 只在草稿保存时迁移。

### 13.3 前端静态合同

断言：

- `地图`、`世界`、`素材`；
- `显示至` 和 `语义`；
- 四层地址树；
- `对象检查器`；
- 原图/切片树；
- 不出现 `Game Object 对象库`；
- 不出现 `视觉变体`；
- 不出现地图工具 `图层`；
- 不出现地图工具 `空间资产`；
- 不出现 `1,272 / 1,272 个 Ville 基础 Tile 已准确索引`；
- JS 全部通过 `node --check`；
- HTML 无重复 id。

注意：不要仅凭源文件搜索断定 UI 不存在，因为隐藏模板也会命中；关键断言应结合 DOM 和运行态。

### 13.4 Playwright 端到端

至少覆盖：

1. 打开 Ville 地图草稿，默认显示第 4 层；
2. 切换显示深度 1/2/3/4，像素采样或截图确认累计显示；
3. 在地图 Tab 开启语义，看到语义框；关闭后无地址文字；
4. 在世界 Tab 展开 World → Sector → Arena → GO；
5. 将树滚动到下方，点击子节点，断言 `scrollTop` 未归零；
6. 收起/展开根节点；
7. 新建 Sector，右侧修改名称/bounds/semantic 并保存；
8. 新建 Arena 和 Game Object；
9. 点击树节点，画布定位且右栏同步；
10. 点击重叠区域，默认选择最深层；
11. 素材 Tab 点击原图，完整原图及多个切片框可见；
12. 点击切片，只居中显示切片；
13. 手动切片拖动和 resize，字段同步；
14. 点击切片后素材树子滚动条不回顶；
15. 右栏应用情况能跳转到引用节点；
16. 上传 PNG，保存，刷新后仍可显示；
17. 制造第二客户端更新，当前页面保存得到 409；
18. 发布 warning 可见、error 阻断；
19. 全程 console error 为 0；
20. 检查器标题和图标不重叠。

### 13.5 性能门槛

在常规开发机、140×100 Ville 上：

- 首次可交互时间目标 < 2.5s（本地服务、缓存冷）；
- 平移/缩放目标接近 60 FPS，最低不持续低于 30 FPS；
- 单次 pointermove 主线程任务 < 16ms；
- 切换树节点不解码重复图片；
- 选择节点不重新创建 1,272 个切片 DOM；
- undo 历史不保存 30 份完整大地图 JSON；
- source 图片失败时有局部错误态，不使整个工作区白屏。

---

## 14. 验收清单

### 信息架构

- [ ] 顶部只有地图 / 世界 / 素材。
- [ ] 地址树只有 World / Sector / Arena / Game Object。
- [ ] 无第五层、无对象库、无视觉变体。
- [ ] 地图是第一层基座，2～4 层累计叠加。

### 地图

- [ ] 使用真实 Tileset 图形。
- [ ] 显示至 1/2/3/4 正确。
- [ ] 语义在地图 Tab 也能覆盖。
- [ ] 语义关闭后无自行绘制的地点文字。

### 世界

- [ ] 树新增按钮随层级变化。
- [ ] 点击树节点即定位。
- [ ] 检查器可编辑地址、X/Y/W/H、空间语义。
- [ ] 检查器只保留保存主按钮。
- [ ] 跨层重叠正常，同层重叠有 warning。

### 素材

- [ ] 原图与切片是树结构。
- [ ] 原图模式显示全部切片框。
- [ ] 切片模式居中显示切片。
- [ ] 手动切片可移动、resize、保存。
- [ ] tilemap.json 精确索引公式正确。
- [ ] 长图切片不偏移。
- [ ] 应用列表只在右侧。
- [ ] 不显示 100% 覆盖率卡片。

### 交互质量

- [ ] 树和子树滚动不回顶。
- [ ] 根节点可收起。
- [ ] 字号可读。
- [ ] 右侧图标和标题不重叠。
- [ ] 无多余说明卡片。
- [ ] 刷新后保存内容一致。

### 工程质量

- [ ] 保留 Revision/lock/publish 不变量。
- [ ] Asset 不存 base64，不信任客户端 hash。
- [ ] 服务端权威编译 runtime tiles。
- [ ] 发布版本不可变。
- [ ] 旧地图和旧 API 有兼容路径。
- [ ] wheel 安装包包含新增静态资源。
- [ ] 定向测试和全量测试通过。

---

## 15. 风险与规避

| 风险 | 后果 | 规避 |
| --- | --- | --- |
| 继续在一个超大 JS 中用函数覆盖 | 难维护、容易回归旧术语 | 按模块拆分，Store 单一数据源 |
| 只在前端维护 tiles | 保存后 Maze 与视觉不一致 | 服务端确定性编译 |
| 把地址名当 ID | 重命名导致引用断裂 | 稳定 ID + 派生地址 |
| 把 GO 复用做成对象库/变体 | 违背最终 UX | 共享 recipe，不增加 UI 层 |
| 根据语义 bbox 直接裁图片 | 图形范围错误 | 视觉连通分量 + 原图邻接 + 唯一最近分配 |
| 每次选择重建完整树 | 滚动回顶 | keyed update 或显式恢复 scrollTop |
| 保存 data URL | Revision 巨大、hash 不稳 | AssetService + 引用 |
| 固定 Ville Tileset 列表 | 自定义地图回放失败 | render manifest 动态资源 |
| 删除旧 spatial asset 后端 | 破坏历史 Revision | UI 隐藏，后端兼容保留 |
| 模型顺手清理脏工作树 | 覆盖用户工作 | 只改任务文件，先记录 git status |

---

## 16. 完成定义

只有同时满足下列条件才算完成，不以“页面看起来接近”为完成：

1. 高保真中的三个工作区均落入正式控制台；
2. Ville 使用真实素材和精确 GID 索引；
3. 四层地址节点可增、改、移动、复制、删除；
4. 地图底图和上层节点能累计显示；
5. 语义在地图/世界工作区都能正确叠加；
6. 素材原图/切片/配方/应用关系闭环；
7. 保存刷新不丢数据，乐观锁冲突不覆盖他人修改；
8. 服务端能编译出 Maze 可消费的完整 tiles；
9. 发布 Revision 不可变并可被运行/回放引用；
10. 不重新出现已删除的对象库、视觉变体、图层、空间资产、Agent 和说明卡片；
11. 单元、API、前端合同、Playwright、打包测试全部通过；
12. 没有控制台错误、明显滚动跳变、标题图标重叠和字体过小问题。

---

## 附录 A：给 Claude Code / Qwen2.8 27B（256K 上下文）模型的实施 Prompt

复制下面 Prompt 执行本方案；实施时以本文为产品与工程真值来源。

```text
你现在位于 GenerativeAgentsCN 仓库根目录。请完整实现 docs/map-editor-technical-implementation-plan.md 中定义的“地图编辑器 V2”。

开始前必须：
1. 完整阅读 CLAUDE.md。
2. 完整阅读 docs/map-editor-technical-implementation-plan.md。
3. 阅读 docs/prototypes/map-design-interaction.html，理解视觉和交互，但若原型残留旧术语或旧逻辑，以技术实现方案中的“不可回退的产品决策”为准。
4. 执行 git status --short，确认工作树已有大量用户改动。不得 reset、checkout、清理、覆盖或顺手重构不相关文件。
5. 阅读现有 map-workspace.js / map-workspace.css / experiment-console.html / services/maps.py / web/app.py / config/schema.py / runtime/replay_v2.py 及相关测试，沿用现有架构。

硬性产品约束：
- 顶部地图编辑主 Tab 只能是“地图 / 世界 / 素材”。
- 世界使用一棵 World → Sector → Arena → Game Object 四层地址树。
- Game Object 直接位于 Arena 下，不增加第五层。
- 不得出现 Game Object 对象库、对象库抽屉、视觉变体、独立语义树、图层按钮、空间资产按钮或 Agent 绑定。
- 对象检查器编辑当前树节点的地址、X、Y、宽度、高度、空间语义；新建从左树按钮完成；检查器底部只保留保存主操作。
- 显示至第 1/2/3/4 层必须累计显示：地图底图；+Sector；+Arena；+Game Object。
- 语义是画布叠加开关。在地图 Tab 和世界 Tab 都必须有效；关闭时不画地点文字；开启时不替换左侧地址树/工具。
- 素材左栏必须是原图→切片树，加独立复合配方根；点击原图显示完整原图和全部切片框，点击切片居中显示切片；手动切片可拖动和 resize；应用关系只在右栏。
- 选择树/素材子节点不能让滚动条回到顶部，根节点可展开收起。
- 不显示覆盖率“100% / 1,272 / 1,272”卡片和大段说明性文案。
- 正文/控件字号可读，右侧检查器图标和标题不得重叠。

硬性工程约束：
- 不引入 React/Vue 或新的前端构建链，沿用 FastAPI + 原生 HTML/CSS/JS + Canvas 2D。
- 保留 window.MapWorkspace 和现有地图目录/Revision 生命周期。
- 复用 WorldMapRevision、lock_version、发布不可变机制和 /api/v1/assets。
- V2 作者数据存入 WorldConfig.definition.editor；由服务端权威编译 definition.tiles 和 render_manifest。
- 新建严格 Pydantic 契约、确定性 Tiled/Maze 导入器、编译器和发布校验。
- raw Tiled GID 必须保留并正确处理 0x80000000 / 0x40000000 / 0x20000000 翻转位。
- Ville 导入必须由真实文件计算出 140×100、17 layers、18 tilesets、26,414 视觉摆放、1,272 唯一视觉 GID、19 Sector、63 Arena、222 Game Object 路径；不得硬编码为 UI 数据。
- 不把图片存成 base64；上传走 AssetService，Revision 存稳定引用。
- 不删除旧 spatial asset 后端；新 UI 不展示即可。
- 不修改已发布 Revision，不使用 last-write-wins。
- 新增静态文件必须进入 wheel package-data，并补脱离源码启动/读取测试。

实施顺序严格按文档 Phase 0→8：
1. 先补合同和失败测试。
2. 实现 map_editor Pydantic 契约、Ville 导入器、编译器和纯函数测试。
3. 接入地图服务保存/校验/发布和资源解析。
4. 实现三 Tab 外壳和 Store。
5. 实现素材树、精确切片与范围编辑。
6. 实现四层地址树与节点检查器。
7. 实现真实 Tile Canvas、累计显示和语义叠加。
8. 接通保存、冲突、发布、Replay render manifest。
9. 完成 Playwright、性能、打包和全量回归。

工作方式：
- 每个阶段先查看相关现有测试和调用者，再修改。
- 保持改动局部，不删除用户现有未提交工作。
- 不靠字符串 replace 修补 UI 术语；在正式数据模型和渲染函数中删除旧概念。
- 不用手写 Ville 对象清单；必须由 tilemap.json + maze.json 导入。
- 优先使用稳定 ID、派生地址、共享 render recipe，避免名称充当主键。
- 遇到同层语义重叠给 warning；跨层嵌套正常；子节点越出父级发布 error。
- 任何跳过项都必须说明原因、风险和后续文件位置，不能静默省略。

验证至少执行：
- node --check 所有新增/修改 JS；
- pytest tests/foundation/test_map_editor_v2_contract.py；
- pytest tests/foundation/test_map_editor_ville_import.py；
- pytest tests/foundation/test_map_workspace_frontend.py；
- pytest tests/foundation/test_world_maps.py；
- 相关 API、Replay、wheel 测试；
- 最终 pytest 全量；
- Playwright 实测地图/世界/素材关键路径，确认 console errors=[]。

最终输出必须包含：
1. 已完成的阶段和关键架构决策；
2. 修改/新增文件清单；
3. 数据迁移与兼容行为；
4. 实际运行的测试命令和结果；
5. 尚未完成的内容（若有）及明确原因；
6. 不要声称“完成”，除非文档第 14 节验收清单和第 16 节完成定义均满足。

现在开始。在同一次任务中先完成仓库检查、阅读和 Phase 0 测试设计，随后按阶段持续实施；不要停在计划说明或 Phase 0，除非遇到需要用户授权的破坏性操作或无法从仓库判断的实质性产品冲突。
```
