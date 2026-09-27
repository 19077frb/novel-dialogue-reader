# 0007 引号扫描、Gap 与金标准工具的选择

日期：2026-09-28 · 状态：已采纳（T05）

## 问题

T05 要把“原文”变成“候选引语 + Gap”，并建立制作真实金标准的工具。需要决定：扫描器给多少判断、
候选怎么落库与重扫、异常引号怎么处理、Gap 怎么构造，以及评测工具与前端覆盖显示怎么与“识别结果”划清界限。

## 选择

1. **扫描器只提出候选，不做归属。** 输出位置、配对、嵌套层级与（仅在排版约定明确时的）类型提示：
   `《》〈〉` → `quotation`，`（）()` → `other`，`『』` 在嵌套里 → `quotation`；
   `「」『』“”‘’` 的类型提示为 `null`——「」里可能是对白、心声或强调，扫描器不猜。
   代码里没有任何“轮流分配”“人名匹配”“按数量估计说话人”的逻辑，并有测试断言
   `ScannedQuote` 不含 speaker/assignment 类字段。
2. **候选是派生数据，ID 稳定派生。** `quote_id` = `sha1(book_version_id|start_cp|end_cp|scanner_version)`，
   `gap_id` 同理。重新扫描会替换该版本的候选行（同一内容得到同一批 ID，可重复执行），
   但**已有用户标注时拒绝覆盖**（409 `USER_LABELING_PRESENT`）——人工结果永远优先。
3. **异常引号既不吞章也不静默消失。** 单个候选上限 `max_length_cp=1200`（超过 → 丢弃 + `quote_too_long`），
   跨段上限 `max_span_paragraphs=3`（超过 → 丢弃 + `quote_spans_too_many_paragraphs`），
   嵌套上限 8 层（超出 → 按正文处理 + `nesting_too_deep`）；开引号超过长度上限 4 倍仍未闭合则放弃
   （`unclosed_quote`），找不到开引号的闭引号记为 `stray_close`。所有丢弃都会写警告并计入 stats。
4. **Gap 只用相邻的“外层”候选。** `nesting_depth == 0` 的候选按位置相邻成对，中间必须有实际叙述；
   嵌套引用不参与（它不构成新的发言轮次）；**不按章节截断**（跨章节照常产生，是否结束场景留给 T09）；
   `decision` 一律 `UNCERTAIN`——扫描器不判断语义。
5. **不新增迁移**：沿用 T01 的 `quotes`/`gaps` 表（`delimiter`、`nesting_depth`、`parent_quote_id`、
   `utterance_id`、`scanner_version`、`proposed_decision` 已经够用）。`utterance_id` 在 T05 保持 `NULL`：
   合并同一发言需要证据，扫描器不能仅凭相邻位置合并。
6. **金标准工具与“识别结果”分离**：`ndr/evaluation/gold_standard.py` 提供结构校验（jsonschema）、
   跨字段检查（引用完整性、范围、resolvable 与分组一致性、证据时点、Gap 的 must_keep 范围）、
   扫描器覆盖率统计与模板导出；CLI 为 `backend/scripts/gold_standard.py validate|template|scan`。
   模板里的对白默认 `resolvable=false, group_id=null, kind=unknown`——**模板不是金标准**。
7. **前端只显示“候选覆盖”**：扫描器命中处画虚线（`data-testid=candidate-quote` + `data-quote-id`），
   可一键关闭；同时显示的文案明确写“尚未判定说话人”。颜色/编号/人物名仍然只有 `AnnotationLayer`
   一个空占位，组件测试断言不出现 speaker/quote 类名。

## 被放弃的方案

- **扫描时顺便猜类型或说话人**：会让“候选”被误当成识别结果，违反 T05 门槛。
- **把候选 ID 用随机 UUID**：重新扫描就会换 ID，T12 的人工更正会全部失效。
- **重新扫描时直接删掉一切**：会静默丢弃用户确认；改为有标注就 409 拒绝。
- **按章节重置 Gap**：Gap 允许跨章节，按章节截断会人为制造场景边界。
- **不做长度保护**：一个缺失的收尾引号会吞掉整章（F10 专门验证这点）。
- **前端直接画颜色**：候选不是识别结果；一旦画色就会误导用户。

## 验证

- `pytest backend/tests` → **163 passed**。新增：`test_quote_scanner.py` 23 项（配对/嵌套/父引用/类型提示/
  未闭合/游离闭引号/长度与跨段/硬上限/嵌套上限/码点偏移/ID 稳定性/stats/Gap 规则）、
  `test_source_map.py` 7 项（映射无缝覆盖、CRLF 与 EPub 映射、候选与映射一致、跨块引语映射链）、
  `test_quote_queries.py` 9 项（导入即产出候选、章节过滤、详情与上下文、Gap 列表、定位接口、
  重扫幂等、有标注时 409、扫描不产生标注/场景、错误契约）、`test_gold_standard.py` 10 项
  （T00 样例通过校验且候选覆盖 5/5、模板往返、各类引用错误、覆盖率缺失、CLI 退出码与 JSON 输出）。
- `ruff check backend/src backend/tests backend/scripts` → All checks passed。
- 前端：`vitest` 17 passed（新增候选标记与嵌套标记、ReaderPage 候选开关）；Playwright 4 passed
  （TXT 阅读页出现候选覆盖并可关闭、EPUB 页出现候选覆盖）。
- 离线真实样例：`python backend/scripts/gold_standard.py validate --gold evaluation/examples/minimal-txt-001/gold.json
  --text evaluation/examples/minimal-txt-001/text.txt` → 通过，候选覆盖“精确 5 / 缺失 0”。

## 迁移与回滚

不新增迁移。回滚 = 删除 T05 代码路径；已写入的 `quotes`/`gaps` 行是派生数据，可随时重新扫描生成，
但若已有人工标注，重扫会被拒绝（需先处理人工结果）。