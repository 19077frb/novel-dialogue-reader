# 公共契约摘要（NDR）

本文件是 DEVELOPMENT.md 第 3～6 节的可执行摘要，供前后端与后续任务对齐。
**实现状态以 [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) 为准**：T00 实现了
`GET /api/health`，T01 实现了数据库 schema、迁移、统一错误体与分页；端点清单中的业务接口
仍由后续任务逐项实现。

## 1. 通用 HTTP 约定

- 前缀 `/api`；JSON 统一 snake_case、UTF-8；时间为 UTC ISO 8601（前端转本地时区展示）。
- 列表分页用 `cursor`/`limit`，返回 `{ items, next_cursor }`；普通成功返回
  `{"data": ..., "request_id": "..."}`；文件资源与健康检查使用明确的独立响应体。
- 错误体固定为 `{"error": {"code": "...", "message": "...", "details": {}}, "request_id": "..."}`。
- 状态码：新建 201；异步任务 202；参数错误 422；版本/幂等冲突 409；缺资源 404；超大文件 413；
  不支持的格式 415。
- 上游错误映射为稳定业务错误码（`PROVIDER_AUTH_FAILED`、`MODEL_NOT_FOUND`、`RATE_LIMITED`、
  `PROVIDER_TIMEOUT`、`INVALID_MODEL_OUTPUT` 等），不透传密钥与完整上游响应。
- 服务默认只监听 `127.0.0.1:8765`；CORS 仅允许配置的本地前端来源。非 GET 业务请求验证本地来源，
  文件导入走 multipart；避免外部网页触发本地付费任务。

## 2. 坐标、ID 与版本

- 字符坐标是一个书籍版本规范化全文中的 **Unicode 码点区间 `[start_cp, end_cp)`**；
  前端另建叶节点码点→UTF-16 映射，不用 JS `string.length` 当码点长度。
- 原文版本不可变：更换编码/解析器产生新的 `book_version`，`canonical_sha256` 与
  `canonical_length_cp` 标识其内容。
- ID 为不透明字符串。Quote ID 由“原文版本 + 位置 + 扫描器版本”稳定派生，**不由模型生成**。
- 展示编号（场景内 S1/S2…）与内部稳定 ID 分离；合并/拆分/撤销通过不可变历史实现。
- 可变实体有递增 `version`；写请求携带期望版本，冲突返回 409 并保留数据。
- 金额用整数最小单位或 Decimal 字符串 + currency；缺价格时只报 token，不伪造金额。

## 3. 状态枚举

| 维度 | 取值 |
| --- | --- |
| Scene 状态 | `OPEN / PENDING_BOUNDARY / CLOSED` |
| Gap 决策 | `CONTINUE / UPDATE / BREAK / UNCERTAIN` |
| Quote 类型 | `speech / thought / quotation / group / other / unknown` |
| 归属（仅普通 speech） | `EXISTING / NEW / UNKNOWN`，其他类型为 null |
| 标注状态 | `PROVISIONAL / ACCEPTED / USER_CONFIRMED / UNKNOWN`，另有独立 `stale` 与 `user_locked` |
| 待确认队列 | `PENDING / DEFERRED / RESOLVED`（不等于标注准确性） |
| 任务种类 | `IMPORT / INFERENCE / RECHECK / RECOMPUTE / EXPORT`（INFERENCE 的 purpose 为 `preview/process`） |
| 任务状态 | `QUEUED / RUNNING / PAUSING / PAUSED / PARTIAL / COMPLETED / FAILED / BUDGET_EXHAUSTED / NEEDS_RECONCILIATION` |
| 推理尝试 | `PREPARED / DISPATCHED / SUCCEEDED / FAILED / UNKNOWN_OUTCOME` |
| 健康检查数据库 | `READY / NOT_INITIALIZED / OUTDATED / ERROR`（T01 起由真实迁移状态决定） |

`preview` 与 `process` 是任务目的，不是两套识别引擎：语义输入相同必须命中同一缓存。

## 4. 端点清单与实现任务

| 端点 | 契约摘要 | 实现任务 |
| --- | --- | --- |
| `GET /api/health` | 进程、数据库与版本状态；不调用模型 | **T00 已实现** |
| `POST /api/books/import` | multipart file + encoding；202 返回 book_id 与 IMPORT job_id（TXT/EPUB） | **T02/T03 已实现** |
| `GET /api/books`、`GET /api/books/{id}` | 元数据、导入状态、当前版本 | **T02 已实现** |
| `GET /api/books/{id}/chapters` | 按 ordinal 的目录（EPUB 为 spine 顺序） | **T02/T03 已实现** |
| `PUT /api/books/{id}/reading-progress` | 保存阅读位置与阅读模式，不调用模型 | **T04 已实现** |
| `GET /api/books/{id}/content` | 结构化正文节点 + `payload`（章节或码点范围；horizon 见 T04/T15） | **T02/T03 已实现**（范围部分） |
| `GET /api/books/{id}/resources/{resource_id}` | 受控登记资源（图片等，独立响应体） | **T03 已实现** |
| `GET/POST/PATCH/DELETE /api/model-profiles*` | 非敏感配置与 has_key；keep/replace/remove 密钥 | **T06 已实现** |
| `POST /api/model-profiles/test` | 有预算的微型连接测试（已存配置或草稿） | **T07 已实现** |
| `POST /api/books/{id}/estimates` | 纯本地估算（窗口/目标/token），说明依据 | **T10 已实现** |
| `POST /api/jobs`、`GET /api/jobs/{id}`、`pause`/`resume`/`run`/`reconcile` | 幂等创建、窗口检查点、用量、恢复与对账 | **T10 已实现**（后台工作循环见 T14） |
| `GET /api/books/{id}/annotations` | 有效投影与图例，受 horizon 与阅读模式约束 | T11/T15 |
| `GET /api/books/{id}/review-items`、`GET /api/review-items/{id}` | 待确认队列与详情 | T13 |
| `GET /api/quotes/{id}` | 候选对白详情（含上下文与前置 Gap） | **T05 已实现**（主动标记见 T13） |
| `POST /api/quotes/{id}/corrections`、`POST /api/gaps/{id}/corrections` | 人工更正，不调用模型 | T12 |
| `POST /api/review-items/{id}/defer`、`POST /api/corrections/{id}/undo` | 延后与撤销（版本校验） | T12/T13 |
| `POST /api/scenes/{id}/speaker-revisions` | merge/split 与影响范围 | T12 |
| `POST /api/quotes/{id}/recheck` | 有上限的局部复核 | T14 |
| `GET /api/books/{id}/usage` | 按任务/阶段/模型汇总，含未知用量标记 | **T10 已实现** |
| `/api/books/{id}/exports*`、`GET /api/exports/{id}/download` | 冻结快照、生成、校验与下载 | T15A/T15B |

## 5. 模型接入契约

- 首版实现一种有实测依据的 `chat-completions-compatible` 协议与仅测试用 FakeProvider；
  接口为 `test_connection / generate_labels / estimate_tokens / normalize_usage / capabilities`。
- Base URL 是 API 根路径，适配器自行追加相对端点；不接受完整业务端点造成模糊拼接。
- 只返回 `has_key`，不回传完整或可恢复密钥；密钥不进入前端持久存储、缓存键与普通日志。
- 连接测试用微型结构化任务，同时检查鉴权与输出可解析；**连接成功不代表小说识别效果已验证**。
- FakeProvider 只能在测试/明确演示配置下启用；真实提供方失败不得静默回退到假数据。

## 6. 缓存指纹与用量口径

缓存键包含：原文版本、目标 IDs、完整实际输入、模型与生成参数、协议/提示/schema/筛选版本、
有效依赖状态、阅读模式与证据 horizon。**排除** UI 颜色、job_id、`preview/process` 目的和不影响
语义的显示选项。

