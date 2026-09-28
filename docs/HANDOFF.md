# 开发交接

更新时间：2026-09-28
当前任务：**无**——T00–T19 计划内任务全部完成（`implementation` / `offline_verification` 通过；
`live_verification` / `quality_evaluation` = BLOCKED，缺外部条件）
最近完成任务：T19 启动交付、操作文档与最终交接（此前 T00～T18：骨架→迁移→TXT→EPUB→阅读器→候选与金标准→
配置页→适配器→上下文预算→场景引擎→任务缓存用量→标注投影与预览→人工更正与撤销→确认队列与抽屉→
暂停预算与故障恢复→初读身份与码点定位→导出后端与标准校验→导出界面与下载闭环→评测工具→
上下文压缩/局部复核/成本路由→完整联调与体验检查）
下一任务与理由：**无计划内任务**。若要继续，只有「需要外部条件」的验证项：
①真实提供方受预算端到端试用（需凭据）；②真实作品效果评测 B1–B4（需人工确认样本）；
③EPUBCheck 标准检查（需 `tools/epubcheck/epubcheck.jar`）；④≥2 款独立 EPUB 阅读器试读（需本机安装阅读器）。
清单与复现步骤见 `docs/verification-report.md` 与 README「未完成项与复现步骤」。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 开发启动：`pwsh -File scripts/dev.ps1`（后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`；`-Stop` 结束）。
- 生产/自用启动（同源单端口）：`pwsh -File scripts/serve.ps1`（迁移 → `npm run build` → 单端口 8765
  同时提供 API 与页面；`-SkipBuild` / `-Port` / `-DataDir` / `-AllowFakeProvider`（仅离线演示）/ `-Stop`）。
- 示例配置：`Copy-Item .env.example .env`（可选，全部有默认值；**不含密钥**，密钥只在界面填写）。
- 数据目录与迁移：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0005`（= head）；
  迁移显式执行 `alembic upgrade head`，`NDR_AUTO_MIGRATE=1` 才在启动时自动迁移。
- 评测命令：`python -m ndr.evaluation validate|run|loss …`（清单/配置/报告/消融说明见 `evaluation/`）。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：
  `cd frontend; $env:NDR_E2E_BACKEND_CMD='..\backend\.venv\Scripts\python.exe -m ndr'; npx playwright test`。
- 发布前四类结果（功能/稳定性/live/质量）、报告索引、已修问题、残余阻塞、从零复现步骤：
  `docs/verification-report.md`；验证口径见决策 0022/0023。
## 本次改动（T19）

- 后端：新增 `NDR_STATIC_DIR` 与 SPA 兜底路由（生产同源启动）——`/api/**` 始终 JSON 契约、
  越界路径不读构建目录外文件、兜底路由不进 OpenAPI。
- 脚本：新增 `scripts/serve.ps1`（迁移 → 构建 → 单端口启动，含 `-Stop`）；`scripts/dev.ps1`
  在后端回退到 `backend\.venv` 时跳过 `uv sync`（受限环境也能启动）。
- 交付文档：`README.md` 更新最终状态，并新增「依赖检查」「初始化与迁移」「生产（同源单端口）启动」
  「示例配置（不含密钥）」「未完成项与复现步骤」，在「数据库与迁移」补充「数据与版本升级」；
  新增 `.env.example`（全部 `NDR_*` 开关，不含密钥）。
- 测试：`backend/tests/integration/test_static_site.py`（同源服务 2 项）、
  `backend/tests/integration/test_upgrade_preserves_data.py`（升级保留书籍/人工确认/队列 1 项）。
- 文档：`docs/verification-report.md` 增加 T19 章节（同源启动与从零走查实测、升级测试、门禁）；
  决策 `0023`；CONTRACTS 第 30 节；`IMPLEMENTATION_STATUS.md` 记录 T19 与总体状态；本文件。

## 验证证据

