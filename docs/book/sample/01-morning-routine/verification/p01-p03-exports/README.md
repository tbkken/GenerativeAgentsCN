# P01–P03 浏览器回归导出

2026-09-09，主 Agent 经浏览器创建短回归实验，在 Step 2 安全暂停并导出，随后恢复同一 Run、完成 Step 6 后再次导出。此处四个产物及四份来源记录均从该 Run 原样复制，复制后重新核对 SHA-256。

- 实验：`56ff8cc0-ecae-4691-bf7d-ab4910f98b79`
- Run：`0cd75b46-f3e2-4068-8c40-59cfdee5e753`
- Attempt 1：`2fadf608-f95c-4616-948e-70fb8432121b`，提交 Step 1–2。
- Attempt 2：`1df1a004-f006-440d-9ede-d0a028b86742`，提交 Step 3–6。

这些 ZIP 是普通结果审计包，不是正式 `.garun` 或 `.gaexp`，本次验证不承诺异机恢复。它们属于 P01–P03 的短回归证据，不替换案例 1 原有 32 步参考结果。

## 文件与固定来源

| 文件 | 字节数 | 来源 | SHA-256 |
| --- | ---: | --- | --- |
| [result-bundle-step-000002-3e67e286.zip](result-bundle-step-000002-3e67e286.zip) | 5684858 | Step 2/6 · PAUSED · partial=true | `36f0e6739fd5dfbef86cd3a859659b2021ddb82df58044b6cebeb63eecf13a1b` |
| [quality-report-step-000002-149a510a2f9d4385-8b762e1d.json](quality-report-step-000002-149a510a2f9d4385-8b762e1d.json) | 997 | Step 2/6 · PAUSED · partial=true | `149a510a2f9d4385bee15697f65c07a87c02fcbb8b1357cd293c85bb03189236` |
| [result-bundle-step-000006-26e66786.zip](result-bundle-step-000006-26e66786.zip) | 5753459 | Step 6/6 · COMPLETED · partial=false | `6b17db5ab4e163c78ebe717abbe3c553fae7f86fc2b52251067818f66044a438` |
| [quality-report-step-000006-518da6a3b72fb4fe-aae4bdcf.json](quality-report-step-000006-518da6a3b72fb4fe-aae4bdcf.json) | 2377 | Step 6/6 · COMPLETED · partial=false | `518da6a3b72fb4fe6591d03b01d85daa7c8475f3dd64c085e0cb9103393cc4d9` |

- Step 2 ZIP 在续跑前记录为 5,684,858 字节；完成 Step 6 后大小、完整哈希及来源记录均保持不变。Step 2 独立质量报告与该原 ZIP 内的报告逐字节一致。
- Step 6 ZIP 含连续 6 帧，两个 Attempt 分别为 2 帧、4 帧；包内身份与上述 Run、实验一致。
- 两个 ZIP 均通过 CRC 检查，各自独立质量报告与 ZIP 内报告逐字节一致；没有包含 Attempt 下未提交的 `storage/runtime-storage` 工作副本。
- 最终质量报告保留 3 项观察：Step 1 首次记忆检索为空；Step 4、5 的 `world-navigate` 请求 `[23,7]` 超出视野被拒绝。后两项属于仍待决策的 P06，不属于本轮修复范围。

## 来源记录与校验

来源记录复制在 [provenance/](provenance/)，记录中包含文件名、完整内容哈希、大小、取材 Step/状态和生成时间；不使用当前 Run 状态回填历史来源。四个产物和四份来源记录的完整哈希见 [SHA256SUMS.txt](SHA256SUMS.txt)。

- `result-bundle-step-000002-3e67e286.zip`：生成于 `2026-09-09T06:51:31.499009Z`；[来源记录](provenance/7c23198246a917a2bee872fa841f280ff81a59988ee373f9aa73acc8d6518abd.json)。
- `quality-report-step-000002-149a510a2f9d4385-8b762e1d.json`：生成于 `2026-09-09T06:51:29.056387Z`；[来源记录](provenance/f523774e3cd7828d87ac21ce2db58f90618a59de2a813184b77f638eed5d8da4.json)。
- `result-bundle-step-000006-26e66786.zip`：生成于 `2026-09-09T06:58:04.264564Z`；[来源记录](provenance/5c0f13261bed86e87681a043242ea6ce1ee8f06a9c242a177587ea83b380d6e1.json)。
- `quality-report-step-000006-518da6a3b72fb4fe-aae4bdcf.json`：生成于 `2026-09-09T06:58:03.131846Z`；[来源记录](provenance/591aa4591bcc0a86c321734142e9feecfd54b8f85f49b486dacf614b9714dd30.json)。
