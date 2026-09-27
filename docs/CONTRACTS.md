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
| `PUT /api/books/{id}/reading-progress` | 保存阅读位置，不调用模型 | T04 |
| `GET /api/books/{id}/content` | 结构化正文节点 + `payload`（章节或码点范围；horizon 见 T04/T15） | **T02/T03 已实现**（范围部分） |
| `GET /api/books/{id}/resources/{resource_id}` | 受控登记资源（图片等，独立响应体） | **T03 已实现** |
| `GET/POST/PATCH/DELETE /api/model-profiles*` | 非敏感配置与 has_key；keep/replace/remove 密钥 | T06 |
| `POST /api/model-profiles/test` | 有预算的微型连接测试 | T07 |
| `POST /api/books/{id}/estimates` | 纯本地估算，说明依据 | T08 |
| `GET /api/jobs/{id}` | 任务状态、进度与错误 | **T02 已实现**（最小轮询；调度/usage/恢复见 T10/T14） |
| `GET /api/books/{id}/annotations` | 有效投影与图例，受 horizon 与阅读模式约束 | T11/T15 |
| `GET /api/books/{id}/review-items`、`GET /api/review-items/{id}` | 待确认队列与详情 | T13 |
| `GET /api/quotes/{id}`、`POST /api/quotes/{id}/review-items` | 普通对白详情与主动标记 | T12/T13 |
| `POST /api/quotes/{id}/corrections`、`POST /api/gaps/{id}/corrections` | 人工更正，不调用模型 | T12 |
| `POST /api/review-items/{id}/defer`、`POST /api/corrections/{id}/undo` | 延后与撤销（版本校验） | T12/T13 |
| `POST /api/scenes/{id}/speaker-revisions` | merge/split 与影响范围 | T12 |
| `POST /api/quotes/{id}/recheck` | 有上限的局部复核 | T14 |
| `GET /api/books/{id}/usage` | 按任务/阶段/模型汇总，含未知用量标记 | T10 |
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