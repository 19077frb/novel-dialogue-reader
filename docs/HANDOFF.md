# 开发交接

更新时间：2026-09-28
当前任务：T01 领域模型、数据库迁移与公共契约（已完成 implementation / offline_verification）
最近完成任务：T01（上一项 T00 工程骨架）
下一任务与理由：T02 TXT 导入、编码与统一文档树。T01 已提供 books/book_versions/chapters/
content_nodes/resources 表、迁移、事务与错误契约，T02 的前置条件已满足，且是第 8 节顺序中的下一个任务。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`：fastapi 0.141.1、
  pydantic 2.13.5、SQLAlchemy 2.1.1、Alembic 1.20.0、pytest 9.1.1、ruff 0.16.9）；
  Node.js 22.14.0 / npm 10.9.2（`frontend/package-lock.json`）；Playwright 1.63.0 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 执行 `uv sync`、`alembic upgrade head`
  后启动后端 `http://127.0.0.1:8765/api/health` 与前端 `http://127.0.0.1:5173`；
  停止用 `pwsh -File scripts/dev.ps1 -Stop`（按进程树回收 npm→node、uv→python 子进程）。
- 数据目录、迁移版本与服务状态：数据在 `data/`（`NDR_DATA_DIR` 可覆盖），数据库 `data/ndr.sqlite3`
  当前版本 `0002`（= head）。健康检查真实返回
  `{"database":{"state":"READY","revision":"0002","head_revision":"0002"}}`。
- 验证命令：`pwsh -File scripts/verify.ps1`（ruff、pytest、OpenAPI 一致性、前端 typecheck/test/build）。

## 本次改动

- `backend/src/ndr/domain/`：`enums.py` 汇总 DEVELOPMENT.md 3.3 的全部枚举
  （Scene/Gap/Quote/Assignment/Basis/Annotation/Queue/Job/Run/Reading/Visibility/Export/ErrorCode/
  ContentNodeType/DatabaseState），`common.py` 定义 `data/request_id` 包、错误体与 cursor 分页 schema。
- `backend/src/ndr/storage/`：`models/` 21 张表（books、book_versions、chapters、content_nodes、
  resources、quotes、gaps、scenes、scene_memberships、speaker_groups、participants、annotations、
  annotation_history、identity_revisions、model_profiles、jobs、job_windows、inference_runs、
  result_cache、review_items、corrections）；`types.UtcDateTime`（写入 UTC、读出带时区）；
  `engine.py`（SQLite + foreign_keys/busy_timeout/WAL、迁移状态检测）；`transactions.py`
  （`transaction`、`check_version`、`apply_versioned_update`、`VersionConflict`）；
  `migrate.py`（`run_migrations(settings, revision=...)`，供脚本/测试/E2E 使用）。
- `backend/migrations/`：`0001` 核心表、`0002` 待确认与更正表，均由 autogenerate 从模型生成；
  `alembic.ini` 用 `%(here)s` 解析路径并保持纯 ASCII。
- `backend/src/ndr/api/`：`errors.py`（统一错误体、request_id 中间件、VersionConflict→409、
  校验错误→422、未知路由→契约错误体）、`pagination.py`（cursor/limit）、`openapi.py`
  （把 DataEnvelope/ErrorEnvelope/ErrorBody/CursorPage 注入 components）、`deps.py`（请求级会话）、
  `health.py`（报告真实迁移状态）。
- 契约产物：`docs/openapi.json` 与 `frontend/src/api/schema.d.ts` 重新生成。
- 脚本：`dev.ps1` 现在会执行迁移；两个脚本设置 `PYTHONUTF8=1`。
- E2E：`frontend/playwright.config.ts` 用隔离数据目录 + `NDR_AUTO_MIGRATE=1`，
  `smoke.spec.ts` 断言健康检查为 `READY` 且 `revision == head_revision`。
- 契约变化：数据库从“无 schema”变成 21 张表（见 docs/CONTRACTS.md 第 9 节）；
  `DatabaseState` 增加 `OUTDATED`；新增决策 0003。

## 验证证据

- `pytest backend/tests` → **41 passed**（1 条第三方 Starlette TestClient 弃用警告）。
  关键用例见 `backend/tests/integration/test_schema.py`、`backend/tests/unit/test_api_contract.py`、
  `backend/tests/unit/test_domain_schemas.py`。
- `ruff check backend/src backend/tests backend/scripts` → All checks passed。
- `alembic -c backend/alembic.ini upgrade head` 重复执行安全；测试内验证 0001→head 升级保留既有行。
- 真实运行：`dev.ps1` → `GET http://127.0.0.1:8765/api/health` 返回 READY/0002；
  `GET /api/nope` → 404 契约错误体且 `X-Request-ID` 与响应体 `request_id` 一致；
  经 Vite 代理的 `/api/health` 200；`-Stop` 后 8765/5173 无监听、无残留进程。
- `npm --prefix frontend run typecheck / test -- --run / build` 全部通过（Vitest 2 passed）。
- `npm --prefix frontend run test:e2e` → 2 passed（真实 Chromium + 真实后端 + 隔离数据目录，
  日志 `data/run/e2e-t01.log`）。
- 使用 FakeProvider 还是真实服务：**均未涉及模型**。本次没有任何模型调用，真实 token/费用为 0。
- 未执行/未验证：真实模型联调与真实作品效果评测（无凭据、无真实小说、T06/T07 才引入适配器）；
  TXT/EPUB 导入、阅读界面、预览、确认、导出均未开始。

## 未完成与已知问题

1. **没有业务端点**：除 `/api/health` 外，DEVELOPMENT.md 5.2 的端点都还没实现（T02 起逐步补齐）。
2. **枚举没有数据库级 CHECK**：以 VARCHAR 存契约字符串 + ORM 校验；原因与替代方案见决策 0003。
3. **`books.active_version_id` 无外键约束**（循环外键 + SQLite 限制），由服务层维护一致性。
4. **受限沙箱中 `uv run` 失败**（无法打开 uv 缓存里的 `.git` 标记）：脚本会自动回退到
   `backend\.venv`；普通终端请按 DEVELOPMENT.md 2.3 使用 `uv run`。
5. **`npm --prefix frontend install/ci` 不可用**：须在 `frontend` 目录内执行。
6. **平台编码**：Windows 默认 GBK，`alembic.ini` 必须保持 ASCII；脚本已设置 `PYTHONUTF8=1`。
7. **Playwright 浏览器**在本地缓存中，换机器需 `playwright install chromium`。
8. 本会话后期 `apply_patch` 与沙箱内命令执行器失效，文件改用 `.tools/newfile.ps1`
   （无 BOM UTF-8，写入后回读校验）创建；该临时工具已加入 `.gitignore`，不影响交付物。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动，仍是唯一需求来源；不得为通过检查而删减
  TXT/EPUB、API 配置、真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/minimal-txt-001/text.txt` 的字节（gold.json 的 sha256 与
  码点偏移依赖它）；已发布的迁移 `0001`/`0002` 不修改，结构变化一律新增迁移。
- 当前接口/schema/数据版本：API 契约版本 `1`（`backend/src/ndr/__init__.py`）；
  数据库 revision `0002`；金标准 schema 版本 `1.0`。
- 修改公共契约时：同步 `docs/CONTRACTS.md`、`docs/openapi.json`、`frontend/src/api/schema.d.ts`、
  调用方与测试，必要时新增 `docs/decisions/NNNN-*.md`。
- 提交习惯：按用户要求，每完成一部分功能即用 git 提交。