# 消融与默认策略选择

本文件说明 B2 / B3 / B4 的对比口径、判定门槛与**当前状态**。所有数字必须来自真实运行：
现在没有真实凭据与人工确认样本，因此这里只给出可复现步骤，并如实标注「优化未验证」。

## 对照表

| 配置 | 上下文策略 | 复核 | 强模型路由 | 相对谁发生变化 |
| --- | --- | --- | --- | --- |
| `b0.json` | —（规则基线，无 LLM） | — | — | 低成本参照 |
| `b1.json` | context-1（完整 Gap） | 0 | 关闭 | 无场景状态 |
| `b2.json` | context-1 | 0 | 关闭 | 基准（= 当前默认行为） |
| `b3.json` | **context-2**（长 Gap 保守筛选） | 0 | 关闭 | 只改上下文 |
| `b4.json` | **context-2** | **3 条/窗口** | 关闭 | 在 B3 上加有限复核 |

强模型路由需要任务范围里的 `strong_profile_id`；评测命令目前不会注入第二套模型配置，
因此**路由收益尚未有评测入口**（只有后端集成测试覆盖了「上限 + 困难窗口 + 审计」）。
这一点如实保留为未做，后续要么扩展 `EvalConfig`（会改变配置指纹，需要重新生成报告），
要么在应用内用显式 policy 覆盖做小样本试用。

## 复现命令

```powershell
# 0) 离线数据校验（不加载模型）
python -m ndr.evaluation validate --manifest evaluation/manifests/dev.json

# 1) 压缩到底删了什么（离线证据账；可与任何 policies 组合复现）
python -m ndr.evaluation loss --manifest evaluation/manifests/dev.json `
  --context-policy context-2 --output evaluation/reports/dev-context-loss.json

# 2) 真实对比（需要凭据 + 预算）：同一清单、同一 --profile-id，只换 --config
python -m ndr.evaluation run --manifest evaluation/manifests/dev.json `
  --config evaluation/configs/b2.json --profile-id <id> --allow-live `
  --output evaluation/reports/dev-b2-live.json
python -m ndr.evaluation run --manifest evaluation/manifests/dev.json `
  --config evaluation/configs/b3.json --profile-id <id> --allow-live `
  --output evaluation/reports/dev-b3-live.json
python -m ndr.evaluation run --manifest evaluation/manifests/dev.json `
  --config evaluation/configs/b4.json --profile-id <id> --allow-live `
  --output evaluation/reports/dev-b4-live.json
```

## 判定门槛

1. **删对**：`loss` 证据账里 `must_keep_violations` 必须为 0——金标准声明必须保留的证据不能被压缩丢掉。
   非 0 时不谈收益，先修筛选规则。
2. **不降质量**：B3 的已接受准确率与覆盖率相对 B2 不得低于噪声以外可解释的范围；
   B4 相对 B3 只有在准确率或覆盖率有提升时才值得多花的钱。
3. **算总账**：费用必须含**预处理与复核/重试**成本——报告 `usage_total` 是每次尝试之和，
   复核会额外产生 `inference_runs`；只看最后一次调用会低估 B4。
4. **样本要够**：可确定样本 < `--min-sample`（默认 30）时 `targets_met=null`，不得宣布达标。
5. **有收益才改默认**：把默认从 `context-1` 切到 `context-2` 需要以上证据；否则保留保守策略并标注未验证。

## 当前状态（2026-09-28）

- `evaluation/reports/dev-context-loss.json`：`gaps_scanned=2`、`compressed_gaps=0`、`dropped_spans=0`
  ——仓库自带的原创最小样例 Gap 都短于 80 码点，**压缩没有触发**，因此它只证明工具可用，不能作为压缩效果证据。
- `evaluation/reports/dev-b3-notrun.json` / `dev-b4-notrun.json`：`NOT_RUN`（缺少 `--allow-live`、凭据与预算）。
- 结论：**B3/B4 的真实准确率—覆盖率—总费用对比没有数据**；默认策略保持 `context-1`
  （`gap_compression=False`、`recheck_max_targets=0`、`strong_model_share=0.0`），实现账本里压缩策略的
  Optimization 一栏写 `BLOCKED`。