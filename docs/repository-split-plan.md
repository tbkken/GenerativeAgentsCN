# 仓库拆分计划

本文件记录把书稿、案例素材和运行证据从代码仓库中迁出的步骤。目标不是删除证据，而是让代码仓库只保留可运行、可测试、可发布的契约内容。

## 当前状态（2026-10-22）

代码仓库跟踪体积已从 **503.8 MB / 4562 文件** 降到 **488.1 MB / 4315 文件**。

第一步已完成并提交（`88fb6b0`）：

- `.gitignore`：保留 `data/studio/packages`、`generative-agents.db`、`snapshot.json` 的跟踪，新增本地状态忽略规则。
- 已用 `git rm --cached` 停止跟踪 247 个本地状态文件，工作区文件未删除：
  - `data/studio/logs/`
  - `data/studio/skill-author-cache/`
  - `data/studio/assets/preview-cache/`
  - `data/studio/run-recycle-bin/`
  - `data/studio/studio-operations/`
  - `data/studio/model-credentials.json`
  - `data/studio/master.key`
  - `data/studio/studio-presentation.json`
  - `data/studio/studio-model-settings.json`
- 证据目录仍保持跟踪：`data/studio/packages/`（1148 文件）、`generative-agents.db`、`snapshot.json`。

## 远程仓库（已完成）

| 仓库 | 可见性 | 内容 | 本地分支 |
| --- | --- | --- | --- |
| https://github.com/tbkken/GenerativeAgentsCN-Book | 私有 | 书稿、案例素材、图片、验收记录 | `split/docs` |
| https://github.com/tbkken/GenerativeAgentsCN-Evidence | 私有 | `data/studio` 运行证据快照 | `split/studio-evidence-snapshot` |

两个远程 `main` 已与对应本地分支一致。证据仓库使用单提交快照，历史中不含 `master.key` 和 `model-credentials.json`。

核对命令：

```bash
git status --short
git ls-files data/studio/packages data/studio/generative-agents.db data/studio/snapshot.json | wc -l
git check-ignore -v data/studio/logs data/studio/master.key data/studio/run-recycle-bin
```

## 目标结构

| 仓库/位置 | 内容 | 预计体积 |
| --- | --- | ---: |
| `GenerativeAgentsCN` | `src/`、`tests/`、`tools/`、`.github/`、根配置、两份协议文档 | 约 15 MB |
| `GenerativeAgentsCN-Book` | `docs/book`、`docs/images`、`docs/仿真实验`、`docs/verification` | 约 227 MB |
| `GenerativeAgentsCN-Evidence` | `data/studio` 快照（Run、DB、snapshot） | 约 248 MB |
| 本机 `GA_VAR_DIR` | 运行时 DB、Run、缓存、日志、凭据 | 约 83 MB |

大 ZIP、DB 和 `traces/model-calls-*.jsonl` 也可以改放 GitHub Release 或对象存储，仓库只保留清单与 README。

## 拆分步骤

### 1. 提交第一步

先单独提交 `.gitignore` 与 247 个停止跟踪的文件，避免和当前 social-replay 的未提交改动混在一起。

### 2. 拆分分支（已执行）

已创建以下本地分支：

| 分支 | 内容 | 用途 |
| --- | --- | --- |
| `split/docs` | `docs/` 全部内容，位于仓库根目录 | 已推送到 docs 仓库 |
| `split/studio-evidence` | `data/studio/` 内容，保留历史 | 仅本地归档，**不要直接推送** |
| `split/studio-evidence-snapshot` | 与上一分支相同的树，无历史 | 已推送到 evidence 仓库 |

`split/studio-evidence` 的历史包含 `8e574d1` 中加入的 `master.key` 和 `model-credentials.json`。因此远程只使用无历史的 `split/studio-evidence-snapshot`。

`split/docs` 会同时带上 `docs/skill-authoring.md` 和 `docs/unified-resource-protocol.md`。新仓库中可删除这两份契约文档；主仓库继续保留。

### 3. 远程推送（已执行）

- `split/docs` → `GenerativeAgentsCN-Book` 的 `main`
- `split/studio-evidence-snapshot` → `GenerativeAgentsCN-Evidence` 的 `main`

推送时使用本机代理 `127.0.0.1:5780`：

```bash
git -c http.proxy=http://127.0.0.1:5780 -c https.proxy=http://127.0.0.1:5780 \
  push docs-origin split/docs:main
git -c http.proxy=http://127.0.0.1:5780 -c https.proxy=http://127.0.0.1:5780 \
  push evidence-origin split/studio-evidence-snapshot:main
```

### 4. 更新引用

新仓库推送成功后再更新主仓库链接：

- `README.md:108` → `docs/book/sample/README.md`
- `AGENTS.md:167` → `docs/book/sample/README.md`
- `CLAUDE.md:11` → 书稿案例入口
- `CLAUDE.md:162` → `data/studio` 的保留说明
- `docs/book/sample/` 内部的相对链接随新仓库调整

代码与测试不引用这些目录，功能上无需改动。

### 5. 可选：清理主仓库历史

只有确认新仓库可独立访问后，才执行历史清理：

```bash
pip install git-filter-repo
git filter-repo \
  --path docs/book \
  --path 'docs/仿真实验' \
  --path docs/images \
  --path docs/verification \
  --path data/studio \
  --invert-paths
git push --force-with-lease origin --all
git push --force-with-lease origin --tags
```

这一步会改写公共历史，必须与协作者协调。不要在未推送新仓库前执行。

## 风险与前置检查

- **凭据**：`data/studio/snapshot.json` 明确记录 `master_key_and_credentials_included: true`，同目录的 `master.key` 可解密 DB 中的模型凭据。停止跟踪只影响当前树，`8e574d1` 仍保留在本地和 origin 历史中。若仓库已共享或会公开，必须先轮换模型密钥，再决定是否用 `git filter-repo` 清理远程历史。
- **`var/` 是用户数据**：不要删除。建议复制到仓库外，设置 `GA_VAR_DIR`，验证后再处理旧目录。
- **当前分支不干净**：`codex/retire-legacy-runtime` 上有大量未提交的 social-replay 改动。拆分提交必须与它们分开。
- **不要用子模块**：文档和证据仓库独立发布即可，子模块会增加克隆和权限复杂度。
