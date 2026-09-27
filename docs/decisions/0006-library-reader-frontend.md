# 0006 书架/阅读器的前端结构与阅读体验选择

日期：2026-09-28 · 状态：已采纳（T04）

## 问题

T04 第一次把统一文档契约接到界面上，需要确定：前端类型怎么来、路由与状态归属、
导入失败（编码选错/格式不支持）怎么呈现、阅读位置怎么存、以及“预留给标注层”的接口长什么样，
才能避免后面 T05/T13/T15 反复改地基。

## 选择

1. **前端类型只来自生成的 OpenAPI**：`frontend/src/api/types.ts` 全部是
   `components['schemas'][...]` 的别名（枚举也是生成的联合类型），由
   `npm --prefix frontend run generate:api` 更新；不手写第二份枚举。
2. **路由与状态**：`/` → `/library`，`/library`（书架 + 导入），`/books/:bookId/read`（阅读器）。
   服务器状态全部走 TanStack Query（queryKeys 含 book_id/chapter/cursor）；
   密钥类数据不进入任何持久化状态（T06 才会引入）。
3. **导入体验**：拖放/选择文件 + 编码下拉（默认自动检测）+ 可选书名。解析失败时展示
   后端返回的**编码候选**（可用/不可用 + 可读性）与**标注为有损的预演**，并提供“用该编码重试”；
   格式不支持、超限、EPUB 结构错误都显示可读原因。绝不因为失败而清空用户已选文件。
4. **阅读位置**：书签 = `(book_version_id, read_position_cp, reading_mode)`，存进 `books`
   （迁移 0004 增加 `read_position_version_id` 与 `reading_mode`）。切换章节时保存章节起点；
   滚动时取“视口内第一个节点”的 `data-start-cp` 保存（纯函数 `findCurrentStartCp` 可单测）。
   写入携带 `expected_version`；遇 409 先刷新书籍再按最新版本重试一次，**绝不覆盖较新的写入**。
5. **标注层接口（预留，不伪造结果）**：`DocumentRenderer` 给每个节点输出
   `data-node-id` / `data-node-type` / `data-start-cp` / `data-end-cp`，并支持 `onNodeClick`；
   `AnnotationLayer` 目前只渲染一个计数为 0 的容器。没有标注数据时界面**不会**出现颜色、编号或人物名，
   组件测试断言不产生任何 speaker/quote 类名。T05 起按 `start_cp/end_cp` 在其中分片着色。
6. **受限内容渲染**：ruby 用真实 `<ruby>/<rp>/<rt>` 渲染（基底在正文、注音在 rt）；
   图片只通过受控资源端点 `GET /api/books/{id}/resources/{resource_id}` 加载，使用节点 payload 的 alt；
   `separator` 渲染成 `<hr>`；不执行任何原书脚本、不加载外链。
7. **E2E 隔离**：每次运行使用独立数据目录（`frontend/.e2e/data-<runId>`，global-setup 尽力清理旧目录），
   后端以 `NDR_AUTO_MIGRATE=1` 启动在测试端口；样例文件由
   `backend/scripts/make_e2e_fixtures.py` 生成（含真实 1×1 PNG 与 GB18030 文本）并保持原始字节
   （`.gitattributes` 标记 `frontend/e2e/fixtures/** -text`）。

## 被放弃的方案

- **手写前端类型**：会与后端枚举漂移；生成类型已经包含所有枚举与 payload 形状。
- **把阅读位置放在 localStorage**：换书/换浏览器就丢，且与后端版本冲突检测脱节。
- **用 IntersectionObserver 精确追踪阅读位置**：jsdom 里难以测试、边界行为复杂；
  先用“视口内第一个节点”的纯函数实现，后续需要更高精度再替换。
- **失败时自动用候选编码重试**：静默换编码可能导入错误文本；必须让用户看到候选与预演后确认。
- **E2E 复用同一个数据目录**：上一次运行残留的 SQLite 句柄会导致清理失败，
  且书籍累积会污染断言（本轮就踩到过）。改为每次运行独立目录。

## 验证

- `npm --prefix frontend run test -- --run` → 13 passed（App 健康状态与默认路由、LibraryPage 导入成功/
  编码候选与预演/一键换编码重试/格式错误、DocumentRenderer 标题段落分隔符与定位属性/ruby/图片/无着色、
  ReaderPage 按书签打开章节/切换章节保存位置/`findCurrentStartCp`）。
- `npm --prefix frontend run test:e2e` → 6 passed（真实浏览器 + 真实后端、隔离数据目录）：
  UTF-8 TXT 导入→阅读→切章→刷新仍在同一章；EPUB 按 spine 打开、`<rt>` 注音可见、
  插图 `naturalWidth > 0`（确实通过资源端点解码成功）；GB18030 文件声明为 UTF-8 → 候选与预演 →
  一键换编码成功；`.md` 文件提示只支持 .txt/.epub。
- `pytest backend/tests` → 114 passed（新增 `test_reading_progress.py` 5 项）；`ruff` 全绿；
  `scripts/verify.ps1` 退出码 0。

## 迁移与回滚

新增迁移 `0004`（`books.read_position_version_id`、`books.reading_mode`，后者带
`server_default='initial'` 以便已有数据的库安全升级）。前端为新增目录，回滚即删除对应文件；
数据库回滚用 `alembic downgrade 0003`，但会丢失书签版本与阅读模式。