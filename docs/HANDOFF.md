# 开发交接

更新时间：2026-09-28
当前任务：T11 真实效果预览与按章处理（implementation / offline_verification 完成；live = BLOCKED，quality 未开始）
最近完成任务：T11（此前 T00～T10：骨架→迁移→TXT→EPUB→阅读器→候选与金标准→配置页→适配器→上下文预算→场景引擎→任务缓存用量）
下一任务与理由：T12 人工更正、分组修订与撤销后端。T11 已经把「当前有效投影」暴露给前端
（`GET /api/books/{id}/annotations` 的颜色/编号/图例/统计），T12 要在此基础上支持普通对白详情与主动标记、
`assign_existing`/`create_speaker`/`set_kind`/`mark_unknown`、Gap 更正、merge/split 与撤销
（版本校验、`user_locked` 优先、历史留痕、下游 stale）。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`。
- 页面：`/library`、`/books/:id/read`（阅读 + 标注投影）、`/books/:id/preview`（预览与按章处理）、`/settings/models`。
- 数据目录与迁移：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0004`（= head）。
  测试/E2E：`NDR_CREDENTIAL_BACKEND=session`；E2E 另开 `NDR_ALLOW_FAKE_PROVIDER=1` 与
  `NDR_FAKE_PROVIDER_LABELS=deterministic`（仅测试，见 `frontend/playwright.config.ts`）。
- 验证命令：`pwsh -File scripts/verify.ps1`。

## 本次改动

- `ndr/scenes/projection.py`：只读投影。场景内按首次发言顺序给稳定 `color_index`（与 S1/S2 同源）；
  初读且 `visible_from_cp > horizon` 的条目下发 `withheld=true` 且 label/color 为 null；
  UNKNOWN 不给分组/颜色；统计含 `unprocessed_quotes`；`legend` 只列本范围内实际出现过的分组。
- `ndr/api/annotations.py` + `ndr/domain/annotations.py`：`GET /api/books/{id}/annotations`
  （`start_cp`/`end_cp`/`reading_mode`/`visible_horizon_cp`），已挂进 `app.py`；OpenAPI 与前端类型重新生成。
- 测试专用开关：`NDR_FAKE_PROVIDER_LABELS`（`unknown` 默认 / `deterministic`），经 `AdapterSpec` 传到
  `FakeProviderAdapter`；`ndr/llm/adapters/fake.py` 新增确定性标注脚本
  （第一句 NEW + `new1`，其余 EXISTING，`basis=DIRECT` → ACCEPTED）。
- 前端：`DocumentRenderer` 按标注边界分片着色，编号渲染为**真实文本节点** `〔S1〕`；
  `AnnotationLayer`/`SpeakerLegend`/`RangePicker`/`BudgetForm`/`EstimateSummary`/`UsageSummary` 新组件；
  `PreviewPage`（`/books/:bookId/preview`）做范围选择 → 本地估算 → 试运行 → 任务面板 → 原文/标注对比；
  `ReaderPage` 接入同一套投影（读取模式切换 + 图例 + 可关闭标注）；`JobPanel` 显示真实
  `calls`/`cached_windows`/`unknown_usage_runs` 并在终态回调刷新投影与用量。
- 文档：决策 0013；CONTRACTS 第 20 节；README 增加“预览与按章处理（T11）”与两个测试专用环境变量。

## 验证证据

- `pytest backend/tests` → **287 passed**（T10 时 283；新增 `test_annotations_projection.py` 4 项）；
  `ruff` 全绿；OpenAPI 与 `docs/openapi.json` 一致；`scripts/verify.ps1` 退出码 0。
- 前端：`typecheck` 通过；`vitest` → **41 passed**（T10 时 29；`DocumentRenderer` 标注 6 项、
  `PreviewPage` 4 项、`ReaderPage` 新增 2 项）；`vite build` 通过。
- Playwright（真实后端 + 真实 Chromium + 隔离数据目录，模型侧是显式启用的确定性 FakeProvider）→ **12 passed**：
  - TXT：估算（窗口/目标/token）→ 试运行 `mode=preview` → 着色（`data-status=ACCEPTED`、`〔S1〕`、图例 S1）
    → 原文/标注切换后原文不变且**调用数不变** → 按此范围正式处理 `calls=0` 且 `cached_windows == windows_total`。
  - EPUB：同一流程能着色，且 ruby 注音仍在 `<rt>` 里（没有被当正文重复输出）。
  - 按章处理：切换章节后范围随目录变化，着色跟着换。
- 门槛逐条核对：
  - **UI 由后端实际任务结果驱动**：任务面板直接显示后端 `calls`/`cached_windows`，前端不合成成功状态。
  - **正常配置无法显示伪造的成功结果**：FakeProvider 必须 `NDR_ALLOW_FAKE_PROVIDER=1` 才可用，
    且界面明确标注“没有访问任何真实服务”；确定性脚本是额外的测试专用开关，真实提供方不会走到分支。
  - **TXT/EPUB 均能着色**：两条 E2E 分别覆盖。
  - **样式/颜色切换模型调用数为零**：组件测试 + E2E 都断言切换前后调用次数不变。
- 本轮修复的**真实缺陷**：预览页把整段预算 JSON 拼进 `idempotency_key`，超过后端 128 字符上限
  导致创建任务 422（改为 FNV-1a 短摘要）；`settings.spec.ts` 原先假设配置列表为空，
  多个配置并存时 strict mode 冲突（改为按名称限定卡片 + 唯一名称）。
- 未验证（BLOCKED）：真实提供方的预览效果（无凭据、无预算）；真实小说的着色/归属质量（T16，未开始）。

## 未完成与已知问题

1. **Live 仍未打通**：没有真实提供方凭据，T07/T09/T10/T11 的 Live 都是 BLOCKED；T16 的效果评测未开始。
2. **投影没有独立版本常量**：它是读时派生（不入缓存键）。将来导出需要冻结投影时（T15）应补
   `visibility_policy` 之类的版本号。
3. **预览页文档只加载当前章节前 500 个节点**：整本范围时视图可能只显示前一段，标注/估算仍按完整范围算。
4. **任务面板仍靠轮询**（终态即停）：后台工作循环、租约续期与启动对账属 T14。
5. **未知用量与预算**仍按启发式 token 口径；真实分词器/价格资料接入后需重算（T16/T17）。
6. 其余既有事项：`uv run` 在受限沙箱失败（脚本回退 venv）；`npm --prefix frontend install/ci` 需在包目录内执行；
   `alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`；`apply_patch` 失效时用 `.tools/newfile.ps1`；
   **测试窗口的目标必须全部被标注**（否则 `missing_targets`）；E2E 数据目录在同一次运行里共享，
   新用例不要假设配置/书库是空的。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**`、`frontend/e2e/fixtures/**` 的原始字节；已发布迁移 `0001`～`0004`；
  **`user_locked` 标注与用户密钥**。
- 当前版本号（进入缓存键/依赖哈希）：API 契约 `1`；数据库 `0004`；输出契约 `1.0`；
  提示词 `labeling-1`/`connection-1`；上下文 `context-1`；场景状态 `scene-state-1`；接受策略 `acceptance-1`；
  引擎 `attribution-engine-1`；调度器 `scheduler-1`；缓存 `cache-1`；扫描器 `quote-scan-1`。
- 提交习惯：每完成一部分功能即用 git 提交。