用量按每次尝试记录（含测试连接、格式重试与局部复核）；未知用量保持预留量并标记，不写 0。

## 7. 导出契约

- 导出只读取不可变原文与冻结标注投影（快照），不调用模型、不覆盖原文件、不因未处理或待定删除正文。
- 只附加未过期的 `ACCEPTED` 与 `USER_CONFIRMED` 结果；`UNKNOWN`、`PROVISIONAL`、`stale` 与未处理
  对白保留原文。
- 样式支持 `color_and_label / color_only / label_only`；编号是真实文本节点（如 `〔S1〕`），
  不依赖 CSS 伪元素或脚本。
- EPUB 为结构正确的 EPUB 3（`mimetype` 首项且不压缩、OPF `version="3.0"`、nav 登记为导航文档）；
  HTML 为离线单文件、图片内嵌 data URL、无 localhost 依赖。
- 内部校验与标准校验（EPUBCheck）分别记录；工具未运行是 `NOT_RUN`，不等于通过。

## 8. 离线与真实证据的边界

FakeProvider 的成功只证明业务/状态机；真实模型兼容性需要真实调用，跨作品准确率需要独立作品 +
人工标注。两者记录为独立的 `live_verification`、`quality_evaluation` 状态，不得互相替代。

## 9. 数据库 schema 与迁移（T01 起）

- 表结构由 ``backend/src/ndr/storage/models/`` 的 SQLAlchemy 模型定义；迁移用
  Alembic autogenerate 生成，**模型是唯一权威来源**，测试会比对模型与真实数据库列是否漂移。
- 迁移只追加，不删库重建：``0001`` 核心表（书籍/版本、文档、引语、场景、标注、模型配置、任务），
  ``0002`` 待确认队列与人工更正（T12/T13 使用）。
- 命令（从项目根目录）：
  ``uv run --project backend alembic -c backend/alembic.ini upgrade head``。
  数据库路径由 ``NDR_DATA_DIR`` 推导，配置文件用 ``%(here)s`` 解析，不受 cwd 影响。
- ``NDR_AUTO_MIGRATE=1`` 时应用启动会先迁移到 head（E2E 使用）；默认关闭，避免隐式改动用户书库。
- 关键约束：``quotes`` 的位置+扫描器版本唯一（Quote ID 稳定派生）；``annotations.quote_id`` 唯一
  （每个 quote 只有一个当前投影）；``review_items`` 的 quote_id/gap_id 恰好一个（CHECK）；
  ``model_profiles`` 没有任何存放密钥的列；``inference_runs.usage_json`` 可为 NULL 表示未知用量，
  绝不能写成 0。

| 表 | 用途 | 首次落地 |
| --- | --- | --- |
| books / book_versions | 书籍元数据与不可变原文版本 | 0001 |
| chapters / content_nodes / resources | 章节、统一文档树节点、登记资源 | 0001 |
| quotes / gaps | 候选引语与相邻引语之间的叙述 | 0001 |
| scenes / scene_memberships / speaker_groups / participants | 场景、归属历史、场景内匿名分组与参与者 | 0001 |
| annotations / annotation_history / identity_revisions | 当前标注投影、旧结果快照、merge/split | 0001 |
| model_profiles | 模型配置（不含明文密钥） | 0001 |
| jobs / job_windows / inference_runs / result_cache | 任务、窗口、每次推理尝试与语义缓存 | 0001 |
| review_items / corrections | 待确认队列与人工更正（含撤销留痕） | 0002 |
| export_snapshots / export_artifacts | 导出快照与成品 | T15A |

## 10. TXT 导入与编码（T02 已实现）

- `POST /api/books/import`：multipart 字段 `file`（必填）、`encoding`（可选，留空自动检测）、
  `title`（可选，默认文件名）。返回 202 + `{book_id, book_version_id, job_id, import_status, encoding,
  encoding_confidence, chapter_count, canonical_length_cp, reused_book, reused_version, warnings}`。
- 只接受 `.txt`（其它后缀 415）；空文件 422；超过 `NDR_MAX_IMPORT_BYTES`（默认 50 MiB）413。
- 编码：`utf-8 → gb18030 → big5` 严格解码 + 可读性门槛（仅自动检测时使用）。失败返回 422，
  `details` 含 `requested_encoding`、`candidates[]`（每项 `encoding/ok/plausibility/detail`）、
  `preview`（有损，标注 `preview_is_lossy: true`）与失败的 `job_id`。**正文里绝不会出现 U+FFFD。**
- 复用：同一份原始字节复用同一本书；`(canonical_sha256, parser_version, normalization_version)` 相同则复用版本；
  换编码生成新版本并切换 `active_version_id`。
- 正文读取：`GET /api/books/{id}/content` 支持 `chapter_id` 或 `start_cp`/`end_cp`（`[start,end)` 码点），
  `limit`（默认 500，最大 2000）与 `cursor`；返回节点 `{node_id, node_type, ordinal, start_cp, end_cp,
  chapter_id, chapter_ordinal, text}` 与 `next_cursor`。范围非法返回 422，章节不存在返回 404。
- 落盘布局（数据目录内，API 不暴露路径）：`books/<book_id>/source.<ext>`（原始字节，不可变）、
  `books/<book_id>/versions/<version_id>/canonical.txt`（规范化全文，LF）。
- `text_mappings` 每个 canonical 行一条记录，无缝覆盖整段 canonical 文本；`synthetic=true` 表示
  该行行尾是导入时规范化出来的（原文为 CRLF/CR）。

## 11. EPUB 导入与资源（T03 已实现）

- 阅读顺序只由 **spine** 决定；章节标题取自 nav.xhtml（`epub:type="toc"`，缺失时退回 NCX），
  目录项不改变顺序。`chapters[].source_href` 记录 spine 文档路径。
- canonical 文本是各块文本用 `\n` 连接，块间换行是合成的（`text_mappings.synthetic=true`）；
  `source_text_*` 是所在 XHTML 文档内纯文本的偏移。
- 受限节点：块级元素 → `paragraph`/`heading`，`hr` → 零长度 `separator`，
  `img`/`svg image` → 零长度 `image`（`payload.resource_id`/`media_type`）。
  `script`/`style`/`object`/`iframe`/`audio`/`video` 等既不进正文也不登记为资源。
- ruby：基底文字保留在正文，`rt`/`rp` 不进入正文；注音保存在段落节点 `payload.ruby`
  （`[{start_cp, end_cp, base, rt}]`）。
- 资源：非正文 manifest 项登记为 `resources`（`media_type`/`relative_path`/`sha256`），
  脚本类媒体类型不登记；`GET /api/books/{id}/resources/{resource_id}` 从源 EPUB 内按条目读取，
  并带 `X-Resource-Sha256`。资源 ID 只在所属书籍版本内唯一；跨书或不存在的 ID 返回 404。
  外链（http/https）只告警不下载；spine 中的包外文档直接拒绝（422 `EPUB_EXTERNAL_REFERENCE`）。
- 安全与上限（可配置 `NDR_MAX_EPUB_*`）：条目数、单条目解压大小、总解压大小、spine 条目数；
  拒绝绝对路径/`..`/反斜杠逃逸、重复条目、符号链接条目、加密条目、非 ZIP 容器与非法 XHTML。
  失败返回 422，`details.reason_code` 为稳定值（如 `EPUB_UNSAFE_PATH`、`EPUB_SIZE_LIMIT`），
  并留下 FAILED 的 IMPORT 任务。
- `book_versions.encoding` 对 EPUB 记为 `"xml"`（正文编码由各 XHTML 文档的 XML 声明决定）；
  `ImportResult.encoding` 对 EPUB 为 `null`。

## 12. 阅读进度（T04 已实现）

`PUT /api/books/{book_id}/reading-progress`

```json
{ "book_version_id": "bv1", "read_position_cp": 1200, "reading_mode": "initial", "expected_version": 3 }
```

