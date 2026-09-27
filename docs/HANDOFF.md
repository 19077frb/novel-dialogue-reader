# 开发交接

更新时间：2026-09-28
当前任务：T14 暂停恢复、预算到顶与故障闭环（implementation / offline_verification 完成；live = BLOCKED）
最近完成任务：T14（此前 T00～T13：骨架→迁移→TXT→EPUB→阅读器→候选与金标准→配置页→适配器→上下文预算→场景引擎→任务缓存用量→标注投影与预览→人工更正与撤销→确认队列与抽屉）
下一任务与理由：T15 证据时点、阅读投影与最终定位回归。T13/T14 已经把可见性（initial/reread + horizon）、
标注历史、身份修订与恢复动作都接好了，T15 要做跨这些能力的回归：整章预计算 + 初读受限展示、
跨节点 quote、ruby、特殊字符、页面重排后的定位一致性。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`。
- 页面：`/library`、`/books/:id/read`、`/books/:id/preview`、`/books/:id/review`、`/settings/models`。
- 数据目录与迁移：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0004`（= head）。
  测试/E2E：`NDR_CREDENTIAL_BACKEND=session`；E2E 另开 `NDR_ALLOW_FAKE_PROVIDER=1` 与
  `NDR_FAKE_PROVIDER_LABELS=deterministic`。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`cd frontend; $env:NDR_E2E_BACKEND_CMD='..\backend\.venv\Scripts\python.exe -m ndr'; npx playwright test`。

## 本次改动

- `ndr/recovery/service.py`（新）：`job_recovery()`（动作 + 说明 + 付费标记 + 退避秒数 + 是否缺凭据）、
  `recover_on_startup()`（超租约尝试→未知结果、PAUSING→PAUSED、RUNNING→PARTIAL）、`recovery_actions()`。
- `ndr/domain/recovery.py`（新）+ `GET /api/jobs/{id}/recovery`；lifespan 里执行启动扫描
  （失败只记警告，不阻止启动）。
- `jobs/scheduler.py`：`_dispatch_with_bounded_retry()`（只有 RATE_LIMITED/UNAVAILABLE 按
  `min(base·2^(n-1), cap)` 有上限退避，超时不自动重试，每次失败尝试单独写 `inference_runs`）；
  `_build_adapter()` 增加缺凭据判定（非 FakeProvider + 用户选了密钥模式 + 取不到密钥）；
  `run_job` 在适配器构建失败时落到可解释的 `FAILED`（此前会抛异常并把任务留在 RUNNING）。
- 配置：`NDR_RATE_LIMIT_MAX_RETRIES`、`NDR_RATE_LIMIT_BACKOFF_BASE_SECONDS`、
  `NDR_RATE_LIMIT_BACKOFF_MAX_SECONDS`、`NDR_STALE_RUN_LEASE_SECONDS`、`NDR_RECOVER_ON_STARTUP`、
  `NDR_FAKE_PROVIDER_SCRIPT`；FakeProvider 失败脚本也可写在模型配置的 `params.script` 里（按用例切换）。
- 前端：`JobPanel` 渲染后端给的恢复动作（暂停/继续/立即执行/保留未知/确认重发/去补凭据），
  显示真实 `calls`/`cached_windows`/`unknown_usage_runs` 与退避建议；预览页新增
  `preview-recompute`（显式重算入口）与「不会自动重算」说明；`api/jobs.ts` 增加恢复相关调用。
- 文档：决策 0016；CONTRACTS 第 23 节；README 增加「暂停、预算与故障恢复（T14）」。

## 验证证据

- 后端：`pytest backend/tests` → **309 passed**（T13 时 303；新增 `test_recovery.py` 6 项）；`ruff` 全绿；
  OpenAPI 与 `docs/openapi.json` 一致。
