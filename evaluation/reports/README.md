# 评测报告与复现说明

本目录里的报告由 `python -m ndr.evaluation run` 生成，**全部内容可复现**：报告里记录了清单、
配置与配置指纹、各组件版本（引擎/提示词/扫描器/上下文策略/金标准 schema）与生成时间。

| 报告 | 状态 | 说明 |
| --- | --- | --- |
| `dev-b0-offline.json` | `OFFLINE_BASELINE` | B0 规则基线（无 LLM）在原创最小样例上的**真实指标**；`quality_evidence=false` |
| `dev-b1-notrun.json` | `NOT_RUN` | B1 需要真实模型（`--allow-live --profile-id`），当前没有凭据 |
| `dev-b2-notrun.json` | `NOT_RUN` | 同上（B2 = 当前引擎默认行为） |
| `dev-b3-notrun.json` | `NOT_RUN` | B3（`context_policy=context-2` 长 Gap 保守筛选），同样需要真实凭据与预算 |
| `dev-b4-notrun.json` | `NOT_RUN` | B4（B3 + 每窗口最多 3 条局部复核） |
| `dev-context-loss.json` | 证据账（离线） | 压缩**实际丢了哪些原文**、是否删到金标准 `must_keep`；不是效果数字 |

复现命令：

```powershell
# 1) 只校验数据（不加载模型、不联网）
uv run --project backend python -m ndr.evaluation validate --manifest evaluation/manifests/dev.json

# 2) 离线规则基线（真实指标，但不是效果数字）
uv run --project backend python -m ndr.evaluation run `
  --manifest evaluation/manifests/dev.json --config evaluation/configs/b0.json `
  --output evaluation/reports/dev-b0-offline.json

# 3) 离线证据账：压缩丢了哪些原文、是否删到金标准 must_keep（不加载模型）
python -m ndr.evaluation loss --manifest evaluation/manifests/dev.json `
  --context-policy context-2 --output evaluation/reports/dev-context-loss.json

# 4) 真实模型对比（需要真实凭据与预算）
uv run --project backend python -m ndr.evaluation run `
  --manifest evaluation/manifests/dev.json --config evaluation/configs/b2.json `
  --profile-id <model-profile-id> --allow-live `
  --output evaluation/reports/dev-b2-live.json
```

读数要点：

- `overall.grouping.accepted_accuracy`：**已接受准确率**（只在匹配到、已接受、且金标准可确定的对白上算；
  拒答不计入分子分母）。
- `overall.coverage.coverage`：**覆盖率**（分母是全部金标准对白，拒答会拉低它）。
- `overall.grouping.pairwise_f1`：同人 pairwise F1（标签置换不变）。
- `overall.scenes.wrong_split` / `wrong_join`：错误切断 / 错误连接。
- `overall.grouping.extra_groups` / `missing_groups`：新人物误建 / 漏建。
- `degenerate`：全拒答、全合并等退化行为会被显式标记，避免用单一指标“看起来达标”。
- `targets_met`：样本不足或没有已接受样本时为 `null`（**不宣布达标**）；
  首轮目标是已接受准确率 ≥97%、覆盖率 ≥70%，必须用真实独立作品验证。

当前状态：**没有真实凭据与真实作品**，因此 `quality_evidence` 全部为 false，
真实 B1/B2/B3/B4 对比与 97%/70% 的结论都还没有数据。

`dev-b0-offline.json` / `dev-b1-notrun.json` / `dev-b2-notrun.json` 是早期生成的历史报告，**保持原样**：
它们的 `versions` 里没有 新增的 `context_policy_versions` 字段；用现在的工具重跑会多出该字段，
指标与结论不变（要保留历史结论，所以不覆盖）。

补充：`dev-context-loss.json` 在仓库自带的原创最小样例上是
`gaps_scanned=2`、`compressed_gaps=0`（样例 Gap 都短于 80 码点，压缩没有触发），
所以它只证明证据账可用，**不能**当作压缩效果证据；默认策略仍保持 `context-1`。