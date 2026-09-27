# 开发交接

更新时间：2026-09-28
当前任务：T13 待确认队列与阅读页确认抽屉（implementation / offline_verification 完成；live = BLOCKED）
最近完成任务：T13（此前 T00～T12：骨架→迁移→TXT→EPUB→阅读器→候选与金标准→配置页→适配器→上下文预算→场景引擎→任务缓存用量→标注投影与预览→人工更正与撤销）
下一任务与理由：T14 暂停恢复、预算到顶与故障闭环。T13 已经提供任务面板（`JobPanel`）与局部复核入口，
T14 要补上暂停/恢复、重启扫描、`NEEDS_RECONCILIATION` 处理、缺 Key 恢复、限流退避上限、
预算预留与未知用量展示，并给出「受影响范围重算」的显式入口（默认关闭自动付费重算）。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`。
- 页面：`/library`、`/books/:id/read`、`/books/:id/preview`、`/books/:id/review`、`/settings/models`。
- 数据目录与迁移：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0004`（= head）。
  测试/E2E：`NDR_CREDENTIAL_BACKEND=session`；E2E 另开 `NDR_ALLOW_FAKE_PROVIDER=1` 与
  `NDR_FAKE_PROVIDER_LABELS=deterministic`（仅测试，见 `frontend/playwright.config.ts`）。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`cd frontend; $env:NDR_E2E_BACKEND_CMD='..\backend\.venv\Scripts\python.exe -m ndr'; npx playwright test`。

## 本次改动

- 前端新增：`api/review.ts`、`pages/ReviewPage.tsx`（`/books/:id/review`）、
  `components/QuoteDetailDrawer.tsx`（阅读页与队列共用）、`QuoteContext.tsx`、`CorrectionForm.tsx`、
  `GapDecisionControls.tsx`、`RecheckPanel.tsx`；`api/books.ts` 增加 `fetchGaps` 与 `context_window_cp` 参数。
- `DocumentRenderer` 的**候选与标注**都可以点击 → 打开抽屉（未处理的对白也能确认）；
  `ReaderPage` 顶部显示待确认数量并链接到队列页；更正后失效 `annotations`/`review-items` 查询。
- 后端新增 `POST /api/quotes/{id}/recheck`（202）：围绕当前场景（其次章节）创建 `RECHECK` 任务，
  必须显式给出 `profile_id` 与预算；顺带把 `_job_detail` 提升成 `jobs/service.py::job_detail` 复用。
- 修复：`GET /api/quotes/{id}` 把 `scene` 以 dict 赋值给 Pydantic 模型字段
  （`PydanticSerializationUnexpectedValue` 警告）→ 改为构造 `SceneRefOut`。
- 文档：决策 0015；CONTRACTS 第 22 节（并在第 21 节补 `recheck` 行）；README 增加「待确认队列与确认抽屉（T13）」。

## 验证证据

- 后端：`pytest backend/tests` → **303 passed**；`ruff` 全绿；OpenAPI 与 `docs/openapi.json` 一致。
- 前端：`typecheck` 通过；`vitest` → **55 passed**（T12 时 41；新增 `CorrectionForm` 5、`QuoteDetailDrawer` 5、
  `ReviewPage` 4）；`vite build` 通过。
- E2E：**15 passed**（T12 时 12）。新增 `frontend/e2e/review.spec.ts` 3 项：
  - 阅读页入口：点着色对白 → 抽屉（`ACCEPTED`/S1）→ 展开原文（只读）→ 标记待确认 → 锁定未知 → 撤销回 `ACCEPTED`；
    关闭抽屉后阅读页仍然着色（说明投影未被破坏）。
  - 待定入口：队列按 `USER_FLAGGED` 过滤 → 打开抽屉 → 跳过（延后）→ 状态切「已跳过」仍能找到；
    并验证**未处理章节（第二章）的空候选**：抽屉显示「还没有标注」「本场景还没有已有说话人」，
    只能新建说话人，提交后变成 `USER_CONFIRMED`。
  - 旧版本 409：绕过界面直接改掉标注，抽屉提交旧版本 → 提示「已被其它操作更新」。
- 门槛逐条核对：
  - **用户可不写代码完成所有人工确认**：四种更正、Gap 决定、跳过、撤销、局部复核都在界面上完成（E2E 覆盖）。
  - **队列清空不被等同于全部识别正确**：`/review` 顶部与空结果都写明这一点，并显示 `counts`。
  - **展开原文与模型复核是不同操作**：前者只改 `context_window_cp`（本地原文），后者是折叠区里的独立按钮，
    必须选配置与预算并创建真实任务（E2E 断言展开原文不会创建任务）。
- 未验证（BLOCKED）：真实提供方的复核效果（无凭据，属 T16）；T14 的暂停/预算/恢复闭环。

## 未完成与已知问题

1. **Live 仍未打通**：T07/T09/T10/T11/T12/T13 的 Live 都是 BLOCKED；T16 效果评测未开始。
2. **`POST /api/quotes/{id}/recheck` 的范围是「当前场景，其次章节」**：跨窗口的大场景复核仍会按窗口拆分；
   T14 会补「受影响范围重算」入口与默认关闭的自动重算。
3. **队列分页是「追加」模式**：切换筛选会重置列表；`next_cursor` 只在当前筛选下有效。
4. **队列列表把对白/Gap 文本在前端 join**（`fetchQuotes`/`fetchGaps` 各取前 500/200 条）：
   超长书目的队列条目可能显示为「（对白）」；后续可让后端在队列项里内联摘要。
5. **抽屉是固定右栏**（`position: fixed`），窄屏会占满宽度；移动端体验属 T18。
6. **`updated_review_counts` 是扁平 map**（状态值 + 原因值 + `total`）：前端不要自己推算计数。
7. 其余既有事项：`uv run` 在受限沙箱失败（脚本回退 venv）；`npm --prefix frontend install/ci` 需在包目录内执行；
   `alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`；`apply_patch` 失效时用 `.tools/newfile.ps1` / `.tools/append.ps1`；
   **测试窗口的目标必须全部被标注**；E2E 数据目录在同一次运行里共享（新用例不要假设书签/配置是干净的，
   `review.spec.ts` 因此显式点击第一章）；`pytest -q` 因 `addopts=-q` 变成双 `-q` 会**不打印统计行**。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**`、`frontend/e2e/fixtures/**` 的原始字节；已发布迁移 `0001`～`0004`；
  **`user_locked` 标注与用户密钥**；`corrections` / `annotation_history` / `identity_revisions` 只追加。
- 当前版本号（进入缓存键/依赖哈希）：API 契约 `1`；数据库 `0004`；输出契约 `1.0`；
  提示词 `labeling-1`/`connection-1`；上下文 `context-1`；场景状态 `scene-state-1`；接受策略 `acceptance-1`；
  引擎 `attribution-engine-1`；调度器 `scheduler-1`；缓存 `cache-1`；扫描器 `quote-scan-1`；
  更正服务 `correction-1`（内部常量，不进入缓存键）。
- 提交习惯：每完成一部分功能即用 git 提交。