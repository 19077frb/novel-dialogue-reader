# 开发交接

更新时间：2026-09-28
当前任务：T12 人工更正、分组修订与撤销后端（implementation / offline_verification 完成；live = BLOCKED）
最近完成任务：T12（此前 T00～T11：骨架→迁移→TXT→EPUB→阅读器→候选与金标准→配置页→适配器→上下文预算→场景引擎→任务缓存用量→标注投影与预览）
下一任务与理由：T13 待确认队列与阅读页确认抽屉（前端）。T12 已提供队列查询/详情/延后、主动标记、
四种说话人更正、Gap 更正、merge/split 与撤销的完整后端（含权威计数与 stale 依赖），
T13 只需在阅读页把普通对白详情、队列筛选、候选证据与更正/撤销操作接上。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`。
- 页面：`/library`、`/books/:id/read`、`/books/:id/preview`、`/settings/models`。
- 数据目录与迁移：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0004`（= head）。
  测试/E2E：`NDR_CREDENTIAL_BACKEND=session`；E2E 另开 `NDR_ALLOW_FAKE_PROVIDER=1` 与
  `NDR_FAKE_PROVIDER_LABELS=deterministic`（仅测试，见 `frontend/playwright.config.ts`）。
- 验证命令：`pwsh -File scripts/verify.ps1`。

## 本次改动

- `ndr/corrections/`（新包）：
  - `service.py`：对白更正（`assign_existing`/`create_speaker`/`set_kind`/`mark_unknown`）、Gap 更正、撤销。
  - `review.py`：待确认队列查询/详情/幂等标记/延后 + 对白详情（含标注、场景、可用分组、队列项）。
  - `invalidator.py`：依赖失效器（同窗口/同旧分组的下游标 `stale` + `STALE_DEPENDENCY` 项）。
  - `scenes.py`：场景归属调整（正向按新场景重新编号，撤销按记录的 `speaker_id` 精确还原）。
  - `identity.py`：场景内 `MERGE`/`SPLIT` + `identity_revisions` 留痕。
  - `history.py`：标注快照与 `annotation_history` 追加写。
- API：`GET /api/books/{id}/review-items`、`GET /api/review-items/{id}`、`POST /api/review-items/{id}/defer`、
  `POST /api/quotes/{id}/review-items`、`POST /api/quotes/{id}/corrections`、`POST /api/gaps/{id}/corrections`、
  `POST /api/scenes/{id}/speaker-revisions`、`POST /api/corrections/{id}/undo`。
- `GET /api/quotes/{id}` 增补 `annotation`/`scene`/`scene_groups`/`review_items`/`can_correct`。
- **修复真实缺陷（F14）**：`jobs/scheduler.py` 之前没有把 `user_locked` 的对白与分组传给引擎，
  模型结果会覆盖人工确认；现在 `_apply_payload` 收集并传入 `locked_quote_ids` / `locked_group_ids`。
- 文档：决策 0014；CONTRACTS 第 21 节；README 增加“人工更正与待确认队列（T12）”。

## 验证证据

- `pytest backend/tests` → **302 passed**（T11 时 287；新增 `test_corrections.py` 9 项 +
  `test_identity_revisions.py` 6 项）；`ruff` 全绿；OpenAPI/前端类型已重新生成（`npm run generate:api`）。
- 门槛逐条核对：
  - **一个事务得到一致结果**：单个请求内完成「更正记录 + 旧快照 + 当前投影 + 队列 + stale」，失败整体回滚。
  - **手动更正和撤销没有模型请求**：断言 `inference_runs` 行数在更正/撤销前后不变（同进程无任何适配器调用）。
  - **不能通过硬删除历史实现撤销**：`annotations` 行数不变、`annotation_history` 与 `corrections` 只增；
    撤销写一条 `action=undo` 的记录并把 `undone_by` 指回去。
  - **并发旧版本**：`expected_version` 不匹配 → 409 `VERSION_CONFLICT`，目标状态与记录数完全不变。
  - **撤销越过新修订**：先改 A、再改 B，撤销 A → 409 且保留 B 的结果；撤销 B 成功恢复到 A 的状态。
  - **跨场景误关联**：`assign_existing` 引用别的场景的分组 → 422 `CROSS_SCENE_SPEAKER`；多目标跨场景 → `CROSS_SCENE_SCOPE`。
  - **人工锁定**：锁定后换模型名绕过缓存真的调用适配器，锁定对白的状态与版本不变（F14）。
- 未验证（BLOCKED）：更正后重新推理的真实效果（无凭据，属 T16）；队列界面（属 T13）。

## 未完成与已知问题

1. **Live 仍未打通**：T07/T09/T10/T11/T12 的 Live 都是 BLOCKED；T16 效果评测未开始。
2. **Gap 的 `CONTINUE` 合并只在右侧属于“另一个场景”时生效**：若模型没有把两侧切成两个场景，
   确认 `CONTINUE` 只记录决定与队列状态，不做结构变更（这是正确行为，但前端要如实展示）。
3. **被合并/拆分后变空的分组行会保留**（历史与 `identity_revisions` 需要它）：图例只显示仍有引语的分组。
4. **撤销 Gap `BREAK` 不重建场景 ID**：按记录恢复到原场景与原分组；新场景变为已关闭的空场景。
5. **`updated_review_counts` 是扁平 map**（状态值 + 原因值 + `total`）：前端不要自己推算计数。
6. **队列分页按 `review_items.id`**：cursor 只保证稳定顺序，不保证按时间插入（同一毫秒建项时按 id 兜底）。
7. **T13 需要的新前端工作**：普通对白详情抽屉、队列筛选、候选证据展示、更正/撤销按钮与冲突提示。
8. 其余既有事项：`uv run` 在受限沙箱失败（脚本回退 venv）；`npm --prefix frontend install/ci` 需在包目录内执行；
   `alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`；`apply_patch` 失效时用 `.tools/newfile.ps1` / `.tools/append.ps1`；
   **测试窗口的目标必须全部被标注**（否则 `missing_targets`）；E2E 数据目录在同一次运行里共享，
   新用例不要假设配置/书库是空的；`pytest -q` 会因 `addopts=-q` 变成双 `-q` 而**不打印统计行**。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**`、`frontend/e2e/fixtures/**` 的原始字节；已发布迁移 `0001`～`0004`；
  **`user_locked` 标注与用户密钥**；`corrections` / `annotation_history` / `identity_revisions` 是审计数据，只追加。
- 当前版本号（进入缓存键/依赖哈希）：API 契约 `1`；数据库 `0004`；输出契约 `1.0`；
  提示词 `labeling-1`/`connection-1`；上下文 `context-1`；场景状态 `scene-state-1`；接受策略 `acceptance-1`；
  引擎 `attribution-engine-1`；调度器 `scheduler-1`；缓存 `cache-1`；扫描器 `quote-scan-1`；
  更正服务 `correction-1`（仅内部常量，不进入缓存键）。
- 提交习惯：每完成一部分功能即用 git 提交。