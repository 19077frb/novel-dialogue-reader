# 开发交接

更新时间：2026-09-28
当前任务：T17 上下文压缩、局部复核与成本路由（implementation / offline_verification 完成；live = BLOCKED，quality = BLOCKED，**优化未验证**）
最近完成任务：T17（此前 T00～T16：骨架→迁移→TXT→EPUB→阅读器→候选与金标准→配置页→适配器→上下文预算→场景引擎→任务缓存用量→标注投影与预览→人工更正与撤销→确认队列与抽屉→暂停预算与故障恢复→初读身份与码点定位→导出后端与标准校验→导出界面与下载闭环→真实样本评测工具）
下一任务与理由：T18 完整联调、体验与发布检查。前置 T17 已完成（优化可以保持关闭）：
要做 TXT/EPUB 导入 → EPUB/HTML 导出全流程、可访问性、360/1280px 布局、密钥与资源边界、任务恢复、
OpenAPI 无意外差异，产出 `docs/verification-report.md` + 离线/E2E 报告索引 + 真实联调记录
（真实提供方试用缺数据时明确 BLOCKED）。随后 T19 启动交付与最终交接。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`。
- 评测命令：`python -m ndr.evaluation validate|run|loss …`（清单 `evaluation/manifests/`、
  配置 `evaluation/configs/`、报告 `evaluation/reports/`、消融说明 `evaluation/ablations.md`）。
- 数据目录与迁移：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0005`（= head）。
- 测试/E2E：`NDR_CREDENTIAL_BACKEND=session`；E2E 另开 `NDR_ALLOW_FAKE_PROVIDER=1` 与
  `NDR_FAKE_PROVIDER_LABELS=deterministic`。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`cd frontend; $env:NDR_E2E_BACKEND_CMD='..\backend\.venv\Scripts\python.exe -m ndr'; npx playwright test`。
- T17 开关（都通过**既有**字段传入，没有新端点）：任务 `range.context_policy`（`context-1` 默认 / `context-2`）、
  `range.strong_profile_id`；显式策略覆盖用 `run_job(..., policy=BudgetPolicy(...))`（测试/消融用）。

## 本次改动

- `context/budget.py`：`BudgetPolicy` 增加 T17 字段（`gap_compression`、`gap_compression_threshold_cp`、
  `gap_compression_margin_sentences`、`gap_compression_max_ratio`、`recheck_max_targets`、`strong_model_share`），
  全部进入 `as_key()`；新增 `COMPRESSED_POLICY`、`CONTEXT_POLICY_CONSERVATIVE/COMPRESSED`、
  `policy_version_for()`、`POLICY_BY_VERSION`、`policy_for_version()`（未知/缺省一律回落保守）。
- `context/source_selection.py`：`split_sentences()`、`looks_like_evidence()`、`compress_gap_spans()`；
  长 Gap 只留线索句 + 前后各一句，省不下来就整体保留并给 `gap_compression_skipped` warning；
  丢掉的行文写入 `omitted`（`reason=gap_compression`，带码点与 token）。
- `context/recheck.py`（新）：`plan_recheck()`（只取「未解决 ∩ 本窗口」并按上限截断、标记 `restore_evidence`）
  与 `route_window()`/`strong_cap()`/`is_hard_window()`（share 上限、困难窗口、不可用就退回基础模型）。
- `context/window_builder.py`：`policy_version_for(policy)` 进入窗口 ID、依赖哈希、计划哈希与 `WindowPlan.policy_version`，
  所以 `context-2` 不会复用 `context-1` 的缓存。
- `jobs/scheduler.py`：按任务范围选择策略；复核对未解决目标重发一次（保守策略重建 = 证据补回，
  单独记 `inference_runs`，超时按 F15 进 `NEEDS_RECONCILIATION`）；按 `strong_model_share` 上限路由困难窗口；
  `progress_json` 暴露 `strong_windows` / `recheck_windows` / `recheck_targets` / `recheck_calls`。
- `jobs/service.py`：`usage_summary` 的 `by_model` 改为按**每次尝试自己的快照**归属（强模型用量不再算到基础模型头上）。
- `evaluation/`：`loss.py` + `python -m ndr.evaluation loss`（离线证据账：压缩丢了哪些原文、是否删到金标准
  `must_keep`）；`live.py`/`runner.py` 支持 `context_policy` 与 `budget.max_rechecks`；新增 `configs/b3.json`、`b4.json`；
  新增 `ablations.md`；重新生成 `reports/dev-b0-offline.json` 与 `dev-b1..b4-notrun.json`、
  `reports/dev-context-loss.json`。
- 文档：决策 0021；CONTRACTS 第 17 节更新 + 新增第 29 节；本文件与 `IMPLEMENTATION_STATUS.md`。

## 验证证据

- 后端：`pytest backend/tests` → **370 passed**（T16 时 346；新增 10 + 10 + 3 + 2 项，并更新 `test_budget.py`）；
  `ruff` 全绿；`scripts/verify.ps1` 退出码 0（含 OpenAPI 与 `docs/openapi.json` 一致、
  前端 typecheck / 70 项单测 / build 通过）。