- 前端：`typecheck` 通过；`vitest` → **59 passed**（T13 时 55；新增 `JobPanel` 4 项）；`vite build` 通过。
- E2E：**18 passed**（T13 时 15）。新增 `recovery.spec.ts` 3 项覆盖 T14 的门槛：
  - 预算到顶：`BUDGET_EXHAUSTED`、`calls=0`、恢复动作 `new_job` + 「不会自动重算」文案；
    调整预算后点显式重算 → `COMPLETED` 且着色出现。
  - 缺 Key：`FAILED` + 恢复摘要 + 「去补充模型凭据」链接；跳回阅读页仍能读到原文与候选。
  - 提供方超时：`NEEDS_RECONCILIATION` + 未知用量计数为 1（不写 0）+ 保留未知（免费）与确认重发（可能计费）；
    点「保留未知结果」后转 `PARTIAL`。
- 门槛逐条核对：
  - **任务失败不会让原文不可读**：E2E 在 `FAILED` 与 `NEEDS_RECONCILIATION` 状态下都回到阅读页验证原文。
  - **未知付费结果不自动重发**：后端 F15 用例断言重跑任务不产生新尝试；只有显式 reconcile retry 才回队列。
  - **每种非完成状态都有可理解的恢复动作**：`GET /recovery` 覆盖 QUEUED/RUNNING/PAUSING/PAUSED/PARTIAL/
    BUDGET_EXHAUSTED/NEEDS_RECONCILIATION/FAILED（含缺凭据）并有组件测试。
- 本轮修复的**真实缺陷**：适配器构建异常导致任务卡在 `RUNNING`（现在落到可解释的 `FAILED`）；
  缺凭据判定最初会误伤不需要密钥的协议（改为「非 FakeProvider + 密钥模式 + 取不到密钥」）。
- 未验证（BLOCKED）：真实提供方的限流/超时行为（无凭据，属 T16/T18）。

## 未完成与已知问题

1. **Live 仍未打通**：T07/T09/T10/T11/T12/T13/T14 的 Live 都是 BLOCKED；T16 效果评测未开始。
2. **后台执行仍是 FastAPI BackgroundTasks**：单进程、无多进程 worker；启动扫描只在进程启动时跑一次，
   运行期间不会周期性对账（T18 可加入定时扫描）。
3. **限流退避是「同步小睡」**：上限 30 秒，任务在后台线程里等待；真实提供方的 `Retry-After` 头尚未采用。
4. **预算不可变**：预算到顶后只能新建任务（缓存复用已完成窗口）；T17 若要支持「改预算续跑」需新增端点。
5. **E2E 无法重启 webServer**：进程重启场景由 `backend/tests/integration/test_recovery.py` 覆盖，
   E2E 只覆盖界面可见的状态与动作。
6. **恢复动作的中文文案由后端下发**：前端不翻译，改动文案属于契约变更（会体现在 OpenAPI 里）。
7. 其余既有事项：`uv run` 在受限沙箱失败（回退 venv）；`npm --prefix frontend install/ci` 需在包目录内执行；
   `alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`；`apply_patch` 失效时用 `.tools/newfile.ps1` / `.tools/append.ps1`；
   **编辑多行文本前先统一换行符**（CRLF/LF 混用会让替换模式失配）；**测试窗口的目标必须全部被标注**；
   E2E 数据目录在同一次运行里共享（新用例要显式选章节/配置）；`pytest -q` 因 `addopts=-q` 双 `-q` 不打印统计行。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**`、`frontend/e2e/fixtures/**` 的原始字节；已发布迁移 `0001`～`0004`；
  **`user_locked` 标注与用户密钥**；`corrections` / `annotation_history` / `identity_revisions` / `inference_runs` 只追加。
- 当前版本号（进入缓存键/依赖哈希）：API 契约 `1`；数据库 `0004`；输出契约 `1.0`；
  提示词 `labeling-1`/`connection-1`；上下文 `context-1`；场景状态 `scene-state-1`；接受策略 `acceptance-1`；
  引擎 `attribution-engine-1`；调度器 `scheduler-1`；缓存 `cache-1`；扫描器 `quote-scan-1`；
  更正服务 `correction-1`、恢复服务 `recovery-1`（内部常量，不进入缓存键）。
- 提交习惯：每完成一部分功能即用 git 提交。