- 只写数据库，**不调用模型**、不产生任务或标注。
- `book_version_id` 必须属于该书籍（否则 422）；`read_position_cp` 必须落在该版本
  `[0, canonical_length_cp]` 内（否则 422）。
- 带 `expected_version` 时做乐观并发校验：版本不符返回 409 `VERSION_CONFLICT`
  （`details.current_version` 为当前版本），前端刷新后按最新版本重试一次，绝不覆盖较新写入。
- 响应 `{book_id, book_version_id, read_position_cp, reading_mode, version}`；
  `GET /api/books/{id}` 也会返回当前 `read_position_cp` 与 `reading_mode`。
- `reading_mode` 取值 `initial`（初读，只用读到的证据）或 `reread`（重读，可用后文证据）；
  T15 起它会影响证据 horizon 的投影。

## 13. 候选引语与 Gap（T05 已实现）

> 这些接口返回的是**扫描器提出的候选**，不含任何说话人判断；着色/编号属于 T11 起的标注投影。

| 端点 | 说明 |
| --- | --- |
| `GET /api/books/{id}/quotes` | 候选列表；支持 `chapter_id`、`cursor`、`limit`（默认 200，最大 500） |
| `GET /api/books/{id}/gaps` | 相邻外层候选之间的叙述；`decision` 为 `UNCERTAIN`（扫描器不判断语义） |
| `GET /api/books/{id}/locate?start_cp=&end_cp=` | 把码点范围映射回章节/节点/源文档（纯读） |
| `POST /api/books/{id}/quotes/scan` | 重新扫描；202 + RECOMPUTE 任务；已有用户标注时 409 |
| `GET /api/quotes/{id}` | 候选详情：引号内文字、含引号片段、`kind_hint`、上下文与前置 Gap |

契约要点：

- `quote_id = sha1(book_version_id|start_cp|end_cp|scanner_version)`，重新扫描同一内容得到同一批 ID；
  `gap_id` 同理。
- `kind_hint` 只在排版约定明确时给出：`《》〈〉` → `quotation`，`（）()` → `other`，
  嵌套里的 `『』` → `quotation`；其余为 `null`。
- `nesting_depth`/`parent_quote_id` 表达嵌套；`utterance_id` 在扫描阶段恒为 `null`（合并同一发言需要证据）。
- 扫描保护：单候选 ≤1200 码点、跨段 ≤3 个换行、嵌套 ≤8 层；超限的候选丢弃并在
  `POST .../quotes/scan` 的 `warnings` 里给出 `quote_too_long` / `quote_spans_too_many_paragraphs` /
  `unclosed_quote` / `stray_close` / `nesting_too_deep` 等稳定 code。
- `POST .../quotes/scan` 是幂等的（替换该版本候选），但**已有用户标注时返回 409**
  （`details.reason = USER_LABELING_PRESENT`），人工结果不被自动候选覆盖。
- 阅读页只把候选画成虚线并提供开关（`data-testid=candidate-quote`），不显示颜色、编号或人物名。

## 14. 金标准工具（T05）

后端提供 `ndr.evaluation.gold_standard` 与 CLI `backend/scripts/gold_standard.py`：

```powershell
# 结构 + 跨字段 + 扫描器覆盖率
uv run --project backend python backend/scripts/gold_standard.py validate `
  --gold evaluation/examples/minimal-txt-001/gold.json `
  --text evaluation/examples/minimal-txt-001/text.txt

# 用扫描器候选生成可填写的标注模板（不是金标准）
uv run --project backend python backend/scripts/gold_standard.py template --text <文件> --out <输出>
```

检查项：JSON Schema（`evaluation/schemas/gold-standard.schema.json`）、引用完整性（scene/quote/gap 存在）、
范围合法性与包含关系（fragment ⊆ quote、must_keep ⊆ gap）、`resolvable=false ⇒ group_id=null`、
`group_id` 属于同场景参与者、证据 `visible_from_cp` 不早于证据本身、以及“金标准对白是否被候选扫描器覆盖”。
退出码非零表示存在 error。

## 15. 模型配置与凭据（T06 已实现）

| 端点 | 说明 |
| --- | --- |
| `GET /api/model-profiles` | 配置列表（**不含任何密钥字段**，只有 `has_key` 与 `credential_mode`） |
| `GET /api/model-profiles/protocols` | 协议能力声明（页面据此说明实际能力） |
| `POST /api/model-profiles` | 新建；可选 `api_key` 与 `credential_mode`（201） |
| `PATCH /api/model-profiles/{id}` | 字段变更 + `api_key`（替换）/`remove_api_key`（清除）/都不给（保持）+ `expected_version` |
| `DELETE /api/model-profiles/{id}` | 删除配置与其凭据引用；被任务引用时 409 |

契约要点：

- `credential_mode` 取值 `session`（进程内存）/`system`（系统凭据库）/`none`。
  系统凭据库不可用或写入失败时**降级为 `session`** 并返回 `credential_warning`；
  响应里的 `credential_mode` 是**实际生效**的模式，不是请求里写的模式。
- 数据库只保存 `credential_ref`（`model-profile/<id>`），API 不返回该字段；密钥绝不进入响应、缓存键或日志。
- `base_url` 必须是 API 根路径：填成 `.../chat/completions` 之类完整端点返回 422。
- `params` 中出现 `api_key/token/authorization/password/secret` 等键返回 422（密钥只能走 `api_key`）。
- `expected_version` 不符返回 409 `VERSION_CONFLICT`；同名配置、被任务引用的删除返回 409 `RESOURCE_CONFLICT`。
- `POST /api/model-profiles/test`（有预算的微型连接测试）属于 T07，本任务不发起任何真实调用。

## 16. 模型输出契约与连接测试（T07 已实现）

**输出契约**（`llm/schemas.py`，`schema_version="1.0"`，`extra` 一律禁止）：

```json
{
  "schema_version": "1.0",
  "scene_updates": [{"temp_ref": "scene_2", "after_gap_id": "g1", "starts_at_quote_id": "q2", "evidence_refs": ["p1"]}],
  "gap_decisions": [{"gap_id": "g1", "decision": "BREAK", "evidence_refs": ["p1"]}],
  "new_speakers": [{"temp_ref": "new1", "scene_ref": "scene_current", "first_quote_id": "q2", "description": "门外的声音", "evidence_refs": ["p2"]}],
  "labels": [{"quote_id": "q1", "scene_ref": "scene_current", "kind": "speech", "assignment": "EXISTING", "speaker_ref": "speaker_a", "basis": "DIRECT", "evidence_refs": ["p1"]}],
  "identity_proposals": [{"operation": "MERGE", "input_refs": ["s1", "s2"], "output_refs": ["s1"], "evidence_refs": ["p3"]}],
  "needs_context": ["q3"]
}
```

程序级校验（`llm/validation.py`）：目标对白必须全部覆盖且唯一；只能引用**程序给出的** ID；
新场景必须由同一次输出里的 `BREAK` 触发；`NEW` 必须引用已声明的 `temp_ref` 且同场景；
非 speech 的 `assignment`/`speaker_ref` 必须为 null；证据必须来自已发送的片段。
解析只接受整段 JSON 或整段代码块，**不从长文本里截取**、**不执行模型输出**。
重试策略 `RetryPolicy(max_format_retries=1)` 只对 `INVALID_MODEL_OUTPUT` 生效。

**连接测试** `POST /api/model-profiles/test`：

```json
{"profile_id": "mp1"}                       // 或
{"draft": {"name": "临时", "protocol": "chat-completions-compatible", "base_url": "https://api.example.com/v1",
           "model": "m", "credential_mode": "session", "api_key": "..."}}
```

- 请求体是**微型结构化任务**：要求模型回显固定空结果对象；返回
  `{ok, protocol, model, adapter, detail, latency_ms, usage, usage_unknown, error_code, run_id}`。
