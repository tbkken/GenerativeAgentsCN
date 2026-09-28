# 远程实验执行前保存失败

## 复现与影响

- 环境：`8.163.103.188:8000`，原部署 `release-20260923-web-compression`。
- 入口：溪谷大学实验 `2e48b9de-e398-4052-95da-c68b75e79bb8` 的“执行实验”。
- 用户操作对应服务日志出现两次实验 PUT 422，未到 seal / runs 请求，也未创建 Run；不是仿真中途失败。
- 浏览器复现时进入“正在准备执行”，随后显示请求失败 502。该路径会上传完整的大地图实验定义，网络等待放大了体验问题。

## 根因与修复

保存函数从隐藏的旧模型编辑器重建模型配置。旧聊天 provider 选项不支持已选模型的 `openai_compatible`；同时保存代码总会加入协议禁止的 `secret_ref`（即使为 null）。这还会丢失模型配置中未被旧表单覆盖的字段。

保存现在保留实验已经复制的完整模型配置，包括宿主机凭据环境变量绑定；模型变化仅由可见的模型中心复制操作决定。删除隐藏的旧模型编辑器和其凭据保存逻辑。执行准备和最终启动均等待已排队的草稿操作，仅在存在未保存修改时保存，避免重复上传未变更的完整实验。

协议的外部引用和密钥校验保持严格，没有增加导入兼容层或放宽 `secret_ref` 限制。

第一版重启后，原实验预检已显示 0 阻断、0 警告；但实际点击“保存配置”仍在大地图上传阶段返回 502，服务没有接收到已完成的 PUT。继续修复同一保存路径：前端通过 PATCH 仅提交 experiment/simulation/results/models/agents 五类表单内容；Studio 在同一包锁内读取现有完整定义、合并允许的整段内容，并复用原子的完整校验和保存。请求必须提供内容摘要，缺失或过期时拒绝；禁止通过该入口替换地图。完整 PUT 仍供需要全量替换的编辑操作使用。

增加 HTTP 集成与并发测试：确认地图、素材和技能文件保持原字节，过期请求返回 409，禁用字段、缺失摘要和已封存包拒绝保存，失败不影响原包。前端断言保存请求不含地图，地图再大也不会增加这个请求的体积。本地相关 Python 41 passed，Node 全量 104 passed。

## 回归证据

- 新增执行真实 `saveDraftUnlocked` 的 Node 回归：修复前复现模型被覆盖和 `secret_ref` 注入，修复后确认两个 provider 的完整配置与 `credential_env` 保留。
- 启动回归覆盖未修改草稿、待完成保存、修改后保存，以及预检/估算路径。
- 本地相关 Python：57 passed；前端全量：104 passed；依赖边界扫描通过。
- 新 wheel 相对上一部署只改变 `console-api.js`、`experiment-console.html` 和 wheel RECORD。
- wheel SHA256：`c956cfea8911660ea6e42631aa0aeb730e093097275dc84cc47b23789fd3f4fb`。

## 部署与浏览器验收

- 发布检查：Linux Python 全量 551 passed / 13 skipped；Node 全量 104 passed；原生文件/目录符号链接门禁 7 passed；仓库外 wheel 安装、CLI/Studio/暂停恢复/对象响应/Replay 验收通过；模型调用使用确定性本地 HTTP stub。
- 部署：`release-20260923-execution-save-fix`；备份 `/var/backups/generativeagents/pre-execution-save-fix-20260923`。
- Web 于 2026-09-23 01:32:29（Asia/Shanghai）重启，PID 40032，health=ok。数据库全部表的行数和内容指纹、模型凭据文件与重启前备份一致。
- 第一版的原路径预检通过，实际保存仍出现 502，故继续以下第二版修复。

### 最终部署及实际页面验收

- 最终版本：`release-20260923-settings-transfer-fix`；wheel SHA256 `bbfa4bbf0d6bf42a8b4cb34cf766697e7595c563699b1a499386d59b4e19769d`。
- 备份：`/var/backups/generativeagents/pre-settings-transfer-fix-20260923`；此前版本均保留。重启前没有 Run。
- Web 于 2026-09-23 01:44:25（Asia/Shanghai）重启，PID 41006，health=ok；验收时 NRestarts=0。
- 最终检查：Python 553 passed / 13 skipped，Node 104 passed，原生符号链接 7 passed，边界扫描和仓库外 wheel 安装验收均通过。
- 同一浏览器 Tab 返回原实验，点击“保存配置”，页面显示“保存成功 / 草稿已保存到当前实验，不影响其他实验。”；01:45:37 服务日志记录 PATCH 200。
- 保存后再次点击“执行实验”：01:45:54 validate 200，01:45:55 estimate 200；页面显示“0 个阻断项 · 0 个警告”和“检查通过，等待确认执行。”，确认按钮可用。
- 资源索引 `resources/index.json` 与本次重启前备份逐字节相同，模型、地图、智能体等资源内容未被本次保存重写。
- 保留原 4 Agent / 1000 步配置。页面估算 4000–12000 次模型调用、2.2–33.3 小时；没有点击最后的“确认执行”，未创建正式 Run，也不宣称真实模型运行完成。页面保留在执行确认位置。

## 发布检查期间的运维事件

01:27:08（Asia/Shanghai）内核 OOM 日志记录旧 Web PID 38487 被终止；systemd 于 01:27:13 自动恢复为 PID 39155。主机约 1.7GiB 内存、无 Swap，并行执行 Python、Node、wheel 安装验收和原生符号链接检查，加上正在处理实验请求的 Web，造成内存压力。此事件发生于本次发布检查期间，不能作为用户原始 422 的根因。

立即终止该批检查，随后使用独立 systemd 检查单元串行执行，设置 MemoryMax=450M、CPUQuota=50%（负载稳定后调至 100%，即一个 CPU）、Nice=15，Node 使用 test-concurrency=1。已向用户说明该检查方式导致的短暂中断。后续不再在此小内存生产主机并行运行发布检查。
