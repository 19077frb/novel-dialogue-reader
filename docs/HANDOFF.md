# 开发交接

更新时间：2026-09-28
当前任务：T02 TXT 导入、编码与统一文档树（已完成 implementation / offline_verification）
最近完成任务：T02（此前 T00 工程骨架、T01 领域模型与迁移）
下一任务与理由：T03 EPUB 导入与资源映射。T02 已产出统一文档树、source_map、章节与内容读取 API，
EPUB 只要转换到同一契约即可复用；且是第 8 节顺序中的下一个任务。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`：fastapi 0.141.1、
  pydantic 2.13.5、SQLAlchemy 2.1.1、Alembic 1.20.0、python-multipart 0.0.32、pytest 9.1.1、ruff 0.16.9）；
  Node.js 22.14.0 / npm 10.9.2；Playwright 1.63.0 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → `uv sync` → `alembic upgrade head` →
  后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`（代理 `/api`）；停止用 `-Stop`。
- 数据目录、迁移版本与服务状态：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0003`。
  正文落盘：`data/books/<book_id>/source.<ext>` 与 `data/books/<book_id>/versions/<vid>/canonical.txt`。
- 验证命令：`pwsh -File scripts/verify.ps1`。

## 本次改动

- `backend/src/ndr/ingest/encoding.py`：BOM/候选编码检测、严格解码、可读性打分、有损预演与
  `DecodeFailure.details`（requested_encoding / candidates / preview / preview_is_lossy）。
- `backend/src/ndr/ingest/txt.py`：TXT → canonical 全文、章节、节点、source_map；
  `PARSER_VERSION="txt-1"`、`NORMALIZATION_VERSION="canonical-lf-1"`。
- `backend/src/ndr/ingest/service.py`：导入写入路径（书籍→版本→章节→节点→text_mappings→IMPORT 任务）、
  复用规则（source_sha256 / canonical+parser+normalization）、失败任务留痕、事务失败时清理已写文件。
- `backend/src/ndr/ingest/query.py`：书籍列表/详情、目录、正文节点（章节或码点范围 + cursor 分页）、
  任务序列化。
- `backend/src/ndr/storage/paths.py`：数据目录路径解析与越界拒绝；`storage/models/mapping.py`：
  `text_mappings` 表；`book_versions.canonical_path`。
- 迁移 `0003_text_mappings.py`（autogenerate，含 UtcDateTime 导入）。
- `backend/src/ndr/api/books.py`、`api/jobs.py`：导入（multipart，415/413/422）、列表、详情、目录、
  内容与任务查询；`config.py` 增加 `max_import_bytes`；`app.py` 挂载新路由。
- `backend/src/ndr/domain/documents.py`：Book/BookVersion/Chapter/ContentNode/Content/ImportResult/Job schema。
- 契约产物：`docs/openapi.json`、`frontend/src/api/schema.d.ts` 重新生成（7 条路径）。
- 文档：新增决策 0004；CONTRACTS 增加第 10 节；README 增加“导入与阅读（T02）”。

## 验证证据

- `pytest backend/tests` → **76 passed**（T01 时 41；新增 24 单元 + 11 集成）。
  重点：`test_txt_ingest.py`（映射无缝覆盖 canonical、CRLF 标记 synthetic、GB18030 自动检测、
  错误编码候选与有损预演、emoji/扩展汉字码点、重复对白不同位置、标题判定、无标题单章、BOM、空文件）；
  `test_book_import.py`（导入→任务→目录→正文全流程、错误编码 422 且不建书、重复导入复用书与版本、
  换编码新版本、415/413、分页与范围校验、404 契约、落盘位置与源字节不变）。
- `ruff check backend/src backend/tests backend/scripts` → All checks passed。
- 真实联调（本机服务，**无任何模型调用**）：真正的 GB18030 样例经前端代理导入 →
  `encoding=gb18030`、`chapter_count=3`、`canonical_length_cp=143`；11 个节点与原文逐行一致；
  `𠮷`/`🐈` 完整保留；无 `?` 占位、无 U+FFFD；错误编码请求返回 422 且 `candidates` 指出 gb18030 可用，
  同时留下 FAILED 任务。
- 过程中发现：用 `[Text.Encoding]::GetEncoding(936)` 生成样例会丢失扩展汉字/emoji（GBK 非 GB18030），
  这是**样例生成**问题，应用侧行为正确；改用真正的 GB18030 字节后校验通过（记入决策 0004 验证一节）。
- `npm --prefix frontend run typecheck / test -- --run / build` 全部通过；OpenAPI 与生成类型同步。
- 未执行/未验证：真实模型联调与真实作品效果评测（无凭据、无真实小说）；EPUB、阅读界面、预览、
  确认、导出均未开始。

## 未完成与已知问题

1. **EPUB 未实现**（T03）：`.epub` 导入返回 415。
2. **正文展示只有 API**：前端页面（书架/阅读器）在 T04；当前 T00 首页只显示健康状态。
3. **导入是请求内同步执行**：`POST /api/books/import` 会等解析完成再返回（仍返回 202 + job_id），
   大文件会占用请求时间；T10 引入调度器后改为后台执行。
4. **content 的 horizon 未实现**：`/content` 目前按章节/码点范围返回，`visible_horizon` 相关的
   证据时点逻辑属于 T04/T15。
5. **枚举没有数据库级 CHECK**（决策 0003）；`books.active_version_id` 无外键（循环外键 + SQLite 限制）。
6. **受限沙箱中 `uv run` 失败**（无法打开 uv 缓存中的 `.git` 标记），脚本会自动回退 `backend\.venv`。
7. **`npm --prefix frontend install/ci` 需在 frontend 目录内执行**；`alembic.ini` 必须保持 ASCII；
   脚本已设置 `PYTHONUTF8=1`。
8. 本会话中 `apply_patch` 与沙箱内命令执行器失效，文件改用 `.tools/newfile.ps1`（无 BOM UTF-8，
  写入后回读校验）创建；该工具已被 `.gitignore` 忽略，不影响交付物。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/minimal-txt-001/text.txt` 的字节；已发布迁移 `0001`/`0002`/`0003`
  不修改，结构变化一律新增迁移。
- 当前接口/schema/数据版本：API 契约版本 `1`；数据库 revision `0003`；金标准 schema `1.0`；
  `PARSER_VERSION=txt-1`、`NORMALIZATION_VERSION=canonical-lf-1`（换版本会产生新书籍版本）。
- 修改公共契约时同步 `docs/CONTRACTS.md`、`docs/openapi.json`、`frontend/src/api/schema.d.ts`、调用方与测试。
- 提交习惯：每完成一部分功能即用 git 提交。