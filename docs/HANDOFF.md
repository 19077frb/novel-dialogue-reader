# 开发交接

更新时间：2026-09-28
当前任务：T03 EPUB 导入与资源映射（已完成 implementation / offline_verification）
最近完成任务：T03（此前 T00 骨架、T01 模型与迁移、T02 TXT 导入）
下一任务与理由：T04 书架、导入与无模型阅读器。T02/T03 已提供统一文档契约、目录与正文节点 API，
前端只需要消费这些接口；且 T04 是第 8 节顺序中的下一个任务。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`：fastapi 0.141.1、
  pydantic 2.13.5、SQLAlchemy 2.1.1、Alembic 1.20.0、python-multipart 0.0.32、pytest 9.1.1、ruff 0.16.9）；
  Node.js 22.14.0 / npm 10.9.2；Playwright 1.63.0 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、
  前端 `http://127.0.0.1:5173`（代理 `/api`）；停止用 `-Stop`。
- 数据目录、迁移版本与服务状态：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0003`（= head）。
  正文落盘：`data/books/<book_id>/source.{txt,epub}` 与 `versions/<vid>/canonical.txt`。
- 验证命令：`pwsh -File scripts/verify.ps1`。

## 本次改动

- `backend/src/ndr/ingest/document.py`：TXT/EPUB 共享的 `ParsedBook/Chapter/Node/Mapping/Resource`。
- `backend/src/ndr/ingest/epub.py`：安全 ZIP（条目数/单条目/总量上限、越界/重复/符号链接/加密拒绝）→
  container/OPF → spine 顺序读取 → TOC（nav/NCX）标题 → XHTML 受限节点树 → 资源登记 → canonical 与 source_map。
- `backend/src/ndr/ingest/resources.py`：受控资源读取（只读包内登记条目、脚本类不提供、跨书 404）。
- `backend/src/ndr/ingest/service.py`：泛化为 TXT/EPUB 共用写入路径，新增 `import_epub` 与资源落库；
  EPUB 的 `encoding` 记 `"xml"` 哨兵。
- `backend/src/ndr/api/books.py`：导入分派（.txt/.epub，415/413/422）、资源端点、
  `ImportResult` 增加 format/node_count/resource_count；`config.py` 增加 `max_epub_*` 上限。
- `backend/src/ndr/ingest/query.py`：内容节点返回 `payload`（标题层级/图片 resource_id/ruby 注音）；
  允许空范围（只有插图的 EPUB）。
- `backend/tests/fixtures/epub_factory.py`：原创 EPUB 夹具构造器（spine 与文件名顺序可不同、
  TOC/NCX、ruby、图片、外链、越界与符号链接条目）。
- 契约产物：`docs/openapi.json`、`frontend/src/api/schema.d.ts` 重新生成（8 条路径）。
- 文档：新增决策 0005；CONTRACTS 增加第 11 节；README 更新 T03 状态与导入说明。

## 验证证据

- `pytest backend/tests` → **109 passed**（T02 时 76；新增 22 单元 + 10 集成，另有 1 项非法 EPUB 用例）。
  重点：`test_epub_ingest.py`（spine 顺序、TOC/NCX 标题、ruby 基底/注音分离、图片零长度节点与资源登记、
  脚本样式外链排除、越界/符号链接/条目与大小上限、非 ZIP、空 spine、映射覆盖与 synthetic、空白折叠）；
  `test_resources.py`（导入→阅读、ruby/图片节点、资源端点字节与 sha、未知资源 404、跨书不泄露、
  TXT 无资源、重复导入复用版本、越界 EPUB 422 + FAILED 任务、源文件与 canonical 落盘）。
- `ruff check backend/src backend/tests backend/scripts` → All checks passed。
- 真实联调（本机服务、经前端代理、**零模型调用**）：原创 EPUB 导入 → `chapters=1`、`nodes=7`、
  `resources=1`、`canonical_length_cp=52`；节点序列 heading(0-6) → paragraph(ruby 7-15) → image(15-15) →
  paragraph(16-24) → …；正文含基底 `漢`、不含注音 `かん`；资源端点 200 `image/png`、26 字节、
  sha256 与源文件一致；未知资源 404；IMPORT 任务 COMPLETED（progress 含 chapters/nodes/resources）。
- 过程中修复的缺陷：ruby 注解在偏移 0 时被丢弃（真值判断 bug）、章节标题变量覆盖函数参数导致
  元数据书名错误、外链图片重复告警、只含插图的 EPUB（canonical 为空）在 `/content` 返回 422。
- 未执行/未验证：真实模型联调与真实作品效果评测（无凭据、无真实小说）；前端页面、预览、确认、
  导出均未开始。

## 未完成与已知问题

1. **前端只有健康页**：书架、导入进度、阅读器属于 T04；当前所有功能通过 API 验证。
2. **EPUB 容错有限**：非良构 XHTML 直接拒绝（`EPUB_INVALID_XHTML`），不做猜测式解析；
   复杂 CSS/版式只保留文本结构（受限节点），这是刻意的降级策略。
3. **脚注/尾注只保留链接文字**：被引用文档只有在 spine 中才会作为正文导入；完整脚注处理属于 T15A。
4. **导入仍在请求内同步执行**（返回 202 + job_id，任务已终态）；T10 改为后台调度。
5. **content 的 horizon 未实现**（T04/T15）；当前按章节/码点范围返回。
6. **枚举无数据库级 CHECK**（决策 0003）；`books.active_version_id` 无外键；EPUB 的 `encoding` 用 `"xml"` 哨兵（决策 0005）。
7. **受限沙箱中 `uv run` 失败**，脚本自动回退 `backend\.venv`；`npm --prefix frontend install/ci` 须在 frontend 内执行；
   `alembic.ini` 必须保持 ASCII；脚本已设置 `PYTHONUTF8=1`。
8. 本会话中 `apply_patch` 与沙箱内命令执行器失效，文件改用 `.tools/newfile.ps1`（无 BOM UTF-8，
  写入后回读校验）创建；该工具已被 `.gitignore` 忽略，不影响交付物。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/minimal-txt-001/text.txt` 的字节；已发布迁移 `0001`/`0002`/`0003`
  不修改，结构变化一律新增迁移。
- 当前接口/schema/数据版本：API 契约版本 `1`；数据库 revision `0003`；金标准 schema `1.0`；
  `PARSER_VERSION=txt-1` / `epub-1`，`NORMALIZATION_VERSION=canonical-lf-1` / `canonical-epub-blocks-1`
  （换版本会产生新书籍版本）。
- 修改公共契约时同步 `docs/CONTRACTS.md`、`docs/openapi.json`、`frontend/src/api/schema.d.ts`、调用方与测试。
- 提交习惯：每完成一部分功能即用 git 提交。