- `scripts/verify.ps1` 退出码 0：`ruff` 全绿；`pytest backend/tests` → **376 passed**；
  OpenAPI 与 `docs/openapi.json` 一致；前端 typecheck；前端 API 类型与 OpenAPI 一致（`npm run check:api`）；
  前端单测 **72 passed**；前端 build 通过。
- 全量 E2E：`npx playwright test` → **33 passed**（真实后端 + 真实 Chromium + 隔离数据目录）。
- 从零交付走查（实测，详见验证报告第 10 节）：全新数据目录 → `scripts/serve.ps1` 迁移 `0001`～`0005` →
  `GET /` 200、`GET /library` 200（深链接）、`GET /api/health` 200（`READY`/`0005`）、`GET /api/unknown` 404；
  导入原创 TXT（97 码点）→ 任务 `COMPLETED`（`calls=1`）→ 人工确认（标注版本 1→2）→ 投影 5 项/图例 S1 →
  导出 HTML 2494 B（无 `http`/无 `<script>`/含 `〔S1〕`）与 EPUB 2780 B（`PK`+`mimetype`），下载件离线可读。
- 升级：重跑/升级迁移后书籍、`user_locked` 人工确认、标注历史与待确认队列原样保留，接口仍可用。
- **BLOCKED（有证据，非本机可解）**：真实提供方试用（环境无密钥、网络 6 秒超时）、
  真实作品效果评测（B1–B4 全 `NOT_RUN`，`quality_evidence=false`、`targets_met=null`）、
  EPUBCheck（无 jar）、独立阅读器试读（本机无阅读器）。

## 未完成与已知问题

1. **live = BLOCKED**：无真实提供方凭据/预算；`--allow-live` 路径未在真实环境跑过。
2. **quality = BLOCKED**：无人工确认样本；没有任何准确率数字，也没有宣布 97%/70% 达标。
3. **EPUBCheck 未运行**：无 jar 且网络受限；导出报告里标准检查状态如实 `NOT_RUN`。
4. **≥2 款独立 EPUB 阅读器试读未做**：本机未安装任何独立阅读器。
5. **真实提供方的限流/超时行为**只用 MockTransport/FakeProvider 验证过。
6. **T17 优化仍未验证**：默认保持 `context-1` + 复核/路由关闭；`strong_profile_id` 只能由任务范围传入。
7. 其他既有事项：`uv run` 在部分受限沙箱不可用（脚本会自动回退 `backend\.venv`）；
   `npm --prefix frontend install/ci` 需在包目录内执行；`alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`；
   `apply_patch` 失效时用 `.tools/newfile.ps1` / `.tools/append.ps1`；**编辑多行文本前先统一换行符**；
   E2E 数据目录共享（新夹具内容必须与既有夹具不同，否则被 sha256 去重）；测试文件名不要跨目录重名。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**`、`frontend/e2e/fixtures/**` 的原始字节；已发布迁移 `0001`～`0005`；
  **`user_locked` 标注与用户密钥**；审计类表只追加；**已有评测报告不要改写**（要保留历史结论）。
- 当前版本号（进入缓存键/依赖哈希）：API 契约 `1`；数据库 `0005`；输出契约 `1.0`；
  提示词 `labeling-2`/`connection-2`；上下文 `context-1`（默认）/`context-2`（压缩）；复核策略 `recheck-1`；
  路由策略 `routing-1`；场景状态 `scene-state-1`；接受策略 `acceptance-1`；
  引擎 `attribution-engine-1`；调度器 `scheduler-1`；缓存 `cache-1`；扫描器 `quote-scan-1`；
  更正服务 `correction-1`、恢复服务 `recovery-1`、导出快照 `export-snapshot-1`、导出器 `exporter-1`、
  评测 `evaluation-1`、B0 基线 `b0-rule-1`、证据账 `context-loss-1`。
- 提交习惯：每完成一部分功能即用 git 提交。