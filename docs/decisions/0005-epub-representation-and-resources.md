# 0005 EPUB 表示、ruby 与资源服务的选择

日期：2026-09-28 · 状态：已采纳（T03）

## 问题

T03 要把 EPUB 变成与 TXT 相同的统一文档契约，并处理若干会影响 T04/T05/T15A 的决定：
spine 与目录的关系、块之间的换行怎么表示、ruby 注音放哪里、图片资源怎么存与怎么服务、
以及“编码”这一列对 EPUB 意味着什么。

## 选择

1. **阅读顺序只由 spine 决定**。章节 = spine 中可读取的正文文档（按 spine 顺序编号），
   目录（nav.xhtml 的 `epub:type="toc"`，缺失时退回 NCX）只用来取标题，不改变顺序。
   `ParsedBook.toc` 保留原始目录项，`chapters[].source_href` 记录 spine 文档路径（F03）。
2. **canonical 文本 = 各块的文本用 `\n` 连接**，块间换行是导入时合成的，
   `text_mappings.synthetic=True`（DEVELOPMENT.md 4.1 要求不假装换行存在于原文件）。
   映射段覆盖整段 canonical 文本；`source_text_*` 是“该 XHTML 文档内纯文本”的偏移，
   `source_href` 指向 spine 文档。
3. **XHTML 只转成受限节点**：块级元素（p/div/h1-h6/li/blockquote/td…）成段落或标题，
   `hr` 成零长度 SEPARATOR 节点（不往正文插入原文没有的破折号），
   `img/image` 成零长度 IMAGE 节点；`script`/`style`/`object`/`iframe` 等一律跳过，
   既不进正文也不登记为资源。排版空白折叠为单个空格，段落首尾去空白。
4. **ruby 作为段落节点的受限注解保存**：基底文字留在正文，`rt` 注音与 `rp` 回退括号
   都不进入正文，注音写在段落 `tree_json.ruby = [{start_cp, end_cp, base, rt}]`。
   这样既不重复注音，也不产生与段落重叠的节点（渲染方读 payload 即可）。
5. **资源只登记、只从包内读取**：非正文 manifest 项（图片/CSS/字体）登记为
   `resources`（`media_type`/`relative_path`/`sha256`/`byte_size`），脚本类媒体类型不登记。
   `GET /api/books/{id}/resources/{resource_id}` 打开该书版本的源 EPUB 读取条目，
   条目路径再过一次越界校验；资源 ID 只在所属书籍版本内有意义（跨书 404）。
   外链（http/https）只告警不下载；spine 里的包外文档直接拒绝。
6. **`book_versions.encoding` 对 EPUB 记 `"xml"`**：正文编码由各 XHTML 文档自身的 XML 声明
   决定，不是单一文件级编码，因此用哨兵值而不是伪造一个具体编码名。
7. **安全上限可配置**：条目数、单条目解压大小、总解压大小、spine 条目数
   （`NDR_MAX_EPUB_*`），超限抛带 `reason_code` 的 `EpubError` → 422；
   ZIP 条目路径必须归一化后仍在包内，拒绝绝对路径、`..`、反斜杠、重复条目、符号链接与加密条目。

## 被放弃的方案

- **用 manifest 或文件名顺序排章节**：与阅读顺序无关，F03 专门验证这一点。
- **把 ruby 展开成独立 RUBY 节点覆盖基底文字**：会与段落节点重叠，渲染方容易把基底文字画两遍。
- **导入时把资源解压到磁盘**：包内容重复存储、要额外维护清理与越界防护；
  直接从源 EPUB 读取更省事，导出（T15A）同样可以按需读取。
- **容错解析非良构 XHTML**：会用正则/容错解析器去猜结构，容易把脚本或样式混进正文；
  当前遇到非法 XML 直接报 EPUB_INVALID_XHTML。
- **把 epub 的外部链接抓下来**：违反“不主动加载外部 URL”。

## 验证

- `pytest backend/tests/unit/test_epub_ingest.py` → 22 passed（spine 顺序、TOC/NCX 标题、ruby 基底与注音分离、
  图片零长度节点与资源登记、脚本/样式/外链排除、越界/符号链接/条目与大小上限、非 ZIP、空 spine、
  映射覆盖与 synthetic、排版空白折叠、外链 spine 拒绝）。
- `pytest backend/tests/integration/test_resources.py` → 10 passed（导入→按 spine 阅读、ruby/图片节点、
  资源端点字节与 sha 校验、未知资源 404、跨书资源不泄露、TXT 无资源、重复导入复用版本、
  越界 EPUB 422 + FAILED 任务、源文件与 canonical 落盘）。
- 真实联调（本机服务、经前端代理、**零模型调用**）：原创 EPUB 导入 → `chapters=1`、`nodes=7`、
  `resources=1`、`canonical_length_cp=52`；节点序列为 heading/paragraph(ruby)/image/paragraph…；
  正文含基底 `漢` 但不含注音 `かん`；图片节点为零长度且 `resource_id=r0001`；
  资源端点返回 200 `image/png`、字节与 sha256 与源文件一致；未知资源 404；IMPORT 任务 COMPLETED。

## 迁移与回滚

未新增迁移（沿用 `0003` 的 `text_mappings` 与 `resources` 表）。
回滚只需删除 T03 代码；已导入的 EPUB 数据若不再使用，应通过删除书籍记录与 `data/books/<id>` 清理，
不要用数据库降级来处理用户数据。