# 开发交接

更新时间：2026-09-28
当前任务：T04 书架、导入与无模型阅读器（已完成 implementation / offline_verification）
最近完成任务：T04（此前 T00 骨架、T01 模型与迁移、T02 TXT 导入、T03 EPUB 与资源）
下一任务与理由：T05 候选引语、Gap 与标注样例工具。统一文档契约、目录/正文 API、书架与阅读器都已就绪，
且阅读器节点已带 `data-node-id/data-start-cp/data-end-cp` 定位属性，T05 可以在其上建立引语候选与 Gap；
这是第 8 节顺序中的下一个任务。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`：fastapi 0.141.1、
  pydantic 2.13.5、SQLAlchemy 2.1.1、Alembic 1.20.0、python-multipart 0.0.32、pytest 9.1.1、ruff 0.16.9）；
  Node.js 22.14.0 / npm 10.9.2；React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、
  前端 `http://127.0.0.1:5173`（代理 `/api`）；停止用 `-Stop`。
- 数据目录、迁移版本与服务状态：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0004`（= head）。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`npm --prefix frontend run test:e2e`。

## 本次改动

- 后端：`books` 增加 `read_position_version_id` 与 `reading_mode`（迁移 `0004`，后者带 server_default
  以便旧库安全升级）；新增 `PUT /api/books/{id}/reading-progress`（保存书签，不调用模型，
  带 `expected_version` 乐观并发 → 409）；`BookOut` 返回 `reading_mode`；EPUB 图片 payload 增加 `alt`。
- 前端 API 层：`src/api/types.ts`（全部来自生成的 OpenAPI 类型）、`src/api/books.ts`
  （查询键、书籍/目录/正文/导入/书签/任务封装、`resourceUrl`）、`client.ts` 增加 `apiData`/`apiUpload`。
- 前端组件：`DocumentRenderer`（按节点渲染标题/段落/分隔符/图片、ruby 用 `<ruby>/<rt>`、
  输出 `data-node-id/data-node-type/data-start-cp/data-end-cp` 与 `onNodeClick`）、
  `AnnotationLayer`（占位，渲染 0 条标注）、`ChapterNavigation`、`BookCard`、`ImportDropzone`、`JobPanel`。
- 前端页面：`LibraryPage`（拖放/编码选择/导入结果/编码候选+有损预演+一键重试/书架列表）、
  `ReaderPage`（书签定位章节、章节切换、滚动保存位置、409 刷新重试、按 cursor 加载更多）。
- 路由与外壳：`App`（头部 + 健康状态 + 路由，`/` → `/library`）、`main.tsx` 挂 `BrowserRouter`。
- 样式：`global.css` 增加书架/阅读器布局与 360px～1280px 响应式规则。
- E2E：`e2e/import-reader.spec.ts`（4 个用例）、`e2e/global-setup.ts`（每次运行独立数据目录）、
  `e2e/fixtures/*`（由 `backend/scripts/make_e2e_fixtures.py` 生成，`.gitattributes` 固定字节）。
- 契约产物：`docs/openapi.json` 与 `frontend/src/api/schema.d.ts` 重新生成（9 条路径）。
- 文档：新增决策 0006；CONTRACTS 增加第 12 节；README 增加“界面（T04）”与 E2E 样例说明。

## 验证证据

- `pytest backend/tests` → **114 passed**（新增 `test_reading_progress.py` 5 项）；
  `ruff check backend/src backend/tests backend/scripts` → All checks passed。
- `npm --prefix frontend run test -- --run` → **13 passed**（App 健康状态与默认路由、LibraryPage 四种场景、
  DocumentRenderer 四种场景、ReaderPage 三种场景）。
- `npm --prefix frontend run test:e2e` → **6 passed**（真实 Chromium + 真实后端 + 每轮独立数据目录）：
  ① UTF-8 TXT 导入→阅读→切到第二章→刷新后仍停在第二章（验证书签写入）；
  ② EPUB 按 spine 打开、`<rt>` 注音可见、插图经资源端点加载且 `naturalWidth > 0`；
  ③ GB18030 文件声明为 UTF-8 → 候选与有损预演 → 一键换编码后导入成功；
  ④ `.md` 文件提示“只支持 .txt 与 .epub 文件”；⑤⑥ 原有健康检查用例。
- `scripts/verify.ps1` → 6/6 通过、退出码 0。
- 过程中修复的问题：
  ① 迁移新增 NOT NULL 列缺 server_default 会让旧库升级失败（补 `server_default='initial'`）；
  ② T01 的“旧库升级”用例用当前 ORM 写入 0001 结构导致失败（改为原生 SQL 明确旧列）；
  ③ E2E 复用数据目录被残留 SQLite 句柄锁住（改为每次运行独立目录 + 后台重试清理）；
  ④ E2E 中 `h1` 选择器与书籍卡片定位不精确（改为 `.ndr-chapter-heading` 与按书名定位卡片）；
  ⑤ 有损预演在折叠的 `<details>` 里不可见（默认展开）。
- 未执行/未验证：真实模型联调与真实作品效果评测（无凭据、无真实小说）；着色/编号、场景、待确认、
  导出均未开始。**没有任何识别结果被伪造**。

## 未完成与已知问题

1. **没有识别功能**：T05 起才有引语候选/Gap；当前阅读器不显示任何颜色或编号。
2. **阅读位置精度**：滚动保存取“视口内第一个节点”，尚未用 IntersectionObserver 做逐段精确追踪。
3. **内容分页**：`/content` 单页最多 2000 节点，超出需要用 `next_cursor` 加载更多（阅读器已有按钮）。
4. **导入仍在请求内同步执行**（返回 202 + job_id，任务多已终态）；T10 改为后台调度。
5. **EPUB 容错有限**：非法 XHTML 直接拒绝；脚注只保留链接文字（完整处理属 T15A）。
6. **枚举无数据库级 CHECK**（决策 0003）；`books.active_version_id`/`read_position_version_id` 无外键；
   EPUB 的 `encoding` 用 `"xml"` 哨兵（决策 0005）。
7. **受限沙箱中 `uv run` 失败**，脚本自动回退 `backend\.venv`；`npm --prefix frontend install/ci` 须在 frontend 内执行；
   `alembic.ini` 必须保持 ASCII；脚本已设置 `PYTHONUTF8=1`。
8. 本会话中 `apply_patch` 与沙箱内命令执行器失效，文件改用 `.tools/newfile.ps1`（无 BOM UTF-8，
  写入后回读校验）创建；该工具已被 `.gitignore` 忽略，不影响交付物。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/minimal-txt-001/text.txt` 与 `frontend/e2e/fixtures/**` 的原始字节；
  已发布迁移 `0001`～`0004` 不修改，结构变化一律新增迁移。
- 当前接口/schema/数据版本：API 契约版本 `1`；数据库 revision `0004`；金标准 schema `1.0`；
  `PARSER_VERSION=txt-1`/`epub-1`，`NORMALIZATION_VERSION=canonical-lf-1`/`canonical-epub-blocks-1`。
- 修改公共契约时同步 `docs/CONTRACTS.md`、`docs/openapi.json`、`frontend/src/api/schema.d.ts`、调用方与测试。
- 提交习惯：每完成一部分功能即用 git 提交。