- 每次调用都写入 `inference_runs`（`job_id` 为 NULL、快照含 `purpose=connection-test`）；
  网络调用在数据库事务之外；未知用量保持 NULL 且 `usage_unknown=true`。
- 上游错误映射为稳定错误码（`PROVIDER_AUTH_FAILED`/`MODEL_NOT_FOUND`/`RATE_LIMITED`/
  `PROVIDER_UNAVAILABLE`/`PROVIDER_TIMEOUT`/`INVALID_MODEL_OUTPUT`）；详情脱敏截断。
- `fake-provider` 只有设置 `NDR_ALLOW_FAKE_PROVIDER=1` 才可用，否则 422；界面会标注“测试用适配器”。
- **连接成功只说明鉴权与 JSON 输出可解析，不代表小说标注效果**（效果评测属 T16）。

## 17. 上下文窗口与预算（T08 已实现，内部契约）

处理窗口是**调用边界**，不是场景边界；场景状态由 T09 管理。窗口构建不新增 HTTP 端点，
但它的口径会进入 T10 的缓存键：

- `BudgetPolicy`（PLAN 7.3 起点）：正文 `context_tokens=2500`、重叠 `overlap_tokens=200`、
  状态 `state_tokens=300`、提示预留 `prompt_reserve_tokens=900`、输出预留 `output_reserve_tokens=800`；
  复核策略 `RECHECK_POLICY=6000/300/600`。
- T17 新增字段（全部进入 `as_key()`，因此进入依赖哈希与缓存键）：
  `gap_compression`（默认 **False**）、`gap_compression_threshold_cp=80`、
  `gap_compression_margin_sentences=1`、`gap_compression_max_ratio=0.6`、
  `recheck_max_targets=0`、`strong_model_share=0.0`。
- 证据保留层级：目标对白 → 目标之间的 Gap（必留） → 场景状态/已锁定结果 → 两端重叠 → 外层 Gap；
  预算不足时按此顺序丢可选片段。**目标对白绝不截断**。
- 单个目标自身超过正文预算 → 独立窗口 + 标 `oversized_quote`（保留待定，按完整长度计费）。
- `initial` 模式只用 `visible_horizon_cp` 以内的原文；`reread` 忽略 horizon。
  越界证据进入省略记录（`reason=beyond_visible_horizon`），不会送进模型。
- 每条片段都有可回溯 ID（`quote_id`/`gap_id`/`node_id`/`overlap:<start>-<end>`/`state:*`），
  窗口账本逐条记录 token，正文用量等于这些记录之和。
- 策略版本：`context-1`（保守，默认）与 `context-2`（长 Gap 保守筛选），由 `policy_version_for(policy)`
  计算；窗口 `window_id`、窗口/计划 `dependency_hash` 与缓存键都带这个版本，两版结果**不会互相复用**。
  任务范围里的 `context_policy` 选择策略，缺省或未知一律回落到 `context-1`（保证一键回滚）。
- `dependency_hash` 覆盖（原文版本、目标、证据、**省略记录**、策略、策略版本、提示版本、阅读模式、
  horizon、场景引用、说话人引用）。**T10 的缓存键必须包含这些字段**，horizon 或阅读模式变化不得复用旧结果。

## 18. 场景状态、接受策略与身份修订（T09 已实现，内部契约）

`ndr.scenes` / `ndr.speakers` 把「一个窗口的模型输出」变成持久标注；不新增 HTTP 端点。

- **场景转移**：`CONTINUE` 不变；`UPDATE` 不切场景；`BREAK` 关闭当前场景（写 `end_cp`）并开新场景
  （参与者清空、编号重新开始）；`UNCERTAIN` 标 `PENDING_BOUNDARY` 并把 Gap 记入 `unresolved`。
- **状态传递**：`SceneState.snapshot()`（`scene-state-1`）随任务检查点传下去；重叠区同一 `quote_id`
  只保留一份有效标注。
- **接受策略**（`acceptance-1`）：`DIRECT` → `ACCEPTED`；`COREFERENCE`/`RESPONSE_LINK`/`STYLE_ONLY`
  → `PROVISIONAL` + 待确认；`INSUFFICIENT`/`UNKNOWN` → `UNKNOWN` + 待确认且**不建新分组**；
  非 speech 接受类型但 `speaker_ref` 恒为 null。
- **可见时点**：后端取证据中最靠后的位置（`compute_visible_from_cp`）；模型自报的可见时点被 schema 拒绝。
- **人工优先**：`user_locked` 的对白不采纳模型结果，也不写历史；只产生 `locked_quote_kept` 警告。
- **身份修订**：`identity_proposals` 带 `evidence_refs` 视为直接证据；无证据/弱证据/牵涉人工锁定
  一律进待确认队列；合并后标注指向幸存分组，历史留在 `annotation_history`。
- **窗口内引用**：`EXISTING` 可以引用同一窗口里刚声明的临时人物（同一新声音的第二句）。

**T10 需要落库的字段**：窗口 → `job_windows`（`window_id`/`target_ids_json`/`dependency_hash`/`state`/
`lease_until`），检查点 → `jobs.checkpoint_json`（含 `SceneState.snapshot()`），
每次尝试 → `inference_runs`（`usage_json` 未知时为 NULL）。

## 19. 任务、缓存与用量（T10 已实现）

| 端点 | 说明 |
| --- | --- |
| `POST /api/jobs` | 202；`{book_id, mode: preview/process, range, profile_id, reading_mode, visible_horizon_cp, budget, idempotency_key, run_now}` |
| `GET /api/jobs/{id}` | 任务状态 + 窗口列表 + usage + 剩余窗口（`JobDetailOut`；**旧的 `JobOut` 已被它取代**） |
| `POST /api/jobs/{id}/pause` | RUNNING→PAUSING（当前窗口结束后 PAUSED）；QUEUED/PARTIAL→PAUSED |
| `POST /api/jobs/{id}/resume` | 从检查点继续；PREPARED 的尝试可安全重试 |
| `POST /api/jobs/{id}/run` | 立即执行（测试/手动触发；正常流程由后台任务执行） |
| `POST /api/jobs/{id}/reconcile` | `{"action":"retry"\|"keep_unknown"}`：显式处理未知远程结果 |
| `POST /api/books/{id}/estimates` | 纯本地估算（窗口数/目标数/token），不写库、不调用模型 |
| `GET /api/books/{id}/usage` | 运行次数、未知用量次数、token 汇总、按状态/模型计数；缺价格资料时 `cost=null` |

契约要点：

- 幂等：同一 `idempotency_key` + 相同请求摘要 → 返回既有任务；摘要不同 → 409 `IDEMPOTENCY_CONFLICT`。
- 窗口：`job_windows` 记录 `window_id`/`state`/`dependency_hash`；已完成窗口不会重复调用。
- 缓存：键只含语义输入（决策 0012），**不含 job_id 与 preview/process 目的**。
- 预算：调用前预留、调用后结算；超出 `max_input_tokens` → `BUDGET_EXHAUSTED` 且不再调用；
  未知 usage 保持 NULL 并单独计数，绝不按 0 计。
- 未知结果：DISPATCHED 超租约 → `UNKNOWN_OUTCOME` + `NEEDS_RECONCILIATION`，**不自动重发**。
## 20. 标注投影与预览（T11 已实现）

| 端点 | 说明 |
| --- | --- |
| `GET /api/books/{id}/annotations?start_cp&end_cp&reading_mode&visible_horizon_cp` | 只读有效投影：范围内的标注条目 + 场景内图例 + 统计。**不写库、不调用模型** |

查询与默认值：

- `start_cp >= 0`；`end_cp` 省略时取 `book_version.canonical_length_cp`；要求 `start_cp < end_cp <= canonical_length_cp`，
  否则 422 `VALIDATION_ERROR`。
- `reading_mode=initial` 且未给 `visible_horizon_cp` 时，horizon 默认为请求范围末端：
  范围内结果可见，范围之后不提前泄漏。
