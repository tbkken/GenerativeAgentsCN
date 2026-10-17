# 斯坦福小镇完整地图资料包

本目录准备的是经典中文 `example` 25 人场景所用完整小镇地图，供主 Agent 通过系统界面的“地图 → 导入资源包”上传。这里没有创建公共资源、实验或 Run，没有访问 Studio 数据库、HTTP 或 `var/`。

可导入文件：[stanford-town-original-map-config.zip](stanford-town-original-map-config.zip)。这是 `ga-package` v2 的 `config` 资源包，只包含地图与自身附件；不是已配置实验包。地图稳定键为 `stanford-town-original`，显示名为“斯坦福小镇·完整原版地图”。

## 地图内容与校验

| 项目 | 数值 |
| --- | --- |
| 地图网格 | 宽 140 × 高 100 格，共 14,000 格 |
| 原始切片 | 32 × 32 像素；完整画布 4,480 × 3,200 像素 |
| 层级 | 1 World、19 Sector、63 Arena、222 Game Object |
| 碰撞格 | 2,064，与经典源数据逐坐标相同 |
| 视觉 | 原始 10 个视觉层，1,272 个已使用切片，13 张实际使用图集 |
| 附件 | 全部 16 张源 PNG、原始 Tiled JSON、经典 maze JSON，共 18 项 |
| ZIP 大小 | 3,690,857 字节 |
| ZIP SHA256 | `e0a46214fc9ff235db6b120192b1ed066bba5362aa13ee5b29484b959be561e3` |
| 包根哈希 | `64e536aae8ec7581471bbc62a12d4635bd7e8f5bd1feb88f0521d3e06e732afb` |

[离线核验报告](offline-verification.json)记录每个附件的原始绝对路径、大小与 SHA256。已通过资源协议校验、ZIP 读回与完整性校验、14,000 格地址和碰撞逐格比较、四层语义索引校验、6,759 个精确区域 mask 单元校验、全部节点及父子矩形边界校验。10 层的 GID 数组与原始 Tiled 数据逐项相同，保留翻转信息。

这些是离线资料校验，不代替 UI 上传、地图视觉检查、实验预检、人物原坐标核对或运行验收。原版对象保持静态，未人为绑定对象 Skill；新增设施自主响应不属于这份地图的原始内容。

## 来源基准

源下载目录为：

`C:\Users\D304\Downloads\GenerativeAgentsCN-main\GenerativeAgentsCN-main\generative_agents\frontend\static\assets\village`

图像和 `tilemap/tilemap.json` 原始字节直接取自该目录。16 张 PNG 与当前产品的 `src/generative_agents/ga_studio/bundled/maps/village/tilemap/` 逐文件字节一致；两个 Tiled JSON 因格式字节不同，但解析后的完整结构相同。本包保存下载目录 Tiled 原始文件，其 SHA256 为 `baa342b46b3e12c0a213c3ecab88c645fdc5e000ac7812a9a3de150ac062f6ef`。

经典语义及碰撞基准采用当前产品 `src/generative_agents/ga_studio/bundled/maps/village/maze.json`，SHA256 为 `e4c7f70f9724be54c3348b35bf571f1bc7a67b0b3f5bea2d573af9d412848930`。下载目录 `maze.json` 的 SHA256 为 `5724aea03bf4de8cf4aba5e76df39d7680e31d78fae1e4c80bd9cfcf1aa3d5d2`；两者解析后只有 `[74,20]` 这一格地址不同，全部碰撞格相同。

下载项目的 `docs/book/manuscript/part_04/chapter_27.md` 第 27.3 节将林晓和社区公告板列为新增扩展示例；第 27.7 节明确把 `[74,20]` 的旧地址“霍布斯咖啡馆 → 咖啡馆”扩展为“霍布斯咖啡馆 → 咖啡馆 → 社区公告板”，并说明没有修改 `tilemap.json`。本案例选定扩展前 25 人基准，因此该格保持咖啡馆普通地面，未加入第 223 个公告板对象。这是明确的来源选择，不是系统缺陷修复。

## 协议转换范围

[准备脚本](prepare_map_config.py)只调用读取 bundled 材料的纯转换函数，以及 Protocol 的文件打包、完整性和空间校验函数。地图视觉文档复用 `fresh_ville_editor_document()`，未重绘或缩小地图。素材附件通过包内相对路径引用，不包含数据库资源 ID。

原 `maze.json` 用 4,201 条稀疏记录表达地址和碰撞。原项目 `modules/maze.py` 的 `Tile` 为未声明格默认补充 World 地址、可通行状态；本包仅将这一默认显式展开为 14,000 格，以满足当前文件协议。语义根仍保留来源名称 `the Ville`，避免改变人物原有空间地址；公共地图显示名为中文。源 `maze.size=[100,140]` 表示高、宽，与 Tiled 的 `width=140,height=100` 一致。

原始 Tiled 文件作为来源附件完整保留，包括其作者路径和辅助标记层。当前编辑器渲染使用可移植文档中实际引用的 13 张图集与 10 个视觉层；其余 3 张源 PNG 也完整随包保留。附件中的原图、Tiled 和 maze 原始字节没有被改写。

导入后仍应在 UI 核对：地图尺寸与层级数、原小镇画面与前景遮挡、碰撞边界、25 名人物的原坐标和精确地址。不得为迎合位置选择器自动中心点而修改原地图边界或出生坐标。