- 压缩回归（单测）：短 Gap 不压缩；长 Gap 丢纯叙述但保留「少女低声说」并补回线索句前后各一句；
  全线索 Gap → 不压缩 + warning；丢弃片段有 `OmittedRecord`；「保留 + 丢弃」逐段首尾相接且拼回原文；
  `context-1`/`context-2` 的 `window_id`、`dependency_hash`、缓存键都不同。
- 调度器回归（集成，FakeProvider，离线）：`range.context_policy=context-2` → 首次派发压缩
  （纯叙述不在上下文里）→ 全 UNKNOWN → 复核 1 次且**复核请求里出现了被压缩掉的文句**（证据补回）；
  两次尝试各记一条 `SUCCEEDED` 的 `inference_runs`；`strong_model_share=1.0` + `strong_profile_id` 时困难窗口
  走强模型（尝试快照与 `by_model` 都是 `strong-model`），`share=0.4`（1 窗口 → 上限 0）时不路由。
- 证据账（真实产出，可复现）：`evaluation/reports/dev-context-loss.json` →
  `gaps_scanned=2`、`compressed_gaps=0`、`dropped_spans=0`、`must_keep_violations=0`
  （仓库样例 Gap 都短于 80 码点，压缩未触发——如实记录，**不是**压缩效果证据）；
  合成样例（`must_keep` 与丢弃区间重叠）能计出 `must_keep_violations ≥ 1`。
- 门槛逐条核对：**上下文删减/补回回归**✓；**默认策略保持保守**✓（`context-1` + 三开关关闭，可一键回滚）；
  **不夸大**✓（没有 B3/B4 真实对比，优化写 BLOCKED/未验证）。
- **未验证（BLOCKED）**：真实准确率—覆盖率—总费用对比（含复核与重试成本）、B3/B4 消融结论、
  真实提供方上的复核/路由收益——没有凭据、预算与人工确认样本。
- E2E（真实前后端 + 真实浏览器 + 离线假提供方）：`npx playwright test` → **28 passed**
  （与 T15B 时同为 28 项，本次未新增用例，用于确认 T17 的调度改动没有回归用户流程）；
  T18 会补可访问性、360/1280px 布局与全流程联调检查。

## 未完成与已知问题

1. **T17 优化未验证**：压缩是否降质量、复核是否值钱、路由是否划算，都需要真实数据；
   在拿到 B3/B4 结论前**不要**把默认切成 `context-2`。
2. **强模型路由没有评测入口**：`strong_profile_id` 只能由任务范围传入（无 UI），
   评测命令也不会注入第二套模型配置；要测路由收益需要扩展 `EvalConfig`（会改配置指纹，需重新生成报告）。
3. **压缩阈值是经验值**：80 码点 / 1 句 / 0.6 比例来自启发式，没有真实数据校准。
4. **真实效果数字为零**：`evaluation/reports/` 只有离线基线与证据账；B1～B4 都是 `NOT_RUN`。
5. **EPUBCheck 与独立 EPUB 阅读器试读仍未做**（T15B 遗留：无 jar、本机无阅读器且不能联网安装）。
6. **`live.py` 的真实提供方路径仍未在真实环境跑过**；T18 计划做一次受预算约束的真实试用（缺数据则 BLOCKED）。
7. 其余既有事项：`uv run` 在受限沙箱失败（回退 venv）；`npm --prefix frontend install/ci` 需在包目录内执行；
   `alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`；`apply_patch` 失效时用 `.tools/newfile.ps1` / `.tools/append.ps1`；
   **编辑多行文本前先统一换行符**；E2E 数据目录共享（新夹具内容必须与既有夹具不同，否则被 sha256 去重）；
   测试文件名不要在不同目录重名（pytest 会撞模块名，本次已把集成测试改名 `test_recheck_routing_jobs.py`）。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**`、`frontend/e2e/fixtures/**` 的原始字节；已发布迁移 `0001`～`0005`；
  **`user_locked` 标注与用户密钥**；审计类表只追加；**已有评测报告不要改写**（要保留历史结论）。
- 当前版本号（进入缓存键/依赖哈希）：API 契约 `1`；数据库 `0005`；输出契约 `1.0`；
  提示词 `labeling-1`/`connection-1`；上下文 `context-1`（默认）/`context-2`（压缩）；复核策略 `recheck-1`；
  路由策略 `routing-1`；场景状态 `scene-state-1`；接受策略 `acceptance-1`；
  引擎 `attribution-engine-1`；调度器 `scheduler-1`；缓存 `cache-1`；扫描器 `quote-scan-1`；
  更正服务 `correction-1`、恢复服务 `recovery-1`、导出快照 `export-snapshot-1`、导出器 `exporter-1`、
  评测 `evaluation-1`、B0 基线 `b0-rule-1`、证据账 `context-loss-1`。
- 提交习惯：每完成一部分功能即用 git 提交。