- 书籍没有可用版本时返回 409。

条目字段（`AnnotationItemOut`）：

- `quote_id`、`start_cp`/`end_cp`（来自 `quotes`，前端按此在节点内分片着色）、`kind`、`assignment`、`basis`、
  `status`、`source`、`speaker_group_id`、`label`、`color_index`、`visible_from_cp`、`stale`、`user_locked`、`withheld`。
- **颜色与编号都来自场景内稳定分组**：`color_index` 按分组首次发言顺序在场景内取 0..N-1；
  `label` 即 `speaker_groups.display_label`（S1、S2…），只在所属场景内有意义。
- **`withheld=true`**：初读 horizon 之下的后文证据，`label` 与 `color_index` 均为 null（前端不着色、不显示编号）。
- **UNKNOWN 不分配分组**：`speaker_group_id`/`label`/`color_index` 均为 null，前端只显示原文（无色无编号）。
- `stale` 与 `user_locked` 原样下发，T12/T13 会在此基础上做更正与确认。

图例与统计：

- `legend` 只列出**本范围内实际出现**的分组（`group_id`/`label`/`scene_id`/`color_index`/`first_quote_id`/`quote_count`）。
- `counts`：`total`/`accepted`/`provisional`/`unknown`/`stale`/`withheld`/`unprocessed_quotes`
  （最后一项 = 范围内候选里还没有标注的对白数）。
- `scenes`：与范围相交的场景摘要（`scene_id`/`status`/`start_cp`/`end_cp`）。

前端约定（实施在 `frontend/src/`）：

- 预览页与阅读页用**同一个**投影端点；预览任务（`mode=preview`）与正式任务（`mode=process`）
  共享标注存储与缓存，没有第二套临时识别结果。
- 编号渲染为真实文本节点 `〔S1〕`；跨节点的同一引语拆成多个 `span` 但共享 `data-quote-id`，
  不插入跨块的非法 `span`。
- 切换原文/标注视图、点击图例、切换颜色都只改显示，**调用数为零**。
- 幂等键有 128 字符上限：前端用范围/预算/配置的短摘要构造键，同输入复用同一任务。

测试专用开关（**不影响真实提供方**）：

- `NDR_ALLOW_FAKE_PROVIDER=1` 才允许 `fake-provider` 协议。
- `NDR_FAKE_PROVIDER_LABELS=deterministic` 让该适配器确定性地建一个分组并返回 `DIRECT` 归属，
  用于离线验证“颜色/编号”链路；默认 `unknown`（全部标为未知，不假装知道说话人）。

## 21. 人工更正、待确认队列与身份修订（T12 已实现）

| 端点 | 说明 |
| --- | --- |
| `GET /api/books/{id}/review-items` | 待确认队列；可按 `chapter_id`/`scene_id`/`reason`/`queue_status` 过滤，cursor 分页，附 `counts` |
| `GET /api/review-items/{id}` | 目标、当前标注、场景、前后叙述与允许的动作（`assign_existing`… 或 `set_gap_decision`） |
| `POST /api/quotes/{id}/review-items` | 用户主动标记问题（201，幂等：同一目标 + 原因只有一条当前项；已解决项会被重新打开） |
| `POST /api/review-items/{id}/defer` | 只延后（`queue_status=DEFERRED`）；已解决项返回 409 |
| `POST /api/quotes/{id}/corrections` | 说话人更正：`assign_existing` / `create_speaker` / `set_kind` / `mark_unknown`（201） |
| `POST /api/gaps/{id}/corrections` | Gap 更正：`CONTINUE`/`UPDATE`/`BREAK`/`UNCERTAIN`，返回场景修订影响（201） |
| `POST /api/scenes/{id}/speaker-revisions` | 场景内 `MERGE`/`SPLIT`（201） |
| `POST /api/corrections/{id}/undo` | 撤销一次人工更正（201；历史只追加） |
| `POST /api/quotes/{id}/recheck` | 局部复核（202）：围绕当前场景创建真实 `RECHECK` 任务，必须显式给出 `profile_id` 与预算 |

语义要点（DEVELOPMENT.md 3.4 / 5.3 / 6.4）：

- **一个事务一个结果**：校验版本 → 写 `corrections` → 写 `annotation_history` 旧快照 → 改当前投影 →
  解决待确认项 → 标下游 `stale`。任何一步失败都整体回滚。
- **零模型调用**：这些接口只写更正/历史/队列，不创建 `inference_runs`（测试对行数做了断言）。
- **人工优先**：更正后的标注 `source=USER`、`status=USER_CONFIRMED`（`mark_unknown` 为 `UNKNOWN`）、
  `user_locked=true`；模型结果与自动 merge/split 都不得覆盖它（F14，调度器把锁定 ID 传给引擎）。
- **未知不制造新人**：`mark_unknown` 只锁定「未知」，不新建分组，也不把 `assignment` 落到非 speech 类型上。
- **普通对白也能改**：目标还没有标注时先建立一个未处理占位标注（并在没有场景时新建场景），
  因此 `GET /api/quotes/{id}` / 更正接口不要求该对白已在待确认队列里。
- **跨场景拒绝**：多目标范围与 `assign_existing` 只允许同一场景内的分组，否则 422
  （`CROSS_SCENE_SPEAKER` / `CROSS_SCENE_SCOPE`）；`SPLIT` 的桶必须属于同一个分组，否则 `SPLIT_MIXED_GROUPS`。
- **下游失效**：同一场景内、同一推理窗口（或同一旧分组）的下游标注被标为 `stale` 并生成
  `STALE_DEPENDENCY` 待确认项；锁定的对白不动、历史不改。`stale_window_ids` 报告受影响的依赖哈希。
- **撤销**：目标必须仍停在该次更正产生的版本上，否则 409 `VERSION_CONFLICT`（F18）；
  撤销本身也写一条 `action=undo` 的更正记录 + 标注历史，**不做硬删除**；同一条更正不能重复撤销。
- **Gap 更正**：`BREAK` 关闭左侧场景并把其后的引语移入新场景（按新场景重新编号）；`CONTINUE`/`UPDATE`
  在右侧属于别的场景时合并回左侧（被合并场景标记为已关闭）；`UNCERTAIN` 只把边界问题留在队列里。
  场景与引语的历史都保留，撤销按记录的原 `speaker_id` 精确还原。
- **响应里的权威计数**：`updated_review_counts` 是扁平 map，键为状态值（`PENDING`/`DEFERRED`/`RESOLVED`）、
  原因值（`STALE_DEPENDENCY`…）与 `total`；前端不得自己推算。
- `GET /api/quotes/{id}` 在 T05 的上下文/Gap 之上补充 `annotation`、`scene`、`scene_groups`、
  `review_items` 与 `can_correct`（未处理的对白 `annotation=null`）。

## 22. 待确认队列与确认抽屉（T13 已实现）

页面与组件：

| 路由 | 组件 | 说明 |
| --- | --- | --- |
| `/books/:id/review` | `ReviewPage` | 待确认队列：章节/原因/状态筛选 + cursor 分页 + 权威计数 |
| （抽屉，两个入口共用） | `QuoteDetailDrawer` | 阅读页点任意引语 / 队列项「查看并确认」都会打开同一个抽屉 |
| | `QuoteContext` | 前后各 N 码点原文；「展开更多原文」只改 `context_window_cp`（本地只读） |
| | `CorrectionForm` | 四种说话人更正（阅读页与队列共用同一表单） |
| | `GapDecisionControls` | `CONTINUE`/`UPDATE`/`BREAK`/`UNCERTAIN`（场景边界问题只走 Gap 接口） |
| | `RecheckPanel` | 局部复核：显式选择模型配置 + 上限后才会创建付费任务 |

界面契约要点：

