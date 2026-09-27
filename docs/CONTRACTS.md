# 公共契约摘要（NDR）

本文件是 DEVELOPMENT.md 第 3～6 节的可执行摘要，供前后端与后续任务对齐。
**实现状态以 [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) 为准**：T00 只实现了
`GET /api/health`，本文件其余条目是已冻结的契约，由后续任务逐项实现。

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
| 健康检查数据库 | `READY / NOT_INITIALIZED / ERROR` |

`preview` 与 `process` 是任务目的，不是两套识别引擎：语义输入相同必须命中同一缓存。

## 4. 端点清单与实现任务

| 端点 | 契约摘要 | 实现任务 |
| --- | --- | --- |
| `GET /api/health` | 进程、数据库与版本状态；不调用模型 | **T00 已实现** |
| `POST /api/books/import` | multipart file + encoding；返回 book_id 与 IMPORT job_id | T02 |
| `GET /api/books`、`GET /api/books/{id}` | 元数据、导入状态、当前版本 | T02 |
| `GET /api/books/{id}/chapters` | 按 ordinal 的目录与可定位范围 | T02/T03 |
| `PUT /api/books/{id}/reading-progress` | 保存阅读位置，不调用模型 | T04 |
| `GET /api/books/{id}/content` | chapter/range + horizon，返回结构化节点 | T04 |
| `GET /api/books/{id}/resources/{resource_id}` | 受控登记资源 | T03 |
| `GET/POST/PATCH/DELETE /api/model-profiles*` | 非敏感配置与 has_key；keep/replace/remove 密钥 | T06 |
| `POST /api/model-profiles/test` | 有预算的微型连接测试 | T07 |
| `POST /api/books/{id}/estimates` | 纯本地估算，说明依据 | T08 |
| `POST /api/jobs`、`GET /api/jobs/{id}`、`pause`/`resume`/`reconcile` | 幂等创建、检查点、usage、恢复 | T10/T14 |
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
