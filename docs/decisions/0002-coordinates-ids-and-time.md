# 0002 坐标、ID 与时间规范

日期：2026-09-28 · 状态：已冻结契约（T00 定义，T01/T02 实现）

## 问题

对白定位、场景状态、人工更正与导出都依赖坐标和 ID 的稳定性。如果坐标基准或 ID 生成方式在中期
改变，已存标注、撤销、缓存键与导出快照都会失效。

## 选择

- **坐标**：某个 `book_version` 规范化全文中的 **Unicode 码点区间 `[start_cp, end_cp)`**。
  规范化规则与 `source_map` 一同记录，映射段至少保存
  `canonical_start_cp, canonical_end_cp, chapter_id, node_id, source_href, source_text_start_cp,
  source_text_end_cp, synthetic`。
- **前端**：每个叶节点建立码点→UTF-16 映射；`JS string.length` 不得当作码点长度使用。
- **原文不可变**：改编码/解析器产生新 `book_version`，不原地替换。
- **ID**：不透明字符串。`quote_id` 由 `(book_version_id, start_cp, end_cp, scanner_version)`
  稳定派生；`utterance_id`、`scene_id`、`speaker_group_id` 由后端生成。展示编号（S1、S2…）
  只在所属场景内有意义。模型只返回临时引用（如 `new1`），由后端映射为稳定 ID。
- **时间**：存储与传输用 UTC ISO 8601，前端展示时转本地时区。
- **命名**：API JSON 使用 snake_case。

## 被放弃的方案

- **用字节偏移或 UTF-16 索引作为存储坐标**：不同编码的 TXT 与 EPUB 之间不可比，
  且 emoji/扩展汉字会导致前端计数错误（夹具 F11 专门检查）。
- **用姓名或语气字符串作为身份主键**：同一角色可能有多个称呼，行踪与语气不能定义身份。
- **让模型输出字符偏移或直接写数据库**：模型不负责定位与持久化，输出必须先经程序校验。

## 验证

T00 只冻结契约，并提供金标准 schema（`evaluation/schemas/gold-standard.schema.json`）与原创样例。
码点映射、重复对白、emoji 与扩展汉字的定位正确性由 T02/T05 的夹具（F11、F04）验证；
在这些测试通过前，本决策仅表示契约，不表示实现正确性。

## 迁移与回滚

契约在首个迁移（T01）落地后，任何坐标基准变更都需要新增 `normalization_version` 并重新导入，
不得就地改写既有 `book_version`。
