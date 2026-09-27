# 开发交接

更新时间：2026-09-28
当前任务：T10 持久化任务、缓存与用量（implementation / offline_verification 完成；live = BLOCKED）
最近完成任务：T10（此前 T00～T09：骨架→迁移→TXT→EPUB→阅读器→候选与金标准→配置页→适配器→上下文预算→场景引擎）
下一任务与理由：T11 真实效果预览与按章处理。T10 已提供任务/窗口/检查点/缓存/用量与暂停恢复，
T11 需要用同一套 annotations 投影做预览页与按章处理界面（范围选择、估算、试运行、任务面板、原文/标注对比、图例与 usage）。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`。
- 数据目录与迁移：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0004`（= head）。
  测试/E2E：`NDR_CREDENTIAL_BACKEND=session`；E2E 另开 `NDR_ALLOW_FAKE_PROVIDER=1`。
- 验证命令：`pwsh -File scripts/verify.ps1`。

## 本次改动

- `ndr/storage/cache.py`：语义缓存键（原文版本/目标 IDs/输入指纹/模型与参数/协议·提示·schema·策略版本/
  依赖哈希/阅读模式/horizon；**排除** job_id 与 preview·process 目的）与 `ResultCacheStore`（不覆盖既有结果）。
- `ndr/jobs/service.py`：幂等创建（同 key 同摘要复用、不同摘要 409）、本地估算（窗口数/目标数/token，
  标注为启发式）、profile 快照（无密钥）、`usage_summary` 与 `spent_tokens`（未知用量单独计数）。
- `ndr/jobs/scheduler.py`：`run_job`（窗口顺序执行、PREPARED→DISPATCHED→应用+结算、
  缓存命中不调用、预算预留与 `BUDGET_EXHAUSTED`、暂停在窗口之间生效、超时→`NEEDS_RECONCILIATION`、
  每窗口写检查点）、`reconcile_stale_runs`（重启后把超租约 DISPATCHED 标为未知结果）、
  `reconcile_job`（显式 retry / keep_unknown）。
- API：`POST /api/jobs`、`GET /api/jobs/{id}`（`JobDetailOut`，含窗口/用量/剩余）、
  `POST /api/jobs/{id}/pause|resume|run|reconcile`；`POST /api/books/{id}/estimates`、
  `GET /api/books/{id}/usage`（`api/estimates.py`）。
- 前端：类型跟随新契约（`JobOut` → `JobDetailOut`、新增 `JobRunOut`/`EstimateOut`/`UsageOut`），
  测试夹具同步更新。
- 测试：`tests/integration/test_cache.py` 4 项、`tests/integration/test_jobs.py` 8 项；
  `FakeProviderAdapter` 新增可控 `usage`（默认未知）。
- 文档：决策 0012；CONTRACTS 第 19 节；README 增加“任务与用量（T10）”；OpenAPI/前端类型已重新生成（25 条路径）。

## 验证证据

- `pytest backend/tests` → **283 passed**（T09 时 271；新增 4 + 8）；`ruff` 全绿；`scripts/verify.ps1` 退出码 0；
  前端 typecheck / vitest（29）/ build 全部通过。
- 门槛逐条核对：
  - **已完成窗口不重复调用**：同一任务跑两次，第二次 `calls=0`，适配器调用列表长度不变。
  - **未知远程结果不自动重发**：构造超租约的 DISPATCHED 尝试 → `reconcile_stale_runs` 标
    `UNKNOWN_OUTCOME` 且任务/窗口 `NEEDS_RECONCILIATION`；再跑任务 `calls=0`；
    只有显式 `POST /reconcile {"action":"retry"}` 才把窗口放回 QUEUED。
  - **每个尝试可追溯用量**：`inference_runs` 逐次记录 state/usage/elapsed/error_code；
    未知 usage 落 NULL 并计入 `unknown_usage_runs`，`GET /usage` 的 `total_tokens` 不把未知算进去。
  - 另覆盖 F16（预览→处理命中缓存、发送次数不增加、重复点击同幂等键复用任务）、
    F20（预算到顶不发调用、已知 usage 按口径结算）、暂停在窗口之间生效、估算纯本地。
- 本轮修复的**真实缺陷**：`run_job` 无条件置 RUNNING 覆盖 PAUSING 导致暂停失效（改为保留 PAUSING）；
  任务 API 草稿里的 `__import__` 取模型等临时写法清理；`JobOut`→`JobDetailOut` 契约变化后前端类型与夹具同步。
- 未验证（BLOCKED）：真实提供方的任务执行（无凭据）；后台工作循环/多进程调度属 T14。

## 未完成与已知问题

1. **后台执行是 FastAPI BackgroundTasks**：单进程、无租约续期，重启后的“孤儿”任务需要
   `reconcile_stale_runs`（目前由测试/后续 T14 调用；T14 会加入启动扫描与 UI 入口）。
2. **`POST /api/jobs/{id}/resume` 立即返回快照**（state=QUEUED），真实进度靠轮询 `GET /api/jobs/{id}`。
3. **预算只按输入 token 预留**：输出按每条目标 20 token 粗估；真实分词器/价格资料接入后需重算（T16/T17）。
4. **缓存不会过期**：依赖哈希变化会换键；手动清理接口留给 T14/T18。
5. **界面还没有任务面板**：T11 接入预览页与任务轮询。
6. 其余既有事项：`uv run` 在受限沙箱失败（脚本回退 venv）；`npm --prefix frontend install/ci` 需在包目录内执行；
   `alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`；`apply_patch` 失效时用 `.tools/newfile.ps1`；
   **测试窗口的目标必须全部被标注**（否则 `missing_targets`）。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**`、`frontend/e2e/fixtures/**` 的原始字节；已发布迁移 `0001`～`0004`；
  **`user_locked` 标注与用户密钥**。
- 当前版本号（都会进入缓存键/依赖哈希）：API 契约 `1`；数据库 `0004`；输出契约 `1.0`；
  提示词 `labeling-1`/`connection-1`；上下文 `context-1`；场景状态 `scene-state-1`；接受策略 `acceptance-1`；
  引擎 `attribution-engine-1`；调度器 `scheduler-1`；缓存 `cache-1`；扫描器 `quote-scan-1`。
- 提交习惯：每完成一部分功能即用 git 提交。