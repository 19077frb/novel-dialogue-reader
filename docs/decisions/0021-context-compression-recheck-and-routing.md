# 0021 上下文压缩、局部复核与成本路由：默认保守、证据先行

日期：2026-09-28 · 状态：已采纳（T17）

## 问题

T17 要减少上下文（长 Gap 保守筛选）、复核未解决目标、并按难度路由模型。但当前**没有真实模型凭据与预算，
也没有人工确认的真实作品样本**：无法证明这些优化带来收益。若把压缩/复核/强模型路由直接设为默认，
就是拿「看起来更省」替代真实证据；若完全不做，则拿到数据时还得重新设计一遍。

## 选择

1. **版本化上下文策略**：`context-1`（保守，默认）/ `context-2`（长 Gap 保守筛选）；
   版本进入窗口 ID、依赖哈希与缓存键，可用任务范围里的 `context_policy` 一键回滚。
2. **压缩规则保守且可审计**：只丢长 Gap 里的叙述，保留归属线索句（引号或 `说/问/答/…`）及前后各一句；
   短 Gap 不动；省不下来（保留比例 > `max_ratio=0.6`）就整体保留并写 warning；
   丢掉的行文逐段留痕（`omitted.reason=gap_compression`，含码点与 token），单测保证「保留 + 丢弃 = 原文」。
3. **复核有限且带证据补回**：只复核该窗口仍未解决（`UNKNOWN` 且未被用户锁定）的目标，
   每窗口最多 `recheck_max_targets`（默认 0）；复核窗口用**保守策略重建**（`restore_evidence`，
   把压缩丢掉的句子补回）；额外尝试单独记 `inference_runs` 并计入用量，不是「只统计最后一次调用」。
4. **成本路由默认关闭且受上限约束**：`strong_model_share` 默认 0.0，上限 `floor(share × 计划窗口数)`，
   只升级困难窗口（丢过证据 / 超长目标 / 目标 ≥4 / 需复核），且必须由任务范围显式给出 `strong_profile_id`；
   强模型不可用时**如实退回**基础模型并记录原因。
5. **审计口径**：每次尝试记录实际使用的模型快照（`inference_runs.profile_snapshot_json`），
   `GET /api/books/{id}/usage` 的 `by_model` 按**每次尝试自己的快照**归属；
   任务 `progress_json` 暴露 `strong_windows` / `recheck_windows` / `recheck_targets` / `recheck_calls`。
6. **证据先行**：新增 B3/B4 评测配置、离线证据账（`python -m ndr.evaluation loss`）与消融说明；
   没有真实对比数据就不改默认策略。
7. **不扩大范围补偿**：T17 不加新 UI 与新端点；`context_policy` / `strong_profile_id` 走既有 `range` 字段。

## 被放弃的方案

- **直接把 `gap_compression=True` 设为默认**：没有证据支持「压缩不丢识别质量」，违反门槛。
- **无条件复核所有目标/所有窗口**：费用几乎翻倍且没有数据表明收益；改为只复核未解决目标 + 硬上限。
- **用启发式难度自动切换强模型而不设上限**：可能把用户费用放大到预期之外。
- **用 FakeProvider 的对比充当优化收益**：质量证据必须来自真实提供方，假提供方只能验证链路。
- **把证据账当成收益结论**：`loss` 只说明删了什么、是否删到金标准 `must_keep`，不说明值不值得。

## 验证

- 后端 `pytest backend/tests` → **370 passed**（T16 时 346；新增 `test_context_compression.py` 10 项、
  `test_recheck_routing.py` 10 项、`test_recheck_routing_jobs.py` 3 项、`test_evaluation_loss.py` 2 项，
  并更新 `test_budget.py` 的策略字段断言）；`ruff` 全绿；`scripts/verify.ps1` 退出码 0
  （含 OpenAPI 与 `docs/openapi.json` 一致、前端 typecheck / 70 项单测 / build）。
- **压缩回归**：短 Gap 不压缩；长 Gap 丢纯叙述但保留「少女低声说」这类线索句与前后各一句；
  全线索 Gap 省不下来 → 不压缩 + `gap_compression_skipped` warning；丢弃片段有 `OmittedRecord`；
  「保留 + 丢弃」逐段首尾相接且拼回原文；`context-1` 与 `context-2` 的 `window_id`、
  `dependency_hash`、缓存键**都不同**。
- **调度器回归（集成，离线）**：`range.context_policy=context-2` 时首次派发确实压缩
  （纯叙述不在上下文里）→ 全 UNKNOWN → 复核一次，且**复核上下文把丢掉的文句补回**（同一句出现在复核请求里）；
  两次尝试各记一条 `SUCCEEDED` 的 `inference_runs`；`strong_model_share=1.0` + `strong_profile_id` 时
  困难窗口走强模型（尝试快照与 `by_model` 都是 `strong-model`），`share=0.4`（1 个窗口 → 上限 0）时不路由。
- **证据账**：`loss` 子命令在 dev 清单上真实产出 `evaluation/reports/dev-context-loss.json`
  （`gaps_scanned=2`、`compressed_gaps=0`——样例 Gap 太短，压缩没有触发，如实记录）；
  合成样例（`must_keep` 与丢弃区间重叠）能计出 `must_keep_violations ≥ 1`。
- **未验证（BLOCKED）**：真实准确率—覆盖率—总费用对比（含复核成本）、B3/B4 消融结论、
  真实提供方上的复核收益与路由收益。因此默认仍是 `context-1`，三个开关全部关闭。

## 后果

- 现在可以随时用 `range.context_policy` / `range.strong_profile_id` + 显式 policy 覆盖跑 B3/B4，
  而不改变默认行为；任何一次运行都能从 `inference_runs` 与 `progress_json` 还原用过哪个模型、复核了几次。
- 拿到真实数据后的动作：跑 B2/B3/B4 → 对照准确率、覆盖率、总费用（含复核与重试）→
  **有收益才改默认**，并同步更新本决策、`evaluation/ablations.md` 与实现账本。