- **入口覆盖**：阅读页的候选（虚线）与标注（着色）都可以点开抽屉，因此**未处理的对白**也能人工确认；
  队列项从 `/review` 打开时会带上 `review_item_id`，展示原因/状态与「跳过（延后）」。
- **免费 vs 付费分开**：「展开更多原文」只请求本地原文；「局部复核」是折叠区里的显式按钮，
  会创建 `RECHECK` 任务并显示任务面板（真实 `calls`/缓存/用量），不复用「展开原文」的按钮。
- **队列清空 ≠ 全部识别正确**：`/review` 顶部说明这一点，并显示 `counts`（按状态/原因）；
  空结果页也重复说明，避免用户把「筛出来是空的」当成「整本书已确认」。
- **权威计数**：更正后的提示（受影响条数、下游 stale 条数）都来自响应，前端不自行推算。
- **冲突可见**：提交旧版本会得到 409，界面提示「已被其它操作更新」并刷新为最新状态，不静默失败。
- **更正后同步刷新**：抽屉在成功后失效 `review-items` / `quote-detail` / `annotations` 查询，
  阅读页颜色、图例、待确认数量与队列同时更新。
- 提示文案使用中文；组件测试与 E2E 通过 `data-testid` 定位（`review-item`、`quote-detail-drawer`、
  `correction-form`、`gap-decision-BREAK`、`drawer-defer`、`drawer-undo` 等）。

## 23. 暂停恢复、预算到顶与故障闭环（T14 已实现）

| 端点 | 说明 |
| --- | --- |
| `GET /api/jobs/{id}/recovery` | 只读：把任务状态翻译成**可执行动作**（含是否付费、是否缺凭据、建议退避秒数） |
| `POST /api/jobs/{id}/pause` / `resume` / `run` | 已有端点；`resume` 复用已完成窗口，`run` 手动触发本地调度器 |
| `POST /api/jobs/{id}/reconcile` | `retry`（显式重发，可能重复计费）/ `keep_unknown`（保留未知，转 PARTIAL） |
| `POST /api/quotes/{id}/recheck` | 局部复核（见第 21 节） |

恢复动作（`RecoveryActionOut.action`）与状态对应：

| 状态 | 动作 | 付费 |
| --- | --- | --- |
| `QUEUED` | `pause`、`run` | `run` 付费 |
| `RUNNING` | `pause` | 否 |
| `PAUSING` | `wait`（当前窗口返回后生效） | 否 |
| `PAUSED` / `PARTIAL` | `resume` | 剩余窗口会付费 |
| `BUDGET_EXHAUSTED` | `new_job`（预算不可变：用更大预算新建任务） | 是 |
| `NEEDS_RECONCILIATION` | `reconcile_keep`（免费）/ `reconcile_retry`（付费） | 视动作 |
| `FAILED`（缺凭据） | `open_settings` + `run` | `run` 付费 |
| `COMPLETED` | 无 | — |

语义要点：

- **任务失败不影响阅读**：恢复只改任务/尝试/窗口状态，绝不触碰原文与标注；E2E 断言失败后原文仍可读。
- **未知付费结果不自动重发**：超时或进程中断留下的 `DISPATCHED` 尝试在超过租约后被标为
  `UNKNOWN_OUTCOME`，任务与窗口进入 `NEEDS_RECONCILIATION`；只有显式 `retry` 才会重新排队。
  未知用量保持 `usage_json=NULL` 并计入 `unknown_usage_runs`，不写 0。
- **限流有上限地退避重试**：只有「确定没有被处理」的错误（`RATE_LIMITED`、`PROVIDER_UNAVAILABLE`）
  会自动重试，最多 `NDR_RATE_LIMIT_MAX_RETRIES` 次，退避时间
  `min(base * 2^(n-1), cap)`（默认 1s→2s，上限 30s）；每次失败尝试都单独写 `inference_runs`。
  超时**不在**自动重试行列。
- **启动恢复扫描**（`NDR_RECOVER_ON_STARTUP=1`，默认开）：`recover_on_startup` 在 lifespan 里执行
  —— 超租约的 `DISPATCHED` → 未知结果；`PAUSING` → `PAUSED`；`RUNNING` → `PARTIAL`
  （已完成窗口保持 `COMPLETED`，未完成窗口留在 `QUEUED`，等待用户显式继续）。
- **缺凭据可解释**：协议需要密钥、用户又选择了 `session`/`system` 模式却取不到密钥时，
  任务落到 `FAILED` 且 `last_error=PROVIDER_AUTH_FAILED: 缺少模型凭据…`，
  `GET /recovery` 返回 `requires_credential=true` 与 `open_settings` 动作（绝不留下 RUNNING 孤儿）。
  `credential_mode=none`（本地无鉴权网关）与 `fake-provider` 不受此限制。
- **默认关闭自动付费重算**：没有任何自动重建任务的逻辑；预算到顶后由用户在预览页显式点
  「用当前预算重新处理此范围」，或调整预算后再点（`data-testid="preview-recompute"`）。
- 新增/更新的配置项：`NDR_RATE_LIMIT_MAX_RETRIES`（默认 2）、`NDR_RATE_LIMIT_BACKOFF_BASE_SECONDS`（1）、
  `NDR_RATE_LIMIT_BACKOFF_MAX_SECONDS`（30）、`NDR_STALE_RUN_LEASE_SECONDS`（900）、
  `NDR_RECOVER_ON_STARTUP`（true）、`NDR_FAKE_PROVIDER_SCRIPT`（仅测试）。
  测试专用：FakeProvider 的失败脚本也可以写在模型配置的 `params.script` 里
  （`rate_limited_once` / `unavailable_once` / `timeout_once` / `auth_failed_once`），便于按用例切换。

## 24. 证据时点、初读身份还原与码点定位（T15 已实现）

投影（`GET /api/books/{id}/annotations`）在 T11 的基础上补齐 **身份时点**：

- 新增字段 `identity_reverts`：本次投影因为「证据还没出现」而**还原**的身份修订条数（初读专用）。
- 合并/拆分在写入 `identity_revisions` 时一并保存 `snapshot.revert`
  （`quotes`: 哪一句原本属于哪个分组；`groups`: 哪个新分组来自哪个旧分组），
  模型路径（引擎）与人工路径（更正服务）都写。
- 初读（`reading_mode=initial`）且 `visible_from_cp > visible_horizon_cp` 的身份修订**不生效**：
  被合并的对白仍按旧分组着色与编号，图例也分别列出两个分组（F17）。
  重读（`reread`）不做还原；没有时点的旧数据（例如 T09 之前写入的修订）按“已生效”处理。
- 还原只发生在**读**路径：不写数据库、不调用模型；`test_visibility.py` 对标注/历史/修订行数做了断言。

阅读端 horizon：

- 阅读页在初读模式下提交 `visible_horizon_cp = 本章末端`（`reread` 时不提交），
  并用 `data-testid="reader-horizon"` 显示「只显示到位置 X 为止的证据（N 条后文证据暂不显示；
  M 处身份合并在后文才揭示）」。
- 任务进入终态后必须失效**整族** `['annotations']` 查询：阅读页用的是带 horizon 的另一个键，
  只失效 horizon=null 那一个会让阅读页继续显示旧的（可能为空的）投影。

码点定位（DEVELOPMENT 4.1）：

- 前端新增 `src/text/codepoints.ts`：`cpLength` / `utf16IndexForCp` / `sliceByCodepoints`。
  **JavaScript 字符串下标是 UTF-16 单元**，`'😀'.length === 2`、`'𠮷'.length === 2`；
  直接 `text.slice(cp, cp)` 会在含 emoji / 扩展汉字时错位。
- `DocumentRenderer` 的候选、标注、ruby 三类切片全部改走码点换算；节点范围改用后端给的权威 `end_cp`
  （缺失时才退回 `start_cp + cpLength(text)`）。
