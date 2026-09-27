# 开发交接

更新时间：2026-09-28
当前任务：T00 工程骨架与执行账本（已完成 implementation / offline_verification）
最近完成任务：T00
下一任务与理由：T01 领域模型、数据库迁移与公共契约。T00 已建立 pyproject、应用工厂、
健康检查、前端骨架、脚本与文档，T01 的前置条件已满足，且是第 8 节顺序中的下一个任务。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 /
  npm 10.9.2（`frontend/package-lock.json`）；Playwright 1.63.0 + Chromium headless shell。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765/api/health`、
  前端 `http://127.0.0.1:5173`；停止：`pwsh -File scripts/dev.ps1 -Stop`（脚本只结束自己启动的进程，
  按进程树 `taskkill /T /F` 回收 npm→node、uv→python 子进程）。
- 数据目录、迁移版本与服务状态：默认 `data/`（可用 `NDR_DATA_DIR` 覆盖），运行日志在
  `data/run/`；**尚无迁移**（T01 建立），健康检查如实返回 `database.state = NOT_INITIALIZED`。
- 验证命令：`pwsh -File scripts/verify.ps1`（ruff、pytest、OpenAPI 一致性、前端 typecheck/test/build）。

## 本次改动

- 后端骨架：`backend/pyproject.toml`（uv、依赖组、Ruff、pytest 配置）、`backend/src/ndr/app.py`
  （应用工厂、生命周期、CORS 仅本地来源）、`config.py`（`NDR_*` 配置、绝对 data 目录、数据库路径）、
  `api/health.py`、`__main__.py`（`uv run --project backend python -m ndr`）。
- 测试：`backend/tests/unit/test_config.py`、`test_health_payload.py`、
  `backend/tests/integration/test_health_http.py`（共 9 项，含真实 ASGI 连通）。
- 前端骨架：`frontend/package.json`（React 18 + Vite 5 + TanStack Query + Vitest + Playwright）、
  `vite.config.ts`（127.0.0.1:5173、`/api` 代理 8765、可用 `NDR_DEV_API_TARGET` 覆盖）、
  `src/App.tsx`（显示后端返回的真实健康状态与后续任务占位说明，不伪造识别结果）、
  `src/api/client.ts`（错误体解析为 `ApiError`）、`tests/App.test.tsx`。
- E2E：`frontend/playwright.config.ts` + `frontend/e2e/smoke.spec.ts`，使用独立数据目录
  `frontend/.e2e/data` 与独立端口 8795/5273，不接触用户书库。
- 运维与文档：`scripts/dev.ps1`、`scripts/verify.ps1`、`backend/scripts/export_openapi.py`、
  `docs/CONTRACTS.md`、`docs/IMPLEMENTATION_STATUS.md`、`docs/decisions/0001`、`docs/decisions/0002`、
  `README.md`、`.gitignore`、`.gitattributes`、`.npmrc`。
- 评测资产：`evaluation/schemas/gold-standard.schema.json`、`evaluation/annotation-guide.md`、
  `evaluation/examples/minimal-txt-001/{text.txt,gold.json}`（原创样例）。
- 契约变化：新增公共契约摘要与坐标/ID 决策；**数据库结构未变**（T01 落地）。

## 验证证据

- `uv run --project backend ruff check backend/src backend/tests backend/scripts` → All checks passed
  （沙箱内用等价的 `backend\.venv\Scripts\python.exe -m ruff`、`-m pytest` 执行，见 README 偏差 2）。
- `pytest backend/tests` → 9 passed（1 条 Starlette TestClient 弃用警告来自第三方）。
- `npm --prefix frontend run typecheck` → 通过；`npm --prefix frontend run test -- --run` → 2 passed；
  `npm --prefix frontend run build` → 构建成功（dist 产物未提交）。
- `playwright test`（真实 Chromium + 真实前后端，独立端口/数据目录）→ 2 passed，日志
  `data/run/e2e-t00.log`；断言页面显示后端返回的 `ok` 与 `NOT_INITIALIZED`，并断言 `/api/health`
  不含模型调用。
- `scripts/dev.ps1` 真实启动：`GET http://127.0.0.1:8765/api/health` 与经 Vite 代理的
  `GET http://127.0.0.1:5173/api/health` 均 200；`scripts/dev.ps1 -Stop` 后 8765/5173 无监听，
  `data/run/dev-pids.json` 被清理。
- 金标准样例：`text.txt` 长度 97 码点、sha256 与 `gold.json` 一致，5 条 quote 的 `[start_cp,end_cp)`
  切片都等于对应 `「…」` 文本，分组属于同一场景，gap 引用存在（临时校验脚本全 PASS）。
- 使用 FakeProvider 还是真实服务：**两者均未涉及模型**。本次没有任何模型调用，真实 token/费用为 0
  （无凭据、无预算、无真实模型联调；T06/T07 才引入适配器与 FakeProvider）。
- 未执行/未验证：真实模型联调、真实作品效果评测、EPUB/TXT 导入、导出、EPUBCheck 与真实阅读器试读
  —— 这些任务尚未开始。

## 未完成与已知问题

1. **受限沙箱中 `uv run` 失败**：uv 会在缓存目录写入并打开 `.git` 标记文件，受限环境拒绝访问
   （`failed to open file ...sdists-v9\.git: 拒绝访问`）。缓解：缓存放在仓库内 `.uv-cache/`；
   `scripts/dev.ps1`、`scripts/verify.ps1` 会在 `uv run` 不可用时回退到 `backend\.venv`。
   在普通（非受限）终端中 `uv run` 正常，请优先按 DEVELOPMENT.md 2.3 的命令执行。
2. **`npm --prefix frontend install/ci` 不可用**：仓库根目录没有 `package.json`，npm 10.9.2 会把安装
   目标解析到根目录并报 ENOENT。安装类命令须在 `frontend` 目录内执行（`Push-Location frontend`）；
   `npm --prefix frontend run <script>` 正常。
3. **Python 版本**：本机只有 3.11.4，`requires-python = ">=3.11"`，见决策 0001；未在 3.12 上验证。
4. **T01 起的所有功能均未开始**：无数据库、无迁移、无导入、无模型配置、无识别、无导出。
   进度账本中这些任务保持 NOT_STARTED，不得视为已完成。
5. **Playwright 浏览器**为本地缓存（`%LOCALAPPDATA%\ms-playwright`），换机器需重新
   `playwright install chromium`；E2E 在缺失浏览器时不会静默通过（会直接失败）。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未被改动，仍是唯一需求来源；不要为了让检查通过
  而删减 TXT/EPUB、API 配置、真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**` 是原创样例，可增补但不要改写已冻结的 `text.txt` 字节
  （`gold.json` 的 sha256 与码点偏移依赖它）。
- 当前接口/schema/数据版本：API 契约版本 `1`（`backend/src/ndr/__init__.py: API_VERSION`），
  OpenAPI 由 `backend/scripts/export_openapi.py` 生成到 `docs/openapi.json`，前端类型由
  `npm --prefix frontend run generate:api` 生成；金标准 schema 版本 `1.0`；尚无数据库 schema 版本。
- 新增/修改公共契约时：同步 `docs/CONTRACTS.md`、OpenAPI、生成的前端类型、调用方与测试，
  并按需要新增 `docs/decisions/NNNN-*.md`。
- 提交习惯：按用户要求，每完成一部分功能即用 git 提交（T00 的提交见仓库历史）。