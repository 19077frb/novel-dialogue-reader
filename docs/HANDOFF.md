# 开发交接

更新时间：2026-09-28
当前任务：T15A EPUB/HTML 导出后端与标准校验（implementation / offline_verification 完成；live = BLOCKED）
最近完成任务：T15A（此前 T00～T15：骨架→迁移→TXT→EPUB→阅读器→候选与金标准→配置页→适配器→上下文预算→场景引擎→任务缓存用量→标注投影与预览→人工更正与撤销→确认队列与抽屉→暂停预算与故障恢复→初读身份与码点定位）
下一任务与理由：T15B 导出对话框、样张与下载闭环（前端）。T15A 已提供冻结快照、后端样张、生成、状态与受控下载，
以及可复现的校验报告；T15B 只需在阅读页/预览页接上 ExportDialog，并把校验状态与快照过期提示展示清楚。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`。
- 页面：`/library`、`/books/:id/read`、`/books/:id/preview`、`/books/:id/review`、`/settings/models`。
- 数据目录与迁移：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision **`0005`（= head）**：T15A 新增
  `export_snapshots` / `export_artifacts`。
- 测试/E2E：`NDR_CREDENTIAL_BACKEND=session`；E2E 另开 `NDR_ALLOW_FAKE_PROVIDER=1` 与
  `NDR_FAKE_PROVIDER_LABELS=deterministic`。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`cd frontend; $env:NDR_E2E_BACKEND_CMD='..\backend\.venv\Scripts\python.exe -m ndr'; npx playwright test`。

## 本次改动

- 迁移 `0005_export_snapshots`：`export_snapshots`（冻结投影/身份/策略/样式/revision/hash/warnings）、
  `export_artifacts`（format/fingerprint/job/state/相对路径/sha256/大小/validation）。
- `ndr/exports/`（新包）：
  - `snapshot.py`：`freeze_snapshot`（整本 vs 节选、`position_safe` 用阅读位置作 horizon）、`build_warnings`；
  - `render.py`：统一渲染模型、8 色板 + 共用 CSS、三种样式预设、**未知/未处理/stale/withheld 保持原样**；
  - `html.py`：单文件 HTML（内联 CSS + `data:` 图片，无脚本/无外链）；
  - `epub.py`：EPUB 3 打包（mimetype 第一且不压缩、container/OPF/nav/CSS/章节/图片，时间戳固定 → 可复现）；
  - `validation.py`：内部检查（结构/mimetype/资源闭合/外部引用/localhost/正文逐段一致性）+ EPUBCheck 包装器
    （参数数组调用，缺 jar/java 报 `NOT_RUN`）；
  - `service.py`：指纹幂等、资源读取（复用 `ingest.resources.read_resource_bytes`，EPUB 从源包读）、
    写文件 + 校验 + 状态落库、`render_sample`/`coverage_of`。
- API：`POST /api/books/{id}/exports/preview`、`POST /api/books/{id}/exports`、`GET /api/exports/{id}`、
  `GET /api/exports/{id}/download`（受控下载，正确 MIME 与中文文件名）。
- 脚本与样例：`backend/scripts/validate_exports.py`；`evaluation/examples/exports/`
  （由 `minimal-txt-001/text.txt` + 离线确定性 FakeProvider 生成的 EPUB/HTML + `manifest.json` + README）。

## 验证证据

- 后端：`pytest backend/tests` → **328 passed**（T15 时 314）；`ruff` 全绿；OpenAPI 与 `docs/openapi.json` 一致。
- 覆盖矩阵（F21/F22/F26/F27/F30 后端部分）：
  - F21：TXT→EPUB/HTML；正文完整、`〔S1〕` 为真实文本、下载 MIME 与中文文件名正确、
    **导出前后 `inference_runs` 数量不变**（零模型调用）。
  - F22：EPUB（ruby+图片）→EPUB/HTML；`resource_closure=true`、注音不进正文、图片随包、
    HTML 里图片内联为 `data:image/png;base64,`。
  - F26：只导出第一章 → 只带该章引用的资源，文件名标「（节选）」。
  - F27：坏 zip / 未完成产物：内部检查判失败；非 COMPLETED 的产物下载返回 409。
  - F30：同快照同格式重复导出复用同一 artifact（同 sha256、库里只有一行），重复下载字节一致。
  - 快照隔离：冻结后做人工更正 → 旧快照仍导出旧内容；新预览得到新快照与不同 `snapshot_hash`。
- CLI 实跑（样例）：
  `[epub] …internal=ok · standard=NOT_RUN (未提供 --epubcheck-jar)`、`[html] …standard=NOT_APPLICABLE`，退出码 0；
  损坏文件与缺失文件返回 1。
- 门槛逐条核对：输出为**离线可读的真实文本文件**（HTML 无外链、EPUB 无远程引用）✓；
  **原文与源书不被覆盖**（写 `data/exports/<artifact_id>/`）✓；**未知保持原样**✓；
  **导出无模型调用**✓（测试断言）；**内部检查有证据、标准检查状态真实**✓（`NOT_RUN` 不伪装 PASS）。
- 本轮修复的**真实缺陷**：导出曾按数据目录相对路径读资源，而 EPUB 资源登记的是源包内路径
  （`OEBPS/images/cover.png`），生成的 EPUB 会引用不存在的图片（`resource_closure=false`）。
  改为复用导入侧的 `read_resource_bytes`（同样做越界与加密检查）。
- 未验证（BLOCKED）：**EPUBCheck 标准检查未运行**（本机无 jar、未联网安装）；真实阅读器试读属 T15B。

## 未完成与已知问题

1. **Live 仍未打通**：T07/T09～T15A 的 Live 都是 BLOCKED；T16 效果评测未开始。
2. **EPUBCheck 未安装**：需要 `tools/epubcheck/epubcheck.jar` + JRE；装了之后重跑 CLI 即可把
   `NOT_RUN` 升级为正式标准检查（样例 README 写了命令）。
3. **导出同步执行**：`POST /api/books/{id}/exports` 在请求内完成（本地 IO，无网络）；大书可能较慢，
   若需要进度可改为后台任务（EXPORT Job 已经落库，具备改造基础）。
4. **没有单独的取消接口**：导出不可中断；T15B/T18 若需要可加。
5. **HTML 图片全量内联**：大图多时会显著增大文件；需要时可改为「可选外置资源目录」。
6. **`export_artifacts` 没有清理策略**：产物只增不减；T18 可加保留期与手动清理。
7. 其余既有事项：`uv run` 在受限沙箱失败（回退 venv）；`npm --prefix frontend install/ci` 需在包目录内执行；
   `alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`；`apply_patch` 失效时用 `.tools/newfile.ps1` / `.tools/append.ps1`；
   **编辑多行文本前先统一换行符**；新迁移用 `python -m alembic -c backend/alembic.ini revision --autogenerate`
   生成后要手工改成 `000N_` 编号并补 `import ndr.storage.types`；
   E2E 数据目录在同一次运行里共享（新用例要自带夹具/独立 profile）。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**`、`frontend/e2e/fixtures/**` 的原始字节；已发布迁移 `0001`～`0005`；
  **`user_locked` 标注与用户密钥**；`corrections` / `annotation_history` / `identity_revisions` / `inference_runs` 只追加；
  `export_snapshots` 不可变，`export_artifacts` 只追加（同指纹才复用）。
- 当前版本号（进入缓存键/依赖哈希）：API 契约 `1`；数据库 `0005`；输出契约 `1.0`；
  提示词 `labeling-1`/`connection-1`；上下文 `context-1`；场景状态 `scene-state-1`；接受策略 `acceptance-1`；
  引擎 `attribution-engine-1`；调度器 `scheduler-1`；缓存 `cache-1`；扫描器 `quote-scan-1`；
  更正服务 `correction-1`、恢复服务 `recovery-1`、导出快照 `export-snapshot-1`、导出器 `exporter-1`
  （内部常量，不进入模型缓存键）。
- 提交习惯：每完成一部分功能即用 git 提交。