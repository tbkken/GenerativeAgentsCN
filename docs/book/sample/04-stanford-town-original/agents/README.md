# 经典 25 人：人物资料与原图副本

本目录是界面配置的作者资料；资料准备状态不代表已经导入系统。`roster-reference.json` 与各人物 `source-reference.json` 为历史来源与字段映射，不是 ga-package，也不是当前实验状态。另已生成可经界面导入的 [v2 人物资源包](stanford-town-original-agents-config.zip) 和 [实验位置合并文件](experiment-placement-merge.json)，操作步骤见 [界面导入说明](ui-import-guide.md)。基准为本地中文项目 `results/compressed/example`，不称为论文官方原始实验包。

[完整映射清单](roster-reference.json)保存 25 人的中文名、英文名、建议稳定键、完整基础人设、初始坐标和四层地址。各人物目录的 `source-reference.json` 另保留当前源文件原样字段、逐字段来源、经典空间记忆与素材哈希。

源资产目录本身使用中文键；英文全名来自书稿第 15 章人物表，小写连字符稳定键是本次准备时派生。人物头像与行走图均原样复制，未缩放或重绘：头像 32×32，行走图 96×128（3 列×4 行，每帧 32×32）。[源精灵帧表](sprite-source-reference.json)保留原帧定义。

原 25 人的人设采用 example 中的快照。与当前资源相比恢复 5 个字段：汤姆 currently；伊莎贝拉 learned、currently；山姆 learned、currently。三人的已知咖啡馆对象中移除书稿第 27 章后来添加的社区公告板。其余已知空间来自当前源 agent.json，缺少完整历史空间快照，不能声称已经与更早官方资产逐字一致。

初始坐标全部与 example 相同，四层地址按源 maze 的对应 tile 读取；所有 25 人初始位置都在自己的床上。源世界名 `the Ville` 在此保留用于核对，系统中如统一中文化为“小镇”，需同步转换所有地址与空间记忆根节点。

共享源参数为方框感知、视野 8、注意力带宽 8；源端没有 Agent 仿真移动速度参数。系统坐标单位、速度和感知形状的映射要显式记录，不能把回放动画速度当作原仿真速度。

| 中文显示名 | 文档英文全名 | 建议稳定键 | 原始坐标 | 原始初始地址 |
| --- | --- | --- | --- | --- |
| 阿伊莎 | Ayesha Khan | `ayesha-khan` | [118, 61] | the Ville / 奥克山学院宿舍 / 阿伊莎的房间 / 床 |
| 克劳斯 | Klaus Mueller | `klaus-mueller` | [126, 46] | the Ville / 奥克山学院宿舍 / 克劳斯的房间 / 床 |
| 玛丽亚 | Maria Lopez | `maria-lopez` | [123, 57] | the Ville / 奥克山学院宿舍 / 玛丽亚的房间 / 床 |
| 沃尔夫冈 | Wolfgang Schulz | `wolfgang-schulz` | [107, 62] | the Ville / 奥克山学院宿舍 / 沃尔夫冈的房间 / 床 |
| 梅 | Mei Lin | `mei-lin` | [90, 74] | the Ville / 林氏家族的房子 / 梅和约翰的卧室 / 床 |
| 约翰 | John Lin | `john-lin` | [91, 74] | the Ville / 林氏家族的房子 / 梅和约翰的卧室 / 床 |
| 埃迪 | Eddy Lin | `eddy-lin` | [93, 74] | the Ville / 林氏家族的房子 / 埃迪的卧室 / 床 |
| 简 | Jane Moreno | `jane-moreno` | [72, 74] | the Ville / 莫雷诺家族的房子 / 汤姆和简的卧室 / 床 |
| 汤姆 | Tom Moreno | `tom-moreno` | [73, 74] | the Ville / 莫雷诺家族的房子 / 汤姆和简的卧室 / 床 |
| 卡门 | Carmen Ortiz | `carmen-ortiz` | [57, 74] | the Ville / 塔玛拉和卡门的家 / 卡门的房间 / 床 |
| 塔玛拉 | Tamara Taylor | `tamara-taylor` | [54, 74] | the Ville / 塔玛拉和卡门的家 / 塔玛拉的房间 / 床 |
| 亚瑟 | Arthur Burton | `arthur-burton` | [53, 14] | the Ville / 亚瑟的公寓 / 主人房 / 床 |
| 伊莎贝拉 | Isabella Rodriguez | `isabella-rodriguez` | [72, 14] | the Ville / 伊莎贝拉的公寓 / 主人房 / 床 |
| 山姆 | Sam Moore | `sam-moore` | [36, 65] | the Ville / 摩尔家族的房子 / 主人房 / 床 |
| 詹妮弗 | Jennifer Moore | `jennifer-moore` | [37, 65] | the Ville / 摩尔家族的房子 / 主人房 / 床 |
| 弗朗西斯科 | Francisco Lopez | `francisco-lopez` | [16, 32] | the Ville / 艺术家共居空间 / 弗朗西斯科的房间 / 床 |
| 海莉 | Hailey Johnson | `hailey-johnson` | [26, 32] | the Ville / 艺术家共居空间 / 海莉的房间 / 床 |
| 拉吉夫 | Rajiv Patel | `rajiv-patel` | [26, 18] | the Ville / 艺术家共居空间 / 拉吉夫的房间 / 床 |
| 拉托亚 | Latoya Williams | `latoya-williams` | [16, 18] | the Ville / 艺术家共居空间 / 拉托亚的房间 / 床 |
| 阿比盖尔 | Abigail Chen | `abigail-chen` | [36, 18] | the Ville / 艺术家共居空间 / 阿比盖尔的房间 / 床 |
| 卡洛斯 | Carlos Gomez | `carlos-gomez` | [94, 18] | the Ville / 卡洛斯的公寓 / 主人房 / 床 |
| 乔治 | Giorgio Rossi | `giorgio-rossi` | [86, 18] | the Ville / 乔治的公寓 / 主人房 / 床 |
| 瑞恩 | Ryan Park | `ryan-park` | [65, 19] | the Ville / 瑞恩的公寓 / 主人房 / 床 |
| 山本百合子 | Yuriko Yamamoto | `yuriko-yamamoto` | [28, 65] | the Ville / 山本百合子的房子 / 主人房 / 床 |
| 亚当 | Adam Smith | `adam-smith` | [20, 65] | the Ville / 亚当的家 / 主人房 / 床 |