- 回归：`frontend/tests/codepoints.test.ts` 3 项 + `DocumentRenderer` 的 3 项 astral 用例
  + E2E 里 `「😀𠮷！」` 的着色与原文完整性。

验证命令（T15）：

- `pytest backend/tests/integration/test_visibility.py`（F04 / F11 / F17 + 投影只读）；
- `npm --prefix frontend run test:e2e -- reading-visibility.spec.ts`（初读 vs 重读、astral 定位、EPUB ruby）。

测试专用：FakeProvider 的 `params.script="split_then_merge"` 会“先判成两个声音、再用窗口末尾证据合并”，
用于离线验证 F17；真实提供方不会走到这条分支。

## 25. EPUB/HTML 导出与标准校验（T15A 已实现）

| 端点 | 说明 |
| --- | --- |
| `POST /api/books/{id}/exports/preview` | 冻结一次导出快照并返回后端样张、覆盖统计与警告（**不调用模型**） |
| `POST /api/books/{id}/exports` | 按快照生成 EPUB/HTML（201；幂等，本地执行，不走网络） |
| `GET /api/exports/{id}` | 状态、校验结果与下载可用性 |
| `GET /api/exports/{id}/download` | 受控下载（EPUB `application/epub+zip`；HTML `text/html; charset=utf-8`；安全的附件文件名） |

快照（`export_snapshots`）：

- 冻结内容：标注投影（含颜色/编号/`withheld`/`stale`）、身份投影、可见性策略、样式、所选章节、
  `source_revision`（原文版本 + 标注版本/计数的指纹）、`snapshot_hash`、警告清单。
- `visibility_policy`：`position_safe` 取当前阅读位置作为初读 horizon（不提前暴露后文证据）；
  `reread` 使用完整投影。
- `selected_chapter_ids` 为空数组表示**整本**；非空表示节选（文件名会带「（节选）」）。
- 快照不可变：生成期间发生的人工更正不会改变已经冻结的导出（F24 后端部分）。

渲染（`ndr/exports/render.py`）：

- 样式预设 `color_and_label` / `color_only` / `label_only`；颜色来自固定的 8 色板，
  超出时靠编号辨认（编号是真实文本 `〔S1〕`，灰度打印或颜色被覆盖时仍然有效）。
- **未知（UNKNOWN）、未处理、`stale`、`withheld` 的对白保持原样**：不加颜色、不加编号。
- HTML 与 EPUB 共用同一份渲染器与 CSS（样张与实际文件一致）。

产物与安全：

- HTML：单文件，CSS 内联、图片内联为 `data:` URL，无脚本、无 `http(s)` 引用、无 `localhost`（断网可打开）。
- EPUB：`mimetype` 为第一项且**不压缩**，`META-INF/container.xml` + `OEBPS/content.opf`
  + `nav.xhtml` + 每章 XHTML + 共享 CSS + 仅被选中章节引用的图片；zip 时间戳固定，
  相同输入重复导出字节一致。
- 文件写入 `data/exports/<artifact_id>/`，**从不覆盖原书**；重复请求命中相同
  `snapshot_hash + format + style + exporter_version` 指纹时直接复用已有产物。
- 导出只读已存数据与本地资源（EPUB 资源从**源包**里读取），不导入任何 LLM 适配器。

校验（`ndr/exports/validation.py` + `backend/scripts/validate_exports.py`）：

- 内部检查：`non_empty`、`mimetype_first`、`mimetype_stored`、`mimetype_value`、`has_container`、
  `has_opf`、`has_nav`、`readable_zip`、`resource_closure`、`no_external_references`、`no_localhost`、
  `text_consistency`（逐段检查正文是否都出现在导出文本里，缺失片段会列出）。
- 标准检查：EPUBCheck 用**参数数组**调用 `java -jar`；jar 或 java 缺失时状态为 `NOT_RUN` 并说明原因，
  **绝不伪装 PASS**；未提供 jar 生成的产物 `state=COMPLETED`，但报告里如实记录 `standard=NOT_RUN`。
- 命令行：

  ```powershell
  uv run --project backend python backend/scripts/validate_exports.py \
    --epub evaluation/examples/exports/minimal-txt-001-annotated.epub \
    --html evaluation/examples/exports/minimal-txt-001-annotated.html \
    --expected-text-file evaluation/examples/minimal-txt-001/text.txt \
    --epubcheck-jar tools/epubcheck/epubcheck.jar
  ```

  退出码：全部通过 0；内部检查失败或文件缺失 1；用法错误 2。
  `--expected-text-file` 省略时正文一致性记为 `SKIPPED`（仍不假装通过）。

原创样例（`evaluation/examples/exports/`）：由 `minimal-txt-001/text.txt` + 离线确定性 FakeProvider 生成，
含 EPUB/HTML 与 `manifest.json`（快照哈希、大小、sha256、内部检查结果、标准检查状态）。
本机没有 EPUBCheck jar，样例的标准检查如实记录为 `NOT_RUN`。

## 26. 导出界面（T15B 已实现）

入口：阅读页与预览页头部的「导出」按钮（`data-testid="open-export"`）都会打开同一个 `ExportDialog`。

组件：

| 组件 | 职责 |
| --- | --- |
| `ExportDialog` | 编排：范围 → 样式 → 初读策略 → 格式 → 冻结样张 → 生成 → 校验 → 下载 |
| `ExportScopePicker` | 整本 / 指定章节（章节多选；至少选一章才允许预览） |
| `ExportStylePreview` | 颜色 + 编号 / 仅颜色 / 仅编号，并给出内联小样 |
| `ExportProgress` | 任务状态、内部检查逐项（✓/✗）、标准检查状态与缺失片段/资源 |
| `ExportDownload` | 受控下载链接（服务端文件名/MIME）+ sha256 摘要 + 「重复下载不重新生成」 |

界面契约要点：

- **样张来自后端渲染器**：`POST /api/books/{id}/exports/preview` 返回的 `sample_html` 放在
  `sandbox=""` 的 iframe 里展示（不执行脚本），不是阅读页截图。
- **快照过期提示**：对话框在每次打开时**重新冻结**快照；若 `snapshot_hash` 与生成时不同
  （期间有人做了更正），显示「标注已经更新：已生成的文件仍使用旧快照」。
  比较的是**内容哈希**，不是快照行 ID，所以没有变化时不会误报。
- **生成幂等**：同一快照 + 格式 + 样式直接复用后端已有产物；重复点击/重复下载都不会重新打包。
- **失败不提供下载**：`state != COMPLETED` 或内部检查失败时只展示原因（缺失片段/资源），下载入口隐藏。
- **校验状态如实**：EPUBCheck 缺失时显示 `NOT_RUN` 与原因；HTML 显示 `NOT_APPLICABLE`。
- 样式与范围切换会重新冻结快照（样张随之更新），但**不会**触发任何模型调用；导出本身也不调用模型。

## 27. 导出与阅读器的验证状态（T15B）

| 项目 | 状态 | 证据 / 说明 |
| --- | --- | --- |
| 内部结构检查（zip/mimetype/资源闭合/外部引用/正文一致） | PASS | `pytest backend/tests/integration/test_exports.py` + CLI 实跑（样例 `internal=ok`） |
| EPUBCheck 标准检查 | NOT_RUN | 本机没有 `tools/epubcheck/epubcheck.jar`，也没有联网安装；CLI/接口都如实报 `NOT_RUN` |
| HTML 断网打开 | PASS（离线可读） | E2E 下载成品后断言：无 `http(s)` 引用、无 `<script>`、含正文与 `〔S1〕` |
| 独立 EPUB 阅读器试读 ≥ 2 款 | NOT_RUN | 本机未安装任何独立阅读器（无 Calibre / SumatraPDF / Thorium 等），也没有联网安装；**不用浏览器样张代替** |

## 28. 评测工具（T16 已实现，效果数字未产出）

命令（在仓库根目录，`uv run` 或 `backend\.venv` 皆可）：

