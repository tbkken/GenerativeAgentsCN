# 实验 Web 服务运行手册

适用环境：Windows、本机单 Web 进程、SQLite、多个实验子进程并发。当前产品不实现权限控制，因此只建议监听 `127.0.0.1`；若改为局域网地址，应先补反向代理和访问控制。

## 1. 安装与目录

在仓库根目录 `E:\GenerativeAgentsCN` 执行：

```powershell
python -m pip install -r .\generative_agents\requirements.txt
```

默认数据写入 `var`，主要结构为：

```text
var/
├─ generative-agents.db
├─ master.key
├─ assets/sha256/
├─ runs/<run-uuid>/
│  ├─ manifest.json
│  ├─ attempts/<attempt-uuid>/
│  ├─ frames/
│  ├─ checkpoints/
│  └─ artifacts/
└─ scheduler/
```

不要手工编辑 Run 目录、manifest、Frame 或 checkpoint。实验名、Agent 名不参与物理路径；目录名均为系统生成 ID。

## 2. 首次初始化

Web 启动会执行 Alembic upgrade。生产切换前仍建议显式初始化目录快照：

```powershell
python -m generative_agents.cli.import_legacy bootstrap-catalog --dry-run
python -m generative_agents.cli.import_legacy bootstrap-catalog --apply
```

`--dry-run` 不写数据库；`--apply` 相同源指纹会跳过。新建“标准小镇”实验会从数据库中的最新 catalog snapshot 深复制，不在 Run 期间读取共享 `data` 目录。

## 3. 两个模型服务

### 对话模型

```powershell
Set-Location F:\qwen3.6-windows-server
.\start.bat --snapshot start_mtp4
```

期望 OpenAI-compatible 地址为 `http://127.0.0.1:5001/v1`。本项目已验证解析模型为 `qwen3.6-27b-autoround`。

停止时只使用该目录提供的官方脚本：

```powershell
Set-Location F:\qwen3.6-windows-server
.\snapshots\stop_vllm.bat
```

### Embedding 模型

```powershell
Set-Location F:\qwen3-embedding-cpu-server
.\test.bat
.\start.bat -Background
```

前台排障使用 `.\start.bat`；停止使用：

```powershell
Set-Location F:\qwen3-embedding-cpu-server
.\stop.bat
```

期望地址为 `http://127.0.0.1:5002/v1`，解析模型为 `qwen3-embedding-0.6b`。不要用进程名模糊杀进程，避免误停其他 Python 服务。

## 4. 启动 Web 服务

```powershell
Set-Location E:\GenerativeAgentsCN
python -m generative_agents.web.main `
  --database-url sqlite:///E:/GenerativeAgentsCN/var/generative-agents.db `
  --var-dir E:\GenerativeAgentsCN\var `
  --host 127.0.0.1 `
  --port 8000 `
  --max-concurrent-runs 2
```

