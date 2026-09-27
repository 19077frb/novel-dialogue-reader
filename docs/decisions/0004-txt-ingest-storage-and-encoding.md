# 0004 TXT 导入的落盘、编码与 source_map 选择

日期：2026-09-28 · 状态：已采纳（T02）

## 问题

T02 要把 TXT 变成“统一文档树”：需要决定正文存在哪里、source_map 怎么存、章节怎么判定、
导入是同步还是异步、编码选错时怎么办，以及重复导入如何复用版本。这些都会影响后续 T05/T08/T15A。

## 选择

1. **正文以文件形式存在数据目录内，数据库只存相对路径与元数据。**
   `data/books/<book_id>/source.<ext>` 保存不可变原始字节；`data/books/<book_id>/versions/<version_id>/canonical.txt`
   保存规范化全文；`book_versions.source_path`/`canonical_path` 存相对路径。
   所有路径都经 `storage.paths.resolve_within()` 校验，越界直接拒绝，API 不返回磁盘路径。
2. **source_map 用 `text_mappings` 表，每个 canonical 行一条记录。**
   `canonical_start_cp/canonical_end_cp` + `source_text_start_cp/source_text_end_cp` + `synthetic`；
   空白行也有映射（`node_id` 为 NULL），因此映射段**无缝覆盖整段 canonical 文本**，测试逐段校验
   相邻段首尾相接。这样任何偏移都能回溯到源文本，不需要重新按字符串搜索。
3. **章节只认可信标题**：短行（≤30 字符）且匹配 `第N章/节/回/卷/篇/部`、`序章/序言/楔子/前言/后记/尾声/终章/番外` 等；
   首个标题之前的正文进入 `title=None` 的序言章节；完全没有标题时按单章导入并写入 warning。
   标题行同时是一个 `heading` 节点，正文行是 `paragraph` 节点。
4. **编码策略**：自动检测按 `utf-8 → gb18030 → big5` 顺序做**严格解码** + 可读性打分（控制符/私用区/
   替换符比例 ≥ 99.9%）；用户显式指定时只要严格解码成功就尊重选择（可读性偏低只给 warning，
   因为真实文本可能含罕见码点）。任何失败都返回 422，带候选编码、有损预演（标注 `preview_is_lossy`）
   与失败的 `job_id`。**任何情况下都不把 U+FFFD 写进 canonical 正文。**
5. **重复导入复用规则**：`source_sha256` 相同 → 同一本书；同一本书内
   `(canonical_sha256, parser_version, normalization_version)` 相同 → 复用版本（不重复插章节/节点/映射）；
   换编码或换解析器版本 → 新版本并更新 `active_version_id`。
6. **T02 的 IMPORT 在请求内同步完成并写入终态**，仍返回 202 + `job_id`；解析失败也会留下 FAILED 任务
   （带 `last_error`）。契约不变，T10 引入调度器后改为后台执行。

## 被放弃的方案

- **把正文写进数据库**：多 MB 文本会让备份/迁移变重，也模糊了“原文不可变”的边界。
- **整个文档树存成一个 JSON 列**：无法按码点/节点查询，也无法验证映射覆盖。
- **后台线程里做导入**：SQLite 写入并发与事务边界会变复杂，收益只是让 202 更“诚实”；
  改为同步执行 + 明确文档说明，等 T10 的调度器再统一处理。
- **引入 charset-normalizer 做检测**：多一个依赖，且候选顺序 + 可读性打分的判定更可解释、可测试。
- **解码失败时用 `errors="replace"` 继续导入**：会造成静默丢字，正是 F02 要禁止的行为。

## 验证

- `pytest backend/tests/unit/test_txt_ingest.py` → 24 passed（映射无缝覆盖、CRLF 规范化标记、GB18030 自动检测、
  错误编码候选与有损预演、emoji/扩展汉字码点、重复对白位置、标题判定、无标题单章、BOM、空文件）。
- `pytest backend/tests/integration/test_book_import.py` → 11 passed（导入→任务→目录→正文全流程、
  错误编码 422 且不建书、重复导入复用、换编码新版本、415/413、分页与范围校验、404 契约、落盘位置与原文不变）。
- 真实联调（本地服务，无模型调用）：用真正的 GB18030 文件通过前端代理导入 →
  `canonical_length_cp=143`、11 个节点与原文逐行一致、`𠮷`/`🐈` 完整保留、无 `?` 占位与 U+FFFD；
  `data/books/<id>/{source.txt,versions/<vid>/canonical.txt}` 与库内相对路径一致。

## 迁移与回滚

新增迁移 `0003`（`text_mappings` + `book_versions.canonical_path`），不改动 `0001/0002`。
回滚只需 `alembic downgrade 0002`，但会丢失 source_map 与 canonical 路径；用户书库请用备份而非降级。