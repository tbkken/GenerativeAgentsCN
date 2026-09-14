# 对象 Skill 改造后的历史回放加载回归

日期：2026-09-13。用户已明确授权工程子 Agent 定位并修复；主 Agent 负责浏览器复现、Web 重启与原路径验收。

## 修复前浏览器证据

- 实验：案例1：陈明远的晨间生活（进度便签）。
- Experiment：`b1c550ec-1599-4cb8-a4c8-7f303c7344df`。
- 原 Run：`4f5713c4-7b76-4afe-8f3a-b4bd36ee2a28`，已完成 32/32 步。
- 进入实验概览：提示“对象 Skill 列表刷新失败，HTTP 422”。
- 进入实验结果：Run 选择器正常列出原 Run，但画布空白、播放按钮禁用、Agent 列表持续显示“正在读取”。浏览器诊断提示“Run 包不完整或正在移入回收站，无法读取结果”；此提示未准确表达实际原因。
- 进入实验技能页：`Skill 列表加载失败 invalid Skill package registry: 1 validation error for SkillPackageRegistry passive_roots Extra inputs are not permitted [type=extra_forbidden, input_value=[], input_type=list]`。
- 预期：完整保存的历史 Run 可根据已提交事实播放；查看实验内 Skill 文本不触发重新运行。

第二个浏览器对照为案例2：穿过门口，到阅读角看书，Experiment `c63b2d81-433a-4c2f-9d75-b44bcf04e318`，原 Run `b4b4ca09-87bd-4980-bdc5-8146474d6225`，已完成 8/8 步。同样复现概览 HTTP 422 与回放空白、播放禁用。

## 已确认的根因与设计边界

对象 Skill 能力改造把包内注册表字段从 `passive_roots` 改为 `object_roots`，并明确停止接受旧执行合同。旧包即使没有对象 Skill，仍保存 `passive_roots: []`，因此也触发严格模型校验错误。变更由摄像头所需的通用 Game Object 能力引入，并非摄像头的自然语言 SOP 导致。

问题的传播来自读取与执行边界耦合：ReplayReader 的 Run 校验会继续调用完整实验执行语义校验，最终解析当前 SkillPackageRegistry。实验内 Skill 查看也受到同类运行校验影响。新执行合同的拒绝错误因此阻挡历史事实回放及文本审计。

修复原则：保留身份、路径与完整内容哈希校验；回放只读取已提交事实及包内资料，不解析或运行当前 Skill 合同。新建运行与恢复继续执行当前完整语义校验。不得修改历史实验、Run、注册表或 Skill，不加入旧字段转换、不重新执行模型来重建回放。

## 验收状态

工程修复已通过 49 项协议、回放、资源编辑、对象运行/恢复回归。覆盖当前执行校验仍拒绝非当前注册表、Run 目录及 `.garun` 只读回放、Skill 查看、包/帧损坏、身份不符及非法入口拒绝。

主 Agent 于北京时间 2026-09-13 23:52 使用仓库 `restart-web.bat` 隐藏重启 Web，健康检查通过。Web PID 从 `61096` 变为 `65660`。Runtime PID `64744` 与创建时间 `2026-09-13T22:19:58.998322+08:00` 保持不变；没有暂停或重启原交通 Run，重启前其进度为 45/60。

浏览器已确认案例2原 Run 从 Step 1 自动播放至 Step 8，末帧为 09/09 09:07，显示林晨在窗边阅读桌旁写笔记，地图与人物图片可见，按钮变为“重新播放”。没有创建新 Run。

案例1原 `view=skills` 已正常返回 0 个原子 Skill；这与其只使用一个 Brain、没有子 Skill 的配置一致。`view=brains` 可打开 `book-case01-morning-routine` 完整正文，内容标识为 `d61cb71306a4`，编辑区和保存按钮禁用，符合封存只读边界。未再出现 `passive_roots` 或对象 Skill 列表刷新失败。

案例1原 Run 已在浏览器点击播放，以 4× 速度从 Step 1 自动推进至 Step 32。末帧为 09/09 08:33，住宅背景、陈明远人物和继续研究的事件均可见，时间轴显示 `Step 32 / 32`，按钮变为“重新播放”。与修复前相比，原 Run 身份保持 `4f5713c4-7b76-4afe-8f3a-b4bd36ee2a28`，没有重新运行。

两个原问题均完成浏览器验收。交通原 Run 在重启后保持 `3cb1414a-be8e-4d7b-bace-38f4e3b8d540`，进度从重启前 45/60 继续到 46/60，仍显示运行中；没有创建新 Attempt 或重新启动该 Runtime 进程。

工程细节和全部测试命令见 [工程证据](replay-skill-loading-engineering-2026-09-13.md)。现场 16 个 Run 的已提交帧与世界素材均可只读解析；静止 Run 的全部文件，以及运行中 Run 的不可变内嵌实验，读取前后 SHA256 一致。两个源 `.gaexp` 整体哈希也保持一致。该工程读取检查不冒充逐个浏览器播放验收。