浏览器打开 `http://127.0.0.1:8000/`。健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/runtime/capacity
```

生产安装包必须包含 `generative_agents/web/static` 和
`generative_agents/frontend/static/assets/village`。时间探索使用包内 Phaser、tilemap、tileset
和 sprite，不依赖公网 CDN；最小 wheel 安装后可用下列地址检查资源：

```text
/static/console/replay-player.js
/static/console/vendor/phaser.min.js
/generative_agents/frontend/static/assets/village/tilemap/tilemap.json
```

Run 排障入口位于“运行与制品”：Attempt/Artifact 日志可按 byte cursor 读取、SSE tail 和下载；
Checkpoint 可查看校验状态与受控内容；Replay 在线窗口单次最多 100 Step。不要直接读取或修改
`var/runs` 下的文件，路径/大小/SHA 任一不一致都会被完整性边界拒绝。

必须保持 Uvicorn `workers=1`。并发实验由子进程调度器提供，不通过增加 Web worker 实现；多个 Web worker 会争抢本机 supervisor ownership。

### 4.1 Native symlink 发布门禁

Run 日志、Artifact 与 Replay Frame 的存储隔离发布前必须在真实 file symlink 和 directory
symlink 上验证。普通本地全量测试允许在主机缺少创建权限时显示 `SKIPPED`；这不构成发布证据。

本机能力不足时，可显式运行开发检查：

```powershell
python .\tools\run_symlink_release_gate.py --allow-unavailable
```

它只会输出 `SYMLINK_RELEASE_GATE_SKIPPED` 并返回成功，不得用于发布。Windows 可在“开发者设置”
启用开发人员模式，或向执行账号授予 `SeCreateSymbolicLinkPrivilege`，然后运行严格门禁：

```powershell
python .\tools\run_symlink_release_gate.py
```

严格门禁会先真实创建并读取一个文件链接和一个目录链接，再运行 DEF-047/061/063 的 7 个原始
对抗用例；runner 会设置 `GA_REQUIRE_NATIVE_SYMLINK_TESTS=1`，收集数量不符、任一失败或任一
skip 都返回非零。GitHub Actions
`Native symlink release gate` 会在 `ubuntu-latest` 与 `windows-latest` 分别执行严格入口；Windows
job 会先启用临时 runner 的 Developer Mode。
发布分支保护必须把 `native-symlink (ubuntu-latest)` 与
`native-symlink (windows-latest)` 两个 matrix check 都设为 Required；不能用普通全量测试替代它们。

## 5. 并发与状态含义

- `--max-concurrent-runs N`：同机最多 N 个占槽 Run；其余持久排队，按 `queued_at + queue_id` FIFO 领取。
- 一个实验同时最多一个开放 Run；不同实验可以同时运行。
- `available_step`：页面可以读取的最后完整结果步骤。
- `recoverable_step`：具备完整 state/conversation/storage/RNG checkpoint 的最后恢复步骤，可能小于 available。
- `INTERRUPTED`：进程失联或主服务重启后对账得到的终态；若 recoverable=true，可继续恢复。
- `PAUSED`：不占运行 slot，可以继续或直接取消。

停止 Web 前可先等待运行完成或在页面取消。异常停止后重新启动，scheduler 会用 PID/create_time、心跳与 leader lock 对账，不凭 PID 数字单独认领进程。

## 6. 旧数据导入

```powershell
python -m generative_agents.cli.import_legacy runs --dry-run
python -m generative_agents.cli.import_legacy runs --apply
```

自定义旧结果根目录：

```powershell
python -m generative_agents.cli.import_legacy runs --apply `
  --source-root D:\legacy-results `
  --database-url sqlite:///E:/GenerativeAgentsCN/var/generative-agents.db `
  --var-dir E:\GenerativeAgentsCN\var
```

源根目录约定包含 `checkpoints/<name>` 和/或 `compressed/<name>`。导入器不移动、不删除源文件；每个目录单独事务，按绝对源路径 + 内容指纹幂等。旧实验显示“快照不完整”，缺少的 memory/model usage 等视图不会伪造。

## 7. 备份与恢复

备份前优先停止 Web，确认没有运行中的 Run，然后完整复制以下内容到同一备份版本：

- SQLite 主文件及可能存在的 `-wal`、`-shm`；
- `var/assets`；
- `var/runs`；
- `var/master.key`。

SQLite 在线备份可使用 Python/SQLite 官方 backup API，但不能只复制 `.db` 而遗漏 WAL。恢复到空目录，保持原相对结构和 `master.key`，再启动 Web 让 Alembic 前向升级。不要把新数据库与旧 Run 目录混搭；Run/Artifact 的内容哈希校验会拒绝不一致文件。

## 8. 常见排障

### 模型连接测试失败

1. 分别执行两个模型目录的官方测试/启动脚本。
2. 在实验“模型服务”页确认 base URL 包含 `/v1`，`auto` 测试后已显示明确 resolved model。
3. 查看对应 Run attempt 的 `logs` 与 `model-trace.jsonl`，不要把 API key 写进日志或 Revision。

### Run 长时间停在 STARTING

- 检查磁盘和杀毒软件是否阻塞 Python import；Worker 应在重型模型模块加载前建立心跳。
- 查看 `runtime/capacity` 和 attempt log。
- 不要手工修改 slot；重启 Web 触发 supervisor reconcile。

### 页面状态与历史事件不一致

Run 表和结果 API 是事实来源，SSE 只是刷新信号。刷新页面后客户端先读当前事实，再从最新 event cursor 继续；若仍异常，保存 request ID 并查看 Web 日志。

### Checkpoint 无法恢复

系统会按数据库 recoverable_step 精确选择，并验证目录 step、bundle step、Frame/hash、storage。LATEST 损坏时降序寻找最后一个校验通过的 bundle；更晚但超出数据库恢复边界的 bundle 会被隔离，不能手工改名冒充。

### 资源上传失败

首期受控资源类型为 PNG、JPEG、WebP 和合法 JSON，单文件默认不超过 50 MiB。声明 MIME 必须与内容嗅探一致；非法内容返回 `422 INVALID_ASSET`，不会保存半文件。

## 9. 验收命令

```powershell
python -m compileall -q generative_agents
node --check generative_agents\web\static\console-api.js
python -m pytest tests\legacy tests\architecture tests\foundation tests\runtime -q
git diff --check
```

当前已知非功能警告是 Starlette `TestClient` 对旧 httpx 适配层的弃用提示。部署基线仍应补跑 Python 3.12；本机 Python 3.13 通过不能替代目标版本 CI。
