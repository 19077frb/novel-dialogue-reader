# 开发交接

更新时间：2026-09-28
当前任务：T15B 导出对话框、样张与下载闭环（implementation / offline_verification 完成；live = BLOCKED）
最近完成任务：T15B（此前 T00～T15A：骨架→迁移→TXT→EPUB→阅读器→候选与金标准→配置页→适配器→上下文预算→场景引擎→任务缓存用量→标注投影与预览→人工更正与撤销→确认队列与抽屉→暂停预算与故障恢复→初读身份与码点定位→导出后端与标准校验）
下一任务与理由：**T16 真实样本评测与可复现实验**。T16 需要真实模型凭据、预算与人工确认的作品样本，
这些当前都不具备，因此 T16 必须先交付**离线可运行的评测工具与数据校验**
（annotation-guide、作品级划分、不可判定标签、指标脚本、只校验数据的离线命令），
真实调用与效果数字留到有凭据时再跑，状态保持未完成。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`。
- 页面：`/library`、`/books/:id/read`、`/books/:id/preview`、`/books/:id/review`、`/settings/models`；
  阅读页与预览页右上角的「导出」打开导出对话框。
- 数据目录与迁移：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0005`（= head）。
  测试/E2E：`NDR_CREDENTIAL_BACKEND=session`；E2E 另开 `NDR_ALLOW_FAKE_PROVIDER=1` 与
  `NDR_FAKE_PROVIDER_LABELS=deterministic`。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`cd frontend; $env:NDR_E2E_BACKEND_CMD='..\backend\.venv\Scripts\python.exe -m ndr'; npx playwright test`。

## 本次改动

- 前端新增：`api/exports.ts`；`ExportDialog`（范围/样式/初读策略/格式 → 冻结样张 → 生成 → 校验 → 下载）、
  `ExportScopePicker`、`ExportStylePreview`、`ExportProgress`、`ExportDownload`；阅读页与预览页各加「导出」入口。
- 样张放在 `sandbox=""` 的 iframe（后端渲染器输出，不执行脚本）；
  快照过期用**内容哈希**比较（`snapshot_hash`），只有标注真变了才提示；
  失败（内部检查不通过）不提供下载；点开时显式重新冻结（组件不会因关闭而卸载）。
- 文档：决策 0019；CONTRACTS 第 26/27 节；README 增加「导出界面（T15B）」。

## 验证证据

- 前端：`typecheck` 通过；`vitest` → **70 passed**（T15A 时 65；新增 `ExportDialog` 5 项）；`vite build` 通过。
- 后端：`pytest backend/tests` → **328 passed**（未改动后端逻辑，回归确认）；`ruff` 全绿。
- E2E：**28 passed**（T15A 时 23；新增 `export.spec.ts` 5 项）：
  - TXT→HTML：样张在沙箱 iframe、校验报告（`text_consistency` ✓ / 标准 `NOT_APPLICABLE`）、
    下载文件名含「标注版」、成品**无 `http(s)`、无 `<script>`、含 `〔S1〕`**；
  - EPUB→EPUB：`mimetype_first`/`mimetype_stored`/`resource_closure` ✓、标准检查显示 `NOT_RUN`、
    重复下载文件名与字节数一致；
  - 指定章节 → 文件名含「节选」、成品不含未选章节正文；
  - 未处理章节 → 统计「未处理 1」+ 警告「还没有标注」，成品里该对白无颜色 span（保持原样）；
  - 并发纠正 → 重新打开提示「旧快照」，已生成文件不受影响。
- 门槛逐条核对：**用户不运行脚本即可选择、预览、生成、下载**✓（E2E 全程走界面）；
  **关闭应用后仍可阅读**✓（HTML 无外部依赖、EPUB 自带资源，均由断言覆盖）；
  **颜色被覆盖时编号仍起作用**✓（成品含真实文本 `〔S1〕`，样式预设含「仅编号」）。
- 本轮修复的问题：①对话框只靠 `refetchOnMount` 不会在重新打开时刷新（组件未卸载）→ 改为打开时显式重新冻结；
  ②`import-reader.spec` 的候选文本断言假设“该书从未被处理过”，而新用例会先处理同一本书 → 改为包含匹配；
  ③导出测试原夹具与 `sample-review.txt` 内容相同，被 sha256 去重成同一本书导致卡片找不到 → 换独立夹具。
- **未验证（保留，BLOCKED）**：
  - **EPUBCheck 标准检查未运行**：本机没有 `tools/epubcheck/epubcheck.jar`（有 Java，但不能联网安装）；
  - **独立 EPUB 阅读器试读未做**：本机未安装任何独立阅读器（无 Calibre / SumatraPDF / Thorium 等），
    也不能联网安装。**没有用浏览器样张或自研校验替代这两项。**

## 未完成与已知问题

1. **Live 仍未打通**：T07/T09～T15B 的 Live 都是 BLOCKED；T16 效果评测未开始。
2. **导出对话框一次只生成一个文件**：需要同时要 EPUB 与 HTML 时要点两次（后端已支持复用快照）。
3. **快照过期只提示不自动重生成**：符合「不静默混合」的要求，但用户需要手动点一次「生成」。
4. **`export_artifacts` 只增不减**：产物保留在 `data/exports/`；清理策略留待 T18。
5. **导出同步执行**：大书的打包在请求内完成（本地 IO，无网络）。
6. 其余既有事项：`uv run` 在受限沙箱失败（回退 venv）；`npm --prefix frontend install/ci` 需在包目录内执行；
   `alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`；`apply_patch` 失效时用 `.tools/newfile.ps1` / `.tools/append.ps1`；
   **编辑多行文本前先统一换行符**；E2E 数据目录在同一次运行里共享——**新夹具的内容必须与既有夹具不同**
   （sha256 相同的导入会被去重成同一本书），且新用例不要依赖别的用例是否处理过某本书。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**`、`frontend/e2e/fixtures/**` 的原始字节；已发布迁移 `0001`～`0005`；
  **`user_locked` 标注与用户密钥**；`corrections` / `annotation_history` / `identity_revisions` / `inference_runs` 只追加；
  `export_snapshots` 不可变，`export_artifacts` 只追加（同指纹才复用）。
- 当前版本号（进入缓存键/依赖哈希）：API 契约 `1`；数据库 `0005`；输出契约 `1.0`；
  提示词 `labeling-1`/`connection-1`；上下文 `context-1`；场景状态 `scene-state-1`；接受策略 `acceptance-1`；
  引擎 `attribution-engine-1`；调度器 `scheduler-1`；缓存 `cache-1`；扫描器 `quote-scan-1`；
  更正服务 `correction-1`、恢复服务 `recovery-1`、导出快照 `export-snapshot-1`、导出器 `exporter-1`。
- 提交习惯：每完成一部分功能即用 git 提交。