```powershell
python -m ndr.evaluation validate --manifest evaluation/manifests/dev.json
python -m ndr.evaluation run --manifest evaluation/manifests/dev.json `
    --config evaluation/configs/b0.json --output evaluation/reports/dev-b0-offline.json
python -m ndr.evaluation run --manifest evaluation/manifests/dev.json `
    --config evaluation/configs/b2.json --profile-id <id> --allow-live `
    --output evaluation/reports/dev-b2-live.json
python -m ndr.evaluation loss --manifest evaluation/manifests/dev.json `
    --context-policy context-2 --output evaluation/reports/dev-context-loss.json
```

| 部件 | 说明 |
| --- | --- |
| `ndr.evaluation.manifest` | 清单加载/校验：作品级划分（同一作品只能在一个 split）、金标准结构与引用、作品一致性 |
| `ndr.evaluation.configs` | 配置加载 + 配置指纹（与缓存键同一套规范化哈希） |
| `ndr.evaluation.baselines` | **B0 规则基线**（无 LLM）：显式归属表面形式 + 叙述长度切场景；找不到就拒答 |
| `ndr.evaluation.metrics` | 指标计算（纯函数，手算样例可验证） |
| `ndr.evaluation.live` | 显式允许的真实运行：导入正文 → 建任务 → 引擎 → 读投影（需 `--allow-live` + `--profile-id`） |
| `ndr.evaluation.loss` | 离线证据账（T17）：重放长 Gap 压缩，逐段列出丢掉的行文与是否删到金标准 `must_keep` |
| `ndr.evaluation.runner` | 编排、按作品/难例/总体聚合、写报告 |
| `evaluation/manifests`、`configs`、`reports` | 清单、B0/B1/B2/B3/B4 配置、报告与复现说明 |

指标（DEVELOPMENT 7.3）：

- `extraction.precision/recall/f1`：提取质量（按码点重叠 IoU ≥ 0.5 一对一匹配）。
- `scenes.wrong_split` / `wrong_join` / `accuracy`：错误切断 / 错误连接（相邻金标准对白之间比较）。
- `grouping.accepted_accuracy`：**已接受准确率**（匹配到 + 已接受 + 金标准可确定；拒答不计入分子分母），
  标签映射按金标准场景内最大票数贪心配对（标签置换不变）。
- `grouping.pairwise_precision/recall/f1`：同人 pairwise（只看“是否同组”）。
- `grouping.extra_groups` / `missing_groups`：新人物误建 / 漏建。
- `coverage.coverage` / `refusal_rate`：覆盖率（分母是全部金标准对白，拒答会拉低）。
- `coverage.unknown_force_rate`：**未知强标率**（金标准说不可确定、预测却给了分组）。
- `degenerate`：`all_refusal` / `all_merge` / `single_group` / `forced_all_unresolvable` 显式标记。
- `sample.sample_sufficient`：可确定样本 < `--min-sample`（默认 30）时为 false。
- `targets_met`：样本不足或没有已接受样本时为 `null`（**不宣布达标**）；目标为已接受准确率 ≥97%、覆盖率 ≥70%。
- 报告还记录 `versions`（应用/引擎/提示词/扫描器/上下文策略/金标准 schema）、`config_fingerprint`、
  `usage_total` 与 `usage_unknown_books`（未知用量单独计数）。

真实性边界：

- `quality_evidence` 只有在「真实模型运行 + 每个 book 都 COMPLETED + 提供方不是测试用 FakeProvider +
  样本足够」时才为 true；B0 基线、FakeProvider、外部假预测一律 false，并在 `blocks` 里写明原因。
- `--allow-live` 是**显式开关**；不加就只写 `NOT_RUN` 与原因，绝不偷偷调用模型。
- 真实运行失败（凭据缺失、限流、超时）会记 `LIVE_FAILED` 与错误原因，不回退到假数据。

## 29. 上下文压缩、有限局部复核与成本路由（T17 已实现，默认关闭）

三件事都**可离线验证**，但**是否值得开启**需要真实对比数据（B3/B4）。因此默认配置不变：
`context-1` + `recheck_max_targets=0` + `strong_model_share=0.0`；账本里如实写「优化未验证」。

### 29.1 版本化上下文策略

- `context-1`（默认）：现状，完整 Gap 必留。`context-2`：长 Gap 保守筛选（见 §17 的 T17 字段）。
- 只在「Gap 宽度 > `gap_compression_threshold_cp`（默认 80 码点）」时筛选；短 Gap 原样保留。
- 保留规则：命中归属线索的句子（引号标记或 `说/道/问/答/喊/叫/…` 线索词）必须保留，
  其前后各补 `gap_compression_margin_sentences`（默认 1）句；首句与末句一定保留。
- **省不下来就不压缩**：保留比例 > `gap_compression_max_ratio`（默认 0.6）时整体保留，并写
  `warnings: gap_compression_skipped:insufficient_savings:<gap_id>`。
- 丢掉的行文逐段写入窗口 `omitted`（`reason=gap_compression`，含 `start_cp/end_cp/tokens`）：
  保留 + 丢弃恰好覆盖整个 Gap，既不跳过原文也不编造内容（单测逐段核对）。
- 策略版本进入窗口 ID、依赖哈希与缓存键（§17），换策略不会复用旧缓存；任务范围用
  `context_policy` 选择版本，缺省/未知一律回落 `context-1`。

### 29.2 有限局部复核（`recheck_max_targets`）

- 触发条件：该窗口首次结果里仍有**未解决**的目标（`status=UNKNOWN` 且未被用户锁定）。
- 上限：每个窗口最多 `recheck_max_targets` 条（默认 0 = 不复核）；目标按窗口顺序取交集后截断。
- 复核窗口一律**用保守策略重建**，把压缩阶段丢掉的句子补回来（`restore_evidence`）；
  它有自己的窗口 ID / 依赖哈希 / 缓存键。
- 首次结果与复核各写一条 `inference_runs`，用量按每次尝试结算（不是「只统计最后一次调用」）；
  复核窗口**不写** `job_windows`，窗口级「一次有效提交」的语义不变。
- 复核失败不改变首次结果；复核**超时**按 F15 处理：标 `UNKNOWN_OUTCOME` + 任务
  `NEEDS_RECONCILIATION`，不自动重发。

### 29.3 成本路由（`strong_model_share`）

- 任务范围可给出 `strong_profile_id`（`range` 是自由字段，不新增端点、不改 OpenAPI）；
  没有它时路由不可用，**如实退回基础模型**并把原因记进运行结果。
- 上限：`floor(strong_model_share × 计划窗口数)`；默认 0.0 = 关闭。
- 只升级**困难窗口**：丢过证据（`omitted` 非空）、有超长目标、目标 ≥4 条，或需要复核。
- 审计：每次尝试的 `inference_runs.profile_snapshot_json` 记录实际使用的模型配置；
  `GET /api/books/{id}/usage` 的 `by_model` 也按**每次尝试自己的快照**归属。
- 任务 `progress_json` 如实给出 `strong_windows` / `recheck_windows` / `recheck_targets` / `recheck_calls`。

### 29.4 评测与证据

- `evaluation/configs/b3.json`（context-2，`max_rechecks=0`）与 `b4.json`（context-2，`max_rechecks=3`）：
  同一清单、同一模型配置下只改上下文/复核参数，用 `python -m ndr.evaluation run … --allow-live` 跑对比。
- `python -m ndr.evaluation loss --manifest … --context-policy context-2`：**离线证据账**，
  逐段列出压缩丢掉的行文，并标记是否与金标准 `gaps[].must_keep` 重叠（删错计数）。
- `evaluation/ablations.md`：消融说明、判定门槛与当前状态。**当前状态 = 未验证**：
  没有真实凭据与人工确认样本，B3/B4 的准确率—覆盖率—费用对比无法产出。