# 0018 导出快照、离线成品与真实校验状态

日期：2026-09-28 · 状态：已采纳（T15A）

## 问题

导出要做对三件事：**用哪一份标注**（生成过程中用户还在确认怎么办）、**成品能不能离线打开**、
以及**校验到底跑了没有**。前两件做错会产出“看着成功其实不可信”的文件，第三件做错就是把没跑的工具
当成 PASS（F27/F29 明确禁止）。

## 选择

1. **一个快照 = 一次导出**：`export_snapshots` 冻结标注投影、身份投影、可见性策略、样式、所选章节、
   `source_revision` 与 `snapshot_hash`。生成期间的人工更正不会改变正在导出的文件（F24）；
   新预览会得到新快照，旧快照仍然可复现。
2. **可见性策略显式**：`position_safe` 用当前阅读位置作为初读 horizon（复用 T15 的
   `withheld` + 身份还原），`reread` 用完整投影。导出与阅读页看到的是同一套投影语义。
3. **未知/未处理/过期保持原样**：`UNKNOWN`、没有标注、`stale`、`withheld` 的对白不写颜色也不写编号，
   原文照常输出；导出面板会给出这些数量（写在快照 warnings 里）。
4. **编号是真实文本**：`〔S1〕` 直接写进 HTML/EPUB，灰度打印或颜色被覆盖时仍然读得出来（F25 的后端基础）。
5. **统一渲染器**：HTML 与 EPUB 共用 `render.py` 与同一份 CSS；样张（`exports/preview`）也走同一个渲染器，
   不用阅读页截图冒充（“样张与实际文件共享渲染逻辑”）。
6. **离线成品**：HTML 单文件内联 CSS 与 `data:` 图片；EPUB 的 `mimetype` 第一且不压缩、
   只带被选中章节真正引用的图片、无远程引用、无脚本。zip 时间戳固定 → 同一输入重复导出字节一致。
7. **幂等**：指纹 = `snapshot_hash + format + style + exporter_version`；已完成的相同指纹直接复用产物，
   重复下载内容一致。文件写在 `data/exports/<artifact_id>/`，从不覆盖原书。
8. **校验分两层，状态如实**：内部检查自己实现（结构、mimetype、资源闭合、外部引用、正文逐段一致性）；
   标准检查调用 EPUBCheck（参数数组，不拼 shell），jar/java 缺失时返回 `NOT_RUN` 并写明原因。
   CLI 退出码：通过 0 / 失败 1 / 用法错误 2；省略 `--expected-text-file` 时一致性记为 `SKIPPED`。
9. **导出不碰模型**：`ndr/exports` 不导入任何 LLM 适配器；测试断言导出前后 `inference_runs` 数量不变。

## 被放弃的方案

- **导出时读实时标注**：生成中途有人更正就会出现“半新半旧”的文件；必须冻结。
- **只做 HTML 不做 EPUB / 只做 EPUB 不做 HTML**：阅读器生态两种都需要，且 HTML 是“断网可读”的兜底。
- **用外链图片或 CDN 样式**：离线打不开，也会泄露本地路径；一律内联/打包本地资源。
- **没装 EPUBCheck 就报 PASS**：这是明确禁止的假成功；改为 `NOT_RUN` + 原因。
- **导出覆盖原书或写进书籍目录**：会破坏「原文不可变」；成品单独放在 `data/exports/` 下。
- **把整本书的资源都塞进节选导出**：违反 F26 的资源闭包要求；只带被选中章节引用到的资源。

## 验证

- 后端：`pytest backend/tests` → **328 passed**（T15 时 314；新增 `test_export_render.py` 8 项 +
  `test_exports.py` 7 项）。`ruff` 全绿。
  - F21：TXT（已确认 + 自动标注）→ EPUB/HTML；正文完整、`〔S1〕` 存在、下载 MIME/中文文件名正确、
    **导出前后 `inference_runs` 不变**。
  - F22：EPUB（ruby + 图片）→ EPUB/HTML；`resource_closure` 通过、注音不进正文、图片随包、
    EPUB→HTML 时图片内联为 data URL。
  - F26：只导出第一章 → 只带该章引用的资源，文件名标「（节选）」。
  - F27：损坏的 zip 被内部检查判为失败；`state != COMPLETED` 的产物不允许下载（409）。
  - F30：同快照同格式重复导出复用同一产物（同 sha256、库里只有一行 artifact），重复下载内容一致。
  - 快照隔离：冻结后做人工更正 → 旧快照仍导出旧内容，新预览得到新快照与不同 hash。
  - CLI：内部检查失败退出 1；缺 jar 时 `standard=NOT_RUN` 且整体仍以 0 退出（内部通过）。
- 原创样例：`evaluation/examples/exports/` 由 `minimal-txt-001/text.txt` + 离线确定性 FakeProvider 生成，
  `manifest.json` 记录快照哈希、大小、sha256 与校验结果。实跑 CLI 输出：
  `internal=ok · standard=NOT_RUN (未提供 --epubcheck-jar)`，退出码 0。
- 本轮修复的**真实缺陷**：导出最初按数据目录里的相对路径读资源，而 EPUB 的资源登记的是**源包内路径**
  （如 `OEBPS/images/cover.png`），导致生成的 EPUB 引用不存在的图片（`resource_closure=false`）。
  现在复用导入侧的 `read_resource_bytes`，从源包读取并做同样的越界/加密检查。
- **未验证（BLOCKED）**：EPUBCheck 标准检查未运行（本机没有 jar，也没有联网安装）——状态如实记为 `NOT_RUN`；
  真实 EPUB 阅读器试读属 T15B。

## 迁移与回滚

新增迁移 `0005`：`export_snapshots`、`export_artifacts`（含索引与外键，降级会删除这两张表）。
回滚 = `alembic downgrade 0004` 并删除 `ndr/exports/`、`api/exports.py`、`domain/exports.py`、
`scripts/validate_exports.py`；已生成的成品文件在 `data/exports/` 下，可独立删除。