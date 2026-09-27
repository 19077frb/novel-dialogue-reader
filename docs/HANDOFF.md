# 开发交接

更新时间：2026-09-28
当前任务：T18 完整联调、体验与发布检查（implementation / offline_verification 完成；live = BLOCKED，quality = BLOCKED）
最近完成任务：T18（此前 T00～T17：骨架→迁移→TXT→EPUB→阅读器→候选与金标准→配置页→适配器→上下文预算→场景引擎→任务缓存用量→标注投影与预览→人工更正与撤销→确认队列与抽屉→暂停预算与故障恢复→初读身份与码点定位→导出后端与标准校验→导出界面与下载闭环→评测工具→上下文压缩/局部复核/成本路由）
下一任务与理由：T19 启动交付、操作文档与最终交接。前置 T18 已完成：要完善 README、依赖检查、初始化/迁移、
dev.ps1、生产构建后同源启动、数据与版本升级说明；建立不含密钥与私人小说的示例配置；整理所有未完成项与复现步骤，
并在新的数据目录按 README 从零初始化、导入原创样例走通试运行/确认/EPUB+HTML 导出，校验下载文件离线可读。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`。
- 评测命令：`python -m ndr.evaluation validate|run|loss …`（清单 `evaluation/manifests/`、
  配置 `evaluation/configs/`、报告 `evaluation/reports/`、消融说明 `evaluation/ablations.md`）。
- 数据目录与迁移：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0005`（= head）。
- 测试/E2E：`NDR_CREDENTIAL_BACKEND=session`；E2E 另开 `NDR_ALLOW_FAKE_PROVIDER=1` 与
  `NDR_FAKE_PROVIDER_LABELS=deterministic`。
- 验证命令：`pwsh -File scripts/verify.ps1`（ruff / pytest / OpenAPI 一致 / 前端 typecheck /
  **API 类型一致 `npm --prefix frontend run check:api`** / 前端单测 / build）；
  E2E：`cd frontend; $env:NDR_E2E_BACKEND_CMD='..\backend\.venv\Scripts\python.exe -m ndr'; npx playwright test`。
- 发布前四类结果与索引见 `docs/verification-report.md`；验证口径见决策 0022。

## 本次改动

- 可访问性（`frontend/src`）：导出对话框把 `role="dialog"` 移到对话框本体并加 `aria-modal`/`aria-labelledby`、
  打开时移入焦点、Escape 关闭、关闭后把焦点还给触发按钮；确认抽屉同样有 `role="dialog"` + 标题关联 + Escape + 焦点归还；
  任务状态与导出状态改为 `role="status"`/`aria-live="polite"`；导航改 `NavLink`（`aria-current="page"`）；
  新增「跳到主要内容」跳转链接与统一 `:focus-visible` 外框；新增 2 项单测。
- 边界加固：导出下载与资源读取遇到越界路径（含被手动篡改的库记录）改为契约错误（404/409），
  不再 500、也不回显磁盘路径；新增 `backend/tests/integration/test_boundaries.py` 3 项。
- 接口一致性：新增 `frontend/scripts/check-api-types.mjs` 与 `npm run check:api`
  （用 `docs/openapi.json` 重新生成 `src/api/schema.d.ts` 后逐字节比对，已验证篡改时会失败），并接入 `scripts/verify.ps1`。
- E2E：新增 `frontend/e2e/full-flow.spec.ts`（TXT/EPUB 全流程 → 双格式导出 → 下载件离线可读）
  与 `frontend/e2e/a11y-layout.spec.ts`（可访问性 + 360/1280 布局），新增夹具 `sample-t18-full.txt`。
- 文档：`docs/verification-report.md`（功能/稳定性/live/质量四类结果、报告索引、复现命令、已修问题、残余阻塞）、
  决策 0022、本文件与 `IMPLEMENTATION_STATUS.md`。

## 验证证据

- `scripts/verify.ps1` 退出码 0：`ruff` 全绿；`pytest backend/tests` → **373 passed**；
  OpenAPI 与 `docs/openapi.json` 一致；前端 typecheck；**前端 API 类型与 OpenAPI 一致**；
  前端单测 → **72 passed**；前端 build 通过。
- 全量 E2E：`npx playwright test` → **33 passed**（T15B 时 28）。新增用例覆盖：
  TXT 与 EPUB 两条完整链路（导入 → 估算/处理 → 导出 EPUB 与 HTML → 下载件无 http(s)/无 `<script>`/
  含〔S1〕编号/ruby 注音不进正文）、可访问性（语言 zh-CN、单一 `h1`、`main`、主导航 `aria-current`、
  所有可交互元素有可访问名称、图片有 alt、Tab 首个焦点是跳转链接且激活后进入 `main`）、
  对话框/抽屉键盘语义（`role`/`aria-modal`/标题关联/Escape/焦点归还）、360×740 与 1280×900 下五个页面无横向溢出。
- 边界：越界资源 id（`..%2F..%2F`、`%2E%2E`、`r0001%00.png`、Windows 绝对路径）→ 404 且不回显数据目录；
  被篡改的 `source_path` → 409；被篡改的产物 `relative_path` → 404；导出件与资源字节都不会被骗出来。
- 接口一致性：`check:api` 在人为篡改 `schema.d.ts` 时返回 1（真失败），恢复后返回 0。
- **BLOCKED（有证据）**：环境变量无任何提供方密钥；仓库无 `data/` 目录（没有保存的凭据）；
  `https://github.com` HEAD 请求 6 秒超时（无法下载 EPUBCheck/安装阅读器）；
  本机无 Calibre/Thorium/Adobe 阅读器；`tools/epubcheck` 无 jar。因此真实提供方试用与质量评测未做。

## 未完成与已知问题

1. **真实提供方试用未做（T18 live = BLOCKED）**：缺凭据/预算，网络受限；`NOT_RUN` 如实保留。
2. **质量评测未做（BLOCKED）**：B1–B4 全 `NOT_RUN`，`quality_evidence=false`，`targets_met=null`。
3. **EPUBCheck 未运行**：无 jar，网络受限；导出报告里标准检查状态为 `NOT_RUN`。
4. **≥2 款独立 EPUB 阅读器试读未做**：本机未安装任何独立阅读器，且不能联网安装。
5. **真实提供方的限流/超时行为**仍只在 MockTransport/FakeProvider 上验证。
6. **T17 优化仍未验证**：默认保持 `context-1` + 复核/路由关闭；`strong_profile_id` 只能由任务范围传入（无 UI）。
7. 其余既有事项：`uv run` 在受限沙箱失败（回退 venv）；`npm --prefix frontend install/ci` 需在包目录内执行；
   `alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`；`apply_patch` 失效时用 `.tools/newfile.ps1` / `.tools/append.ps1`；
   **编辑多行文本前先统一换行符**；E2E 数据目录共享（新夹具内容必须与既有夹具不同，否则被 sha256 去重）；
   测试文件名不要跨目录重名。

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