# 开发执行文档：轻小说对话辅助阅读器

版本：1.1 · 日期：2026-09-28  
项目根目录：`F:\Code\novel-dialogue-reader`  
需求来源：[PLAN.md](PLAN.md)  
用途：供后续 AI 按顺序实现、验证、记录和交接。本文是开发规格，不表示代码、命令、接口或效果已经实现。

## 0. 给接手 AI 的执行规则

### 0.1 先读什么

1. 阅读用户最新指令、适用的 AGENTS.md、PLAN.md 和本文。
2. 检查真实文件、Git 状态（仅当仓库已存在）、正在运行的服务与已有实现，不凭文档假设项目仍为空。
3. 阅读 `docs/IMPLEMENTATION_STATUS.md` 与 `docs/HANDOFF.md`；首次执行时由 T00 创建。
4. 按第 8 节任务顺序，选择前置条件满足的第一个未完成任务。用户指定任务时先检查其依赖。

用户最新要求决定需求；PLAN.md 说明产品目标，本文细化实现。发现冲突时记录并修正相关契约，不擅自删减 TXT/EPUB、API 配置、真实预览、待定确认或 EPUB/HTML 导出功能来让检查通过。

### 0.2 每次任务的执行协议

- 开始前说明任务 ID、目标和影响模块。
- 完成一个可验证的切片，再推进下一项；用户只指定一项时完成该项及必要修复，不顺带重写全部项目。
- 使用已有代码和依赖，避免重复搭框架。修改公共契约时同步模型、迁移、API 类型、调用方和测试。
- 常规实现细节按本文自行决定；不要对每个可逆操作反复请求确认。
- 没有真实小说或 API 凭据时，继续完成不依赖它们的工程任务；明确记录未验证项，不编造真人标注、模型结果或准确率。
- 自动测试默认使用可控 FakeProvider；只有明确启用真实提供方且已有凭据与预算时才发起付费调用。绝不把用户密钥写进交接文档。
- 不自动部署、上传小说、公开仓库或发起无范围上限的全书调用。
- 同步更新进度、验证证据、已知缺陷和下一任务；不要只用一句“已完成”交接。

### 0.3 四种完成状态必须分开

每项任务记录：`implementation`（实现）、`offline_verification`（离线验证）、`live_verification`（真实服务/阅读器验证）、`quality_evaluation`（真实作品效果）。取值为 `NOT_STARTED / IN_PROGRESS / PASS / FAIL / BLOCKED / NOT_APPLICABLE`。

缺少凭据时，代码与离线验证可以通过，真实验证必须保持 BLOCKED。FakeProvider 的成功不能证明真实模型兼容性，人工编写样例不能证明跨作品准确率。

### 0.4 本次文档范围

目前新增此文件作为执行指南；后文出现的目录、接口和命令均是后续任务需要建立的目标。开始执行 T00 后才生成代码工程、运行脚本和进度文档。

## 1. 首版交付与不可破坏的约束

### 1.1 用户必须能走通的流程

导入 TXT/EPUB → 浏览原文和目录 → 输入 LLM 地址、Key、模型并测试 → 选择少量文字真实试标注 → 对比原文和着色 → 处理不确定对白 → 按章继续阅读 → 暂停/恢复任务与保存纠正 → 选择范围与样式 → 导出 EPUB/HTML 文件。

无模型凭据时仍能导入、阅读和导出现有结果；手动确认、换色、重读和导出不能隐式调用付费模型。

### 1.2 功能编号

| 编号 | 必须交付的能力 | 对应任务 |
| --- | --- | --- |
| R01 | TXT 编码、EPUB 目录/spine、资源与位置映射 | T02、T03、T04 |
| R02 | API 配置、密钥策略、连接测试 | T06、T07 |
| R03 | 真实效果预览、原文对比、缓存复用 | T10、T11 |
| R04 | 待确认队列、上下文、人工确认和撤销 | T12、T13 |
| R05 | 叙述穿插不硬切对话，长场景跨窗口延续 | T05、T08、T09 |
| R06 | EXISTING / NEW / UNKNOWN 与匿名分组 | T07、T09、T12 |
| R07 | token 预算、有限复核、用量与失败恢复 | T08、T10、T14、T17 |
| R08 | 原文不可变、映射正确、人工确认优先 | T01、T03、T12、T15 |
| R09 | 阅读进度控制后文证据展示 | T08、T09、T15 |
| R10 | 离线回归、真实联调、独立作品评测和交接 | T00、T16、T18、T19 |
| R11 | EPUB/HTML 带格式导出、快照、静态初读策略与格式校验 | T15A、T15B、T18、T19 |

### 1.3 不变量

- Scene、Window、Chapter、页面是四种不同边界。
- 新段落、心理描写、固定字数阈值、新人物出现都不自动关闭 Scene。
- 无法判断不能自动创建新人；UNKNOWN 之间不能视作同一个人。
- 稳定内部 ID 与展示 S1/S2 分离；不以姓名、语气或地点字符串作为身份主键。
- LLM 不负责重写原文、不负责给出字符偏移、不负责直接操作数据库。
- 用户确认不能被较晚到达的自动结果覆盖。
- preview/process 是任务目的，不是不同识别引擎；语义输入相同必须复用结果。
- 自动标注与人工修订都保留版本，支持证据展示时点和撤销。
- 导出只读取不可变原文与冻结标注投影，不调用模型，不覆盖原文件，不因未处理或待定而删除正文。
- 统计所有真实调用，包括测试连接、格式重试、局部复核；usage 缺失不是零费用。
- 不声称对远程模型实现了绝对的“恰好调用一次”，未知网络结果需单独处理。

## 2. 技术基线、结构和运行契约

### 2.1 默认选型

- 后端：Python 3.12 兼容基线，FastAPI、Pydantic、SQLAlchemy 2 风格、Alembic、SQLite、httpx。
- 前端：React、TypeScript、Vite、React Router、TanStack Query；普通 CSS 或 CSS Modules，不引入大型组件体系作为前置条件。
- 本地凭据：keyring 的系统凭据后端；不可用时只保留会话密钥，不明文降级。
- 解析：标准库 ZIP/XML 能力或经确认适用的解析库；字符编码候选可使用 charset-normalizer，不能无提示替换解码失败字符。
- 测试：pytest、Ruff、Vitest、React Testing Library、Playwright；评测可用 scipy 的匹配算法。
- 包管理：后端 uv 与 `uv.lock`，前端 npm 与 `package-lock.json`。开发时检查运行环境与依赖官方说明，锁定相互兼容的版本；本文不宣称它们是最新版本。
- 本地服务默认 `127.0.0.1:8765`；开发前端默认 `127.0.0.1:5173` 并代理 `/api`。交付模式由后端提供构建后的前端，同源运行。

### 2.2 目标目录

```text
novel-dialogue-reader/
  PLAN.md
  DEVELOPMENT.md
  README.md
  .gitignore
  backend/
    pyproject.toml
    uv.lock
    alembic.ini
    migrations/
    src/ndr/
      __main__.py
      app.py
      config.py
      domain/         # 枚举、约束、schema
      ingest/         # TXT/EPUB、标准文档树、映射
      exports/        # 快照、投影、EPUB/HTML、样式、格式检查与下载
      quotes/         # 引号扫描、Gap、发言片段
      scenes/         # 场景状态与边界
      context/        # 窗口、证据选择、预算
      speakers/       # 匿名分组、身份修订
      llm/            # adapter、prompt、解析与校验
      jobs/           # 调度、检查点、usage 与恢复
      storage/        # ORM、事务、缓存、凭据
      api/            # HTTP 路由
    scripts/export_openapi.py
    tests/{unit,integration,fixtures}/
  frontend/
    package.json
    package-lock.json
    src/{pages,components,api,state,styles}/
    tests/
    e2e/
  evaluation/
    annotation-guide.md
    schemas/
    examples/
    reports/
  docs/
    CONTRACTS.md
    openapi.json
    IMPLEMENTATION_STATUS.md
    HANDOFF.md
    decisions/
  scripts/
    dev.ps1
    verify.ps1
  data/               # 原文、SQLite、解析资源；不提交
```

模块职责沿用 PLAN.md，Python 实际包统一在 `backend/src/ndr/`。应用不能通过当前 shell 工作目录猜测 data 路径，使用显式配置或根据配置文件解析的绝对路径。

### 2.3 必须建立的命令

以下均从项目根目录运行；T00 建立基础命令，相关功能任务补齐对应脚本。命令不存在时先实现，不声称已通过。

```powershell
uv sync --project backend --all-groups
npm --prefix frontend ci
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend python -m ndr
npm --prefix frontend run dev -- --host 127.0.0.1
uv run --project backend ruff check backend/src backend/tests
uv run --project backend pytest backend/tests
npm --prefix frontend run typecheck
npm --prefix frontend run test -- --run
npm --prefix frontend run build
npm --prefix frontend run test:e2e
uv run --project backend python backend/scripts/export_openapi.py --output docs/openapi.json
npm --prefix frontend run generate:api
```

首次初始化没有锁文件时先正常解析并生成锁文件，之后使用 `npm ci`。Alembic 的 script_location 根据配置文件自身路径解析。E2E 测试启动独立数据目录、FakeProvider 和测试端口，不能操作用户书库。

`dev.ps1` 检查依赖、迁移并启动服务，打印访问地址与停止方法；创建后台进程时使用隐藏窗口，退出时只处理本脚本启动的进程。`verify.ps1` 只运行已建立的相关检查，失败返回非零，不吞掉错误。

## 3. 统一数据与事务契约

### 3.1 约定

- API JSON 统一 snake_case，UTF-8，时间为 UTC ISO 8601；前端展示时转本地时区。
- ID 为不透明字符串；数据库可使用 UUID。Quote ID 由原文版本、位置、扫描器版本稳定派生，不能依赖模型生成。
- 字符坐标均为对应书籍版本的规范化全文 Unicode 码点区间 `[start_cp, end_cp)`。
- enum/schema 由后端权威定义，通过 OpenAPI 生成前端类型，不维护互相漂移的两份枚举。
- 可变实体有递增 `version`，写请求携带期望版本；本地文件、资源路径不向客户端暴露任意磁盘访问能力。
- 金额采用整数最小单位或 Decimal 字符串并附 currency；缺价格时只给 token，不伪造金额。

### 3.2 核心数据表

通用字段为 `id, created_at, updated_at`。下表是首版最小逻辑结构，可拆分表，但语义和约束必须保持。

| 表 | 必需业务字段 | 约束 / 索引 |
| --- | --- | --- |
| books | title, format, source_sha256, active_version_id, import_status, read_position_cp | 格式限定 TXT/EPUB；导入状态与推理状态分离 |
| book_versions | book_id, source_path, encoding, parser_version, normalization_version, canonical_sha256, canonical_length_cp, warnings | 原文版本不可变；更换编码形成新版本 |
| chapters | book_version_id, ordinal, title, start_cp, end_cp, source_href | ordinal 唯一且按阅读顺序 |
| content_nodes | chapter_id, node_id, node_type, tree_json, start_cp, end_cp | 节点定位可回溯；非正文节点可无正文范围 |
| resources | book_version_id, resource_id, media_type, relative_path, sha256 | 只读取登记且在书籍目录内的资源 |
| quotes | book_version_id, chapter_id, start_cp, end_cp, delimiter, nesting_depth, parent_quote_id, utterance_id, scanner_version | 范围合法；索引版本+位置 |
| gaps | book_version_id, left_quote_id, right_quote_id, start_cp, end_cp, proposed_decision | 允许跨章节，不能只按章节关闭 |
| scenes | book_version_id, start_cp, end_cp, status, version | OPEN/PENDING_BOUNDARY/CLOSED；边界变更有历史 |
| scene_memberships | quote_id, scene_id, valid_from_revision, valid_to_revision | 场景修订不直接丢弃历史 |
| participants | scene_id, description, mention_refs, speaker_id | 未发言可无 speaker_id |
| speaker_groups | scene_id, first_quote_id, display_label, evidence_refs, version | 所属场景与有效引用一致 |
| annotations | quote_id, scene_id, kind, speaker_id, status, source, evidence_refs, visible_from_cp, version, dependency_hash, stale, user_locked | 当前投影；每个 quote 唯一当前版本 |
| annotation_history | annotation_id, revision, snapshot_json, visible_from_cp, run_id, correction_id | 保存旧结果，支持撤销与按证据时点展示 |
| identity_revisions | scene_id, operation, input_ids, output_ids, snapshot_json, evidence_refs, visible_from_cp, version | merge/split 可撤销，不破坏历史投影 |
| review_items | quote_id/gap_id, reason, candidates_json, queue_status, annotation_version | 目标恰好一种；同一当前问题不重复建项 |
| corrections | target_type, target_id, action, before_json, after_json, expected_version, applied_version, undone_by | 记录用户更正，撤销也留痕 |
| model_profiles | name, protocol, base_url, model, params_json, credential_mode, credential_ref, version | 不存明文 Key，不含前端任意脚本 |
| jobs | kind, book_id, range_json, profile_snapshot, state, budget_json, checkpoint_json, idempotency_key, last_error | 幂等键+请求摘要约束；凭据只保存引用 |
| job_windows | job_id, window_id, target_ids, dependency_hash, state, run_id, lease_until | 同窗口只产生一次有效提交 |
| inference_runs | job_id, profile_snapshot, request_fingerprint, remote_request_id, state, usage_json, elapsed_ms, error_code | 包含每次尝试；不可把未知用量写成 0 |
| result_cache | cache_key, schema_version, result_json, dependency_hash, created_run_id | cache_key 唯一；结果不包含用户密钥 |
| export_snapshots | book_version_id, selected_chapter_ids, source_revision, annotation_projection_json, identity_projection_json, visibility_policy, style_json, snapshot_hash, warnings_json | 在同一读事务冻结；保存所用 revision，不在导出中重新取最新标注 |
| export_artifacts | snapshot_id, format, options_json, exporter_version, fingerprint, job_id, state, relative_path, file_sha256, byte_size, validation_json | 受控目录；相同 fingerprint 可复用；从不覆盖原书 |

关系引用使用外键；SQLite 启用 foreign_keys 与适当的 busy_timeout。单进程调度可用 WAL；事务期间不能等待网络响应。

### 3.3 状态与类型

- Scene：`OPEN / PENDING_BOUNDARY / CLOSED`。
- Gap：`CONTINUE / UPDATE / BREAK / UNCERTAIN`。
- Quote kind：`speech / thought / quotation / group / other / unknown`。
- 普通 speech assignment：`EXISTING / NEW / UNKNOWN`；其他类型 assignment 为 null，不强行指定普通说话人。
- Annotation：`PROVISIONAL / ACCEPTED / USER_CONFIRMED / UNKNOWN`，另有独立 stale 标志。
- Review queue：`PENDING / DEFERRED / RESOLVED`，不能用队列状态代替标注准确性。
- Job kind：`IMPORT / INFERENCE / RECHECK / RECOMPUTE / EXPORT`；INFERENCE 的 purpose 为 `preview/process`。
- Job state：`QUEUED / RUNNING / PAUSING / PAUSED / PARTIAL / COMPLETED / FAILED / BUDGET_EXHAUSTED / NEEDS_RECONCILIATION`。
- Inference run：`PREPARED / DISPATCHED / SUCCEEDED / FAILED / UNKNOWN_OUTCOME`。

### 3.4 事务边界

自动结果提交：验证输入依赖未变 → 检查用户锁定 → 插入历史 → 更新有效标注与场景 → 更新 review/cache → 保存窗口检查点 → 一次事务提交。

人工更正：校验版本 → 保存前值 → 更新分组/标注并锁定 → 更新队列 → 标记下游 stale → 返回受影响范围。此事务不发起 LLM。

网络返回前若用户已确认同一对白，自动结果只能进入历史或被丢弃，不能覆盖当前人工结果。撤销要求目标仍是适用的最新版本，否则返回版本冲突并保留数据，不执行盲目回滚。

## 4. 核心算法与文档映射的实现规格

### 4.1 导入到统一文档树

所有格式输出同一 `CanonicalDocument`：书籍版本、按顺序的 chapters、可渲染节点树、规范化全文、source_map、资源清单与解析警告。后续算法不直接读取 TXT 字节或 EPUB 压缩包。

TXT：显式编码优先 → 解码验证 → 统一换行并记录映射 → 保留段落 → 提取可信章节标题。猜测编码不可靠时给用户预览选择，不通过 errors=replace 静默丢字。

EPUB：读取 container/OPF → 按 spine 读取正文 → 目录关联 → 解析节点 → 登记图片 → 提取正文文本和 source_map。插图、ruby/rb/rt/rp 保留为受限节点类型；rt/rp 注音不重复进入模型正文。块间合成换行要标记为 synthetic，不假装它存在于原文件。

每个文本映射段至少保存：`canonical_start_cp, canonical_end_cp, chapter_id, node_id, source_href, source_text_start_cp, source_text_end_cp, synthetic`。清理与渲染后不得重新按字符串搜索定位对白。

前端渲染结构化节点树，文字节点按标注边界分片；跨多个节点的同一对白共享 quote_id，不能插入跨 HTML 块的不合法 span。每个叶节点建立码点到 UTF-16 的映射，不能用 JavaScript 字符串 length 当作码点长度。

ZIP 导入检查归一化路径不越出书籍目录，限制条目数量与解压总量，拒绝通过符号链接或资源路径读取项目外文件。EPUB 内容只输出允许的节点和资源，不执行原书脚本或主动加载外部 URL。限制值可配置并返回具体错误，不以异常吞掉整本书。

### 4.2 引语和 Gap

扫描器只提出候选，不把所有引号内容判成 speech。保存嵌套关系和 delimiter，跨段未闭合情况输出警告；通过长度保护防止异常引号吞掉全章，但保护不代表场景断开。

Gap 以当前叙述层的相邻外层候选对白为锚，包含中间叙述。嵌套引用不自动成为当前场景的新发言轮次。多个 quote 可引用同一 utterance，只有存在可靠依据才合并，不能仅按相邻位置合并。

解析器不负责轮流分配人物，也不以人名数量估算说话人数。

### 4.3 构造处理窗口

```text
读取待处理 quote 和上一个可复用场景状态
确定合法证据范围（书籍版本、阅读模式、visible_horizon）
沿文档顺序加入完整引语及 Gap
加入必要人物状态和少量重叠；引用 ID 必须能映射到原文
若超预算：先去可选上下文，再缩小目标范围，不能截断目标发言
若一段目标仍放不下：单独处理或保留待定并报告 oversized_quote
输出目标 ID、上下文片段、截取/省略记录、token 估算和依赖摘要
```

初始参数沿用 PLAN.md：正文 1,500～3,000 token，重叠 100～300 token，复核按需扩大。输入必须给提示、人物状态和输出留空间。Gap 的压缩默认关闭；T17 根据真实对比实验开启。

目标对白正文只在上下文中发送一次；目标列表只列 ID。片段可包含可逆标记，如 `<q id="q101">…</q>`，原文中的特殊符号必须正确转义。省略片段使用独立结构字段描述，不让小说内容伪装成系统标记。

### 4.4 LLM 输入与完整逻辑输出

输入包括：schema_version、固定任务说明、目标引语 IDs、允许的上下文片段、当前 scene_ref、已有场景/人物引用、Gap IDs、已锁定结果摘要及未解决问题。允许引用的 ID 集合由程序生成。

输出的逻辑字段如下；实现为 Pydantic 判别联合和 JSON Schema。示例仅演示当前场景中已有角色和新增声音：

```json
{
  "schema_version": "1.0",
  "scene_updates": [],
  "gap_decisions": [
    {"gap_id": "g102", "decision": "UPDATE", "evidence_refs": ["p12"]}
  ],
  "new_speakers": [
    {"temp_ref": "new1", "scene_ref": "scene_current", "first_quote_id": "q102", "description": "门外的声音", "evidence_refs": ["p12"]}
  ],
  "labels": [
    {"quote_id": "q101", "scene_ref": "scene_current", "kind": "speech", "assignment": "EXISTING", "speaker_ref": "speaker_a", "basis": "DIRECT", "evidence_refs": ["p10"]},
    {"quote_id": "q102", "scene_ref": "scene_current", "kind": "speech", "assignment": "NEW", "speaker_ref": "new1", "basis": "DIRECT", "evidence_refs": ["p12"]},
    {"quote_id": "q103", "scene_ref": "scene_current", "kind": "speech", "assignment": "UNKNOWN", "speaker_ref": null, "basis": "INSUFFICIENT", "evidence_refs": []}
  ],
  "identity_proposals": [],
  "needs_context": ["q103"]
}
```

补充定义：

- 新场景通过 `scene_updates` 中 `{temp_ref, after_gap_id, starts_at_quote_id, evidence_refs}` 声明；after_gap_id 必须有 BREAK 决策，所有标签引用必须唯一可解析。
- UPDATE 不产生新场景；UNCERTAIN 不强制切开，保留待定边界和证据需求。
- new_speakers 的 temp_ref 在单次输出唯一，映射持久 ID 时按首次发言位置排序。
- 非 speech 的 assignment 和 speaker_ref 为 null；group 可以提供参与候选，但不能伪装成单个普通人物。
- basis 枚举：`DIRECT / COREFERENCE / RESPONSE_LINK / STYLE_ONLY / INSUFFICIENT`。这是证据类型，不是校准后的置信概率。
- identity_proposals 包含 merge/split 候选、分组引用和证据，不直接改数据库。自动应用只限明确证据且不触碰人工锁定的情况，否则进入待确认。
- evidence_refs 可以是输入中的段落/引语 ID 或已验证事实 ID；不能引用没发送的全书内容。

首个窗口没有任何已知说话人时，允许为第一次可区分的发言建立一个局部锚点分组；这是创建第一个分组，不证明后续每句都是新人。后续 NEW 仍需要不同人的证据。

### 4.5 模型输出的接受顺序

1. 解析 JSON/schema；禁止执行输出代码或从长文本中随意截取可能的对象。
2. 验证目标覆盖、唯一 ID、场景与分组一致性、临时引用和证据存在性。
3. 校验锁定标注与当前依赖版本。
4. 计算每项结果的可见证据时点；模型自报的 visible_from 不可信，由后端按证据和继承事实计算。
5. 按验证集校准的策略区分 ACCEPTED / PROVISIONAL / UNKNOWN。
6. 提交事务，或只为问题局部请求更多上下文。

冷启动采用保守策略：直接且无冲突的归属优先接受，风格单一依据不接受；其他类别先保持暂定并记录。不能把“模型返回合法 JSON”当作高准确率，也不能为达到覆盖率目标而无评测地放宽规则。

### 4.6 状态延续、修订和重算

下一窗口仅携带可靠场景状态与必要暂定提示；后者明确标记为待定。最后发言者、参与者和叙述视角是不同字段，不能互相替代。

确有 BREAK 时结束前场景并创建新场景。靠近窗口尾的边界可暂定，下一窗口补证据；处理上限本身不触发 BREAK。章节结束允许保存开放状态，不能只因进入下一章强制重置人物。

自动 merge/split 与人工修订都使用不可变历史与当前投影。不要用不可撤销的并查集覆盖全部过去身份，否则无法撤销或按阅读进度展示。

更正后从受影响依赖向前重算；若状态重新与已有检查点一致，可终止传播。未经证明的缓存不得复用。用户锁定结果作为后续约束，必要时其他自动结果变为待定，而不是修改锁定结果。

### 4.7 带格式导出引擎

首版实现 EPUB 3 和离线单文件 HTML。导出使用后端模板生成真实文本与 CSS，不截图、不调用 LLM，也不依赖应用正在运行。TXT 与 EPUB 都先进入 CanonicalDocument，因此四条输入/输出组合共用一个导出投影层。

#### 4.7.1 冻结快照与有效标注

1. 在一致数据库读事务中读取不可变 book_version、所选章节、有效 annotation/identity revisions、人物样式和证据策略，生成完整快照；之后结束事务再生成文件。
2. 只附加未过期的 ACCEPTED 与 USER_CONFIRMED 结果；UNKNOWN、PROVISIONAL、stale 与未处理对白保留原文。人工锁定优先，锁定未知不变成已确定人物。
3. 导出范围以整本或明确章节列表为首版单位；默认保留范围内所有正文，而不是仅导出已识别对白。
4. 冻结未标注数、过期数、有效数及章节范围，提供给 UI。所选范围含未处理内容时明确显示覆盖情况，不能自动调用模型补齐。
5. 预览后发生纠正时，已有 snapshot 保持有效但内容不变；提示用户刷新快照再导出最新结果，不把新旧人物状态混装。

缓存 fingerprint 包括快照内容哈希、格式、样式与导出器版本，不包括 job_id、下载次数和只用于显示的创建时间。相同内容命中已有成品时返回其真实生成时间，不伪造新时间。

#### 4.7.2 静态初读与重读

静态文件不能在翻页时请求应用后端更新身份，必须预先决定每个位置的投影：

- `position_safe`（默认）：每段对白采用该段所在原文段落结束时可成立的证据；不能直接把全书结束时的身份合并套回前文。晚于该位置的证据不足以支持当前着色时保留原样。
- `reread`：显式允许使用当前快照中的后文确认结果，仍保持场景内编号规则。
- 用户确认若来自后文，同样尊重其 visible_from。缺少有效证据时点的人工身份合并在 position_safe 下保守不回填；更正记录应保存操作时可见上下文的上界。
- 生成的编号、图例与人物称呼均使用相同投影；不在书首或场景首列出未来才出现的人物信息。默认用局部编号，不做全书人物关系表。
- 不把当前阅读位置当作整本导出的固定 horizon；position_safe 是逐原文位置计算，用户可导出完整书籍而保持局部证据时点。

#### 4.7.3 XHTML 与 HTML 共用渲染

- 复用 CanonicalDocument、source_map 和 quote spans，在叶文本节点边界拆分并附加 span；跨节点、ruby 和跨段对白用多个 span 关联同一发言，不拼出不合法 HTML。
- 样式至少支持 `color_and_label`、`color_only`、`label_only`；默认颜色加编号。每次发言的可见编号只插入一次，不在跨节点片段中重复。
- 编号是实际文本节点（如 `〔S1〕`），不依赖 CSS 伪元素、title 或脚本，便于颜色被覆盖或灰度显示时仍能辨认。
- 固定的 `ndr-*` class 与基础 CSS 输出明确颜色值，不依赖应用主题变量、外部字体、动画或运行时 JS。允许阅读器改变字号与行距，不锁定整页背景和固定尺寸。
- 原文字符、标点和段落顺序不改写。新增编号带专用 class，校验时可剔除辅助标记再比较规范化正文。
- 选定章节外的普通正文链接不得成为坏的包内引用；可显示目标说明。脚注等必要辅助资源按引用收集，不擅自把未选择的其他正文整章加入。

#### 4.7.4 EPUB 生成

目标为结构正确的 EPUB 3 重排出版物，按 [W3C EPUB 3.3](https://www.w3.org/TR/epub-33/) 核验包格式。OPF 的 package version 使用 EPUB 3 的 `3.0`，不把规格名 `3.3` 直接写入该字段。

```text
mimetype
META-INF/container.xml
EPUB/package.opf
EPUB/nav.xhtml
EPUB/styles/annotations.css
EPUB/text/chapter-0001.xhtml
EPUB/images/...
```

`mimetype` 必须是 ZIP 第一项、未压缩，内容精确为 `application/epub+zip`，无 BOM、空白或 ZIP extra field。OPF 声明唯一标识、书名、语言、修改时间、manifest 与正确顺序的 spine；nav.xhtml 登记为导航文档。所有包内 href、锚点和媒体类型必须可解析，XHTML/XML 必须正确转义。[OCF 要求](https://www.w3.org/TR/epub-33/)

- TXT：依据已有章节生成 XHTML、目录和基础元数据，不虚构作者。
- EPUB：保留可用书名、作者、语言、封面、章节、插图、基本注音与脚注；派生文件生成新 identifier 并记录来源关联，不覆盖原文件。
- 首版使用受控的基础排版，不承诺字节级复制原 EPUB 或保留所有出版方 CSS。现有结构中无法可靠导出的资源要报告，不静默丢失。
- 资源重定位同时更新引用；只打包已登记的本地资源，不趁导出下载网络内容，不将 API 配置、日志和数据库放进书内。

#### 4.7.5 单文件 HTML

生成带 UTF-8 声明、目录锚点、基础样式和全文的独立 HTML；图片按支持的媒体类型内嵌 data URL，CSS 内嵌。既无 localhost 链接，也不依赖应用后端或 JavaScript 才能显示标注。

估算内嵌资源膨胀并设置可配置文件大小上限，超过上限明确提示改用 EPUB 或减少章节，不能静默移除图片。资源缺失、编码失败或不支持的媒体类型给出具体错误/降级报告。

#### 4.7.6 校验、成品与兼容性

先生成到受控临时路径 → 校验正文、目录、链接、资源与格式 → 成功后原子发布到新的 artifact 路径 → 登记哈希与字节数。失败不覆盖原书或此前成功文件；下载只接受数据库中已完成的 export_id，不接受客户端任意磁盘路径。

`validation_json` 分别记录内部检查与标准检查；EPUB 使用固定版本的 [EPUBCheck](https://www.w3.org/publishing/epubcheck/) 并保存结果。工具未安装或无法运行时记录 NOT_RUN，界面标明“标准校验未完成”；不能将其当作 PASS。内部或标准检查实际报错时标记 FAILED，不作为成功成品下载。发布验收要求 EPUBCheck 通过，警告逐项处理或解释。

正式验收至少在两款独立 EPUB 阅读器中打开，并记录产品版本、颜色/编号/目录/图片/ruby 的表现；HTML 在断网条件下打开。阅读器可根据用户设置覆盖作者样式，编号是兼容性补充，不保证所有设备颜色一致。[阅读系统 CSS 规则](https://www.w3.org/TR/epub-rs-33/)

## 5. HTTP、模型接入与任务契约

### 5.1 公共 HTTP 约定

- 前缀 `/api`；分页使用 `cursor/limit`，列表返回 `items/next_cursor`。
- 普通成功：`{"data": ..., "request_id": "..."}`；文件资源和健康检查可采用明确的独立响应。
- 错误：`{"error":{"code":"VERSION_CONFLICT","message":"...","details":{}},"request_id":"..."}`。
- 新建资源 201；异步任务 202；参数错误 422；版本/幂等冲突 409；缺资源 404；超限文件 413；不支持格式 415。
- 上游错误作为稳定业务错误码返回，如 `PROVIDER_AUTH_FAILED / MODEL_NOT_FOUND / RATE_LIMITED / PROVIDER_TIMEOUT / INVALID_MODEL_OUTPUT`，不把上游原始密钥和响应全部透传。
- 本地应用默认只监听回环地址；开发 CORS 仅允许配置的本地前端来源。非 GET 业务请求验证本地来源与 JSON/会话约束，文件导入另走 multipart，避免外部网页触发本地付费任务。

### 5.2 必须实现的端点

| 端点 | 契约摘要 |
| --- | --- |
| `GET /api/health` | 进程、数据库与版本状态；不调用模型 |
| `POST /api/books/import` | multipart file + encoding；返回 book_id 和 IMPORT job_id |
| `GET /api/books`、`GET /api/books/{id}` | 元数据、导入状态和当前版本 |
| `GET /api/books/{id}/chapters` | 按 ordinal 的目录和可定位范围 |
| `PUT /api/books/{id}/reading-progress` | book_version_id、当前位置、阅读模式、expected_version；保存书签，不调用模型 |
| `GET /api/books/{id}/content` | chapter/range + horizon；返回结构化节点，不返回任意未经处理 HTML |
| `GET /api/books/{id}/resources/{resource_id}` | 受控登记资源 |
| `GET /api/model-profiles` | 非敏感配置与 has_key |
| `POST /api/model-profiles` | 新配置、可选 Key、session/system 保存策略 |
| `PATCH /api/model-profiles/{id}` | 字段变更、expected_version、keep/replace/remove 密钥操作 |
| `DELETE /api/model-profiles/{id}` | 删除配置和其凭据引用；有运行依赖时返回冲突 |
| `POST /api/model-profiles/test` | 已存配置或临时草稿；有预算的小请求，返回结构兼容结果及 usage |
| `POST /api/books/{id}/estimates` | 范围、配置、参数；只做本地估算并说明依据 |
| `POST /api/jobs` | process/preview/recompute、书籍、范围、配置、预算、幂等键 |
| `GET /api/jobs/{id}` | 状态、检查点、进度、usage、剩余未处理范围 |
| `POST /api/jobs/{id}/pause`、`resume` | 暂停调度、按明确检查点恢复 |
| `POST /api/jobs/{id}/reconcile` | 对未知远程结果执行提供方查询、保持未解决，或用户明确选择预算内重试；保留旧尝试记录 |
| `GET /api/books/{id}/annotations` | 范围、版本、horizon 与阅读模式，返回有效投影和图例 |
| `GET /api/books/{id}/review-items` | 章节/场景/原因/queue_status 过滤 |
| `GET /api/review-items/{id}` | 目标、允许上下文、候选、版本与依赖 |
| `GET /api/quotes/{id}` | 普通对白详情；不要求它已存在 review item |
| `POST /api/quotes/{id}/review-items` | 用户主动标记问题，幂等返回当前待确认项 |
| `POST /api/quotes/{id}/corrections` | 当前发言人工更正，不自动调用模型 |
| `POST /api/gaps/{id}/corrections` | 用户确认 CONTINUE/UPDATE/BREAK/UNCERTAIN，返回场景修订影响 |
| `POST /api/review-items/{id}/defer` | 仅延后处理，不增加确认计数 |
| `POST /api/corrections/{id}/undo` | 校验适用版本后撤销 |
| `POST /api/scenes/{id}/speaker-revisions` | merge/split、明确 quote IDs、版本及影响范围 |
| `POST /api/quotes/{id}/recheck` | 有上限的局部复核，返回新任务 |
| `GET /api/books/{id}/usage` | 按任务/阶段/模型汇总，有未知用量标记 |
| `POST /api/books/{id}/exports/preview` | 范围/样式/visibility_policy；返回冻结 snapshot_id、局部样张、覆盖统计和警告，不调用模型 |
| `POST /api/books/{id}/exports` | snapshot_id、format、样式、幂等键；返回 export_id/job_id，创建本地 EXPORT 任务 |
| `GET /api/exports/{id}` | 状态、快照摘要、格式、文件大小/哈希、校验结果和下载可用性 |
| `GET /api/exports/{id}/download` | 完成的成品；EPUB 为 application/epub+zip，HTML 为 text/html; charset=utf-8；安全的附件文件名 |
导入结果、估算和以下 JSON 为契约示例，不是已运行输出。

### 5.3 关键请求示例

创建试运行任务：

```json
{
  "book_id": "book1",
  "book_version_id": "bv1",
  "mode": "preview",
  "range": {"chapter_id": "ch1", "start_cp": 0, "end_cp": 1200},
  "profile_id": "mp1",
  "reading_mode": "initial",
  "visible_horizon_cp": 1200,
  "budget": {"max_input_tokens": 12000, "max_output_tokens": 2000, "max_rechecks": 2},
  "idempotency_key": "client-generated-unique-request"
}
```

这些值是样例，不是全局默认预算。任务创建校验范围、原文版本、profile 可用性和预算；同一幂等键但不同请求摘要返回 409。

人工更正：

```json
{
  "action": "assign_existing",
  "speaker_ref": "speaker_a",
  "scope": "utterance",
  "expected_version": 4,
  "expected_scene_version": 2
}
```

action 为 `assign_existing / create_speaker / set_kind / mark_unknown`。`set_kind` 必须携带目标 kind；`create_speaker` 的 description 可空。范围默认为当前 utterance，若包含多个 quote 必须返回实际影响 IDs。

手动确认未知与暂时跳过不同：前者可锁定为未知（增加独立 `user_locked` 字段），后者只更新队列。UNKNOWN 不因人工操作就变成已识别人物。普通人工确定结果为 USER_CONFIRMED，speaker_id 可以因 thought 等类型为空。

响应包含 correction_id、当前 annotation/scene 版本、affected_quote_ids、stale_window_ids 和 updated_review_counts，前端不能自己推算权威计数。

导出请求（先取得同一书籍的快照，不在生成中读取变化后的标注）：

```json
{
  "snapshot_id": "export_snapshot_1",
  "format": "epub",
  "style": {"preset": "color_and_label", "palette_id": "reader-default"},
  "idempotency_key": "unique-export-request"
}
```

preview 请求包含 book_version_id、scope（整本或 chapter_ids）、visibility_policy（position_safe/reread）与样式，返回 snapshot_id、sample_fragment、coverage、warnings。最终生成必须校验快照属于当前书籍且来源版本仍可读取；可对同一冻结快照输出不同格式。任意旧 snapshot 的导出不会自动采用最新纠正，前端提供刷新入口。

导出沿用 QUEUED/RUNNING/COMPLETED/FAILED 任务状态；COMPLETED 表示文件已生成，标准校验是否完成在 validation_json 单独表示。EXPORT 不使用 LLM 的 token 预留、重试或计费逻辑，只记录本地耗时和字节数。

### 5.4 模型协议适配

首版实现一种有实测依据的 `chat-completions-compatible` 协议和仅限测试的 FakeProvider。接口定义：`test_connection, generate_labels, estimate_tokens, normalize_usage, capabilities`。

- 配置 Base URL 定义为 API 根路径，适配器追加相对端点；拒绝重复填入完整业务端点导致的模糊拼接，并在 UI 显示说明。
- 使用服务端 HTTP 客户端，支持本地地址、无 Key 服务和明确的超时配置。
- messages/model 等字段按所接入提供方官方协议实现；json_schema、json_object、temperature、max_tokens 等参数由 capability 决定，不假设所有兼容服务都支持。
- 测试连接使用微型结构化任务，既检查鉴权也检查输出可解析；连接成功不代表已验证小说效果。
- 日志与异常统一脱敏；GET 配置只返回 has_key，不回传完整或可恢复的密钥。
- FakeProvider 只能在测试/明确演示配置下启用，页面要标注；真实提供方失败不能悄悄回退到假数据。

### 5.5 作业、预算、缓存与恢复

单个本地调度器领取持久化任务；同一开放场景按顺序提交。第一版不要求多进程分布式队列，也不能把所有任务状态仅放在内存。

缓存指纹包含：原文版本、目标 IDs、完整实际输入、模型与生成参数、协议/提示/schema/筛选版本、有效依赖状态、阅读模式与证据 horizon。排除 UI 颜色、job_id、preview/process purpose 和不影响语义的显示选项。

执行前用保守估计预留本次最大用量；调用后按真实 usage 结算。缺 tokenizer 时标记估计依据并保守控制，未知 usage 保留预留量或停止追加调用。金额限制只有价格资料可用时启用，页面区分估算与实际账单。

暂停：RUNNING → PAUSING → 当前请求返回并保存 → PAUSED。用户看到暂停中，不承诺远程请求已经停止计费。

恢复：已完成窗口直接复用；PREPARED 未发送可安全重试；DISPATCHED 但没有结果进入 UNKNOWN_OUTCOME/NEEDS_RECONCILIATION。先用提供方支持的查询/幂等能力核对；不支持时说明可能已计费，不能盲目自动重发。其他确定未完成且无风险依赖的工作可继续。

进程重启扫描过期 lease，修复任务可见状态，不能简单把所有 RUNNING 都重新发给远程模型。

## 6. 前端的具体实现契约

### 6.1 页面和组件边界

| 路由 | 页面组件 | 关键子组件 |
| --- | --- | --- |
| `/library` | LibraryPage | ImportDropzone、EncodingPicker、BookCard、ImportStatus |
| `/settings/models` | ModelSettingsPage | ModelProfileForm、CredentialControls、ConnectionTestResult |
| `/books/:id/preview` | PreviewPage | RangePicker、BudgetForm、PreviewCompare、SpeakerLegend、UsageSummary |
| `/books/:id/read` | ReaderPage | ChapterNavigation、DocumentRenderer、AnnotationLayer、QuoteDetailDrawer |
| `/books/:id/review` | ReviewPage | ReviewFilters、ReviewQueue、QuoteContext、SpeakerChoices、CorrectionForm |
| 阅读/预览页内导出对话框 | ExportDialog | ExportScopePicker、ExportStylePreview、ExportProgress、ExportDownload |

任务状态使用共用 JobPanel。QuoteDetailDrawer 和 ReviewPage 复用 CorrectionForm，不能各自实现不同的确认状态机。

### 6.2 数据获取与界面状态

- 使用 TanStack Query 管理服务器状态；页面组件不另存一份权威人物列表。
- 查询键至少含 book_id、book_version、范围、阅读模式、horizon 和相应版本，避免错误复用其他视图的结果。
- 密钥只留在表单临时内存，提交后清空，不进入 Query 持久化或全局状态。
- 每个页面明确 loading/empty/success/error 状态；重试按钮不得在渲染和自动刷新时发起付费请求。
- 只轮询活动任务，初始约 2 秒，失败时退避，终态停止。网络重连先查询任务，不重新创建任务。
- 标注更正成功后按后端 affected IDs 刷新正文、场景图例、详情和 review counts。关键写入先以服务端确认为准，不让乐观 UI 掩盖版本冲突。
- 基础功能适配 360px 与 1280px 宽度；主要信息不能只能通过悬停获得。

### 6.3 各页面完成条件

**导入页**：支持拖放/选择 TXT 与 EPUB；展示进度、编码预览、解析警告和目录。失败可保留原文件并重新选编码，不创建一堆重复书籍。

**设置页**：可新建/编辑/删除配置，填写 URL、Key、模型，区分会话保存与系统保存；测试按钮展示真实结果。Key 空字符串、未更改、删除三种状态不能混为一谈。

**预览页**：先选范围，再明确点击试运行；显示估算依据与实际用量。原文与标注通过同一 DocumentRenderer 渲染，支持切换或并排按 quote_id 同步定位。控制颜色/色条/编号/字号不触发模型调用。

**阅读页**：无模型也能阅读；保存阅读位置。点击普通对白或待定对白均能查看详情。颜色由场景内稳定 group ID 映射；超出容易区分的颜色数量时增加编号，而非假装颜色足够。

**待确认页**：按场景/章节/原因/状态筛选；提供前后叙述和候选证据。可指定已有说话人、创建新人、改类型、锁定未知、跳过、重试和撤销。跳过项仍可找回，不能只因列表清空就显示全部确认。

队列项使用 target_type 区分 quote 与 gap；场景边界问题展示左右对白与完整间隔段，通过 Gap 更正接口处理，不能误用说话人确认接口。

### 6.4 人工操作的付费边界

无付费调用：打开原文、展开已有上下文、筛选列表、变更样式、确定人物、改类型、合并/拆分、撤销，以及导出样张、生成与下载 EPUB/HTML。

可能付费：测试连接、真实试标注、正式处理、局部复核、受影响部分重新推理。这些使用明确按钮和预算；若启用自动更新，用户先开启并设置上限。

“展开上下文”只取原文，不等于“用更多上下文再问模型”，两者必须是不同操作。

### 6.5 初读与后文证据

前端按可见阅读块提交 visible_horizon，后端校验它在合法范围；strict 模式只向模型提供此范围内文字。预处理模式可以计算更大范围，但按历史和 visible_from 投影展示。

后端不能只隐藏人物名称而仍以同色提前合并身份；返回的 speaker 图例和颜色归属同样受 horizon 约束。普通获取 annotations 必须是只读投影，不为用户翻页触发重新推理。

### 6.6 导出界面

阅读页与效果预览页均可打开 ExportDialog：选择 EPUB/HTML → 整本或章节 → 颜色/编号样式 → 初读安全或重读 → 查看局部样张、快照时间和覆盖统计 → 生成 → 查看校验状态并下载。

- 首次打开请求本地 export preview，不触发 LLM；修改样式只刷新样张，不重新识别。
- 默认包含所选范围的全部正文；未处理/待定/过期部分会保留原样。明确展示这些数量，用户可返回确认或继续导出。
- 样张来自后端导出渲染器，并在隔离的无脚本预览容器显示，不能只用应用阅读页截图冒充导出效果。
- 校验失败可查看具体原因并重试；工具未运行与校验失败是不同状态。成功下载用后端 filename/MIME，并释放前端临时对象 URL。
- 生成期间发生人工更正时显示“使用先前快照”，提供刷新快照后重新导出，不静默混合。
- 更改范围为“已完成章节”时列出包含与排除的章节；文件名明确是节选还是全书标注版。

## 7. 验证设计、样例与效果评测

### 7.1 测试层次

- 单元：编码/映射、引号、Gap、预算、输出验证、状态投影。
- 集成：SQLite 真实事务、迁移、FastAPI 请求、FakeProvider、检查点和冲突。
- 前端：表单、队列、错误状态、码点分片、跨页面刷新。
- E2E：隔离数据目录中通过真实前后端完成导入→配置→预览→确认→恢复；模型响应由可控 FakeProvider 供应并明确标注。
- 真实联调：用户提供服务和预算后的少量实请求，独立记录。
- 效果评测：按作品留出、人工确认的小说样本。与软件功能通过是不同结论。

### 7.2 最小回归夹具清单

| 编号 | 内容 | 必须验证 |
| --- | --- | --- |
| F01 | UTF-8 TXT，明确说话人 | 范围准确，可手动/自动归属 |
| F02 | GB18030 TXT 与错误编码选择 | 预览纠正，不静默丢字 |
| F03 | EPUB 文件名顺序与 spine 不同 | 按 spine 阅读 |
| F04 | EPUB 含 ruby、图片、跨节点对白 | 注音不重复进入正文，着色位置正确 |
| F05 | 问答中插入长心理和环境描写 | 不因间隔长而重置场景 |
| F06 | 一句话包含时间跳跃与新交谈 | 允许短 Gap 结束场景 |
| F07 | 三人插话、同一人连续发言 | 不强制轮流，不遗漏第三人 |
| F08 | 声音先出现，后文说明是少年 | 延迟关联同一个匿名分组 |
| F09 | 证据不足的三句短对白 | 允许 UNKNOWN，不给每句建新人 |
| F10 | 嵌套引用、心声、标题引号、集体声音 | 分类不污染普通人物 |
| F11 | 相同对白重复出现、emoji、扩展汉字 | ID 和码点映射正确 |
| F12 | 长场景跨章节、跨预算窗口 | 状态延续与重叠一致性 |
| F13 | 模型输出坏 JSON、错 ID、漏目标 | 拒绝错误提交，有限重试 |
| F14 | 模型响应晚于用户确认 | 人工锁定不被覆盖 |
| F15 | 重启发生在请求发送后、响应保存前 | 显示未知结果，不盲目重复计费 |
| F16 | 预览后处理同范围、重复点击开始 | 复用缓存、任务幂等 |
| F17 | 后文揭示匿名人物身份 | 初读 horizon 下不提前同色 |
| F18 | 旧页面确认、撤销后已有其他修订 | 返回冲突，不丢失较新修改 |
| F19 | ZIP 越界/过大、活动 EPUB 内容 | 拒绝越界，渲染不执行脚本 |
| F20 | 达到预算、缺 Key、超时/限流 | 原文可读、usage 不被错误归零 |
| F21 | TXT 全文带已确认与自动标注，导出 EPUB/HTML | 正文完整、编号与样式正确，导出零模型调用 |
| F22 | EPUB 封面、ruby、图片、跨节点对白和脚注 | 重打包后结构与资源可读，编号不重复 |
| F23 | 未处理、UNKNOWN、PROVISIONAL、stale 混合 | 全文保留，默认仅有效确认结果着色 |
| F24 | 导出期间同时发生人工更正 | 同一文件仅使用一个快照；新导出可反映新版本 |
| F25 | 后文合并身份与灰度阅读 | 初读版不提前同色，可见编号不依赖 CSS 伪元素 |
| F26 | 节选章节引用其他位置的图片/脚注/正文 | 必要资源闭包完整，无坏包内链接，不误加入整章 |
| F27 | XHTML 转义字符、重复锚点、缺失资源 | 报告校验错误，不生成假成功成品 |
| F28 | HTML 断网打开与关闭应用后打开 EPUB | 无 localhost/远程依赖，不携带密钥或应用脚本 |
| F29 | EPUBCheck 缺失、报错或警告 | NOT_RUN/FAIL/警告真实区分，发布门槛不被绕过 |
| F30 | 重复生成和重复下载、中文文件名 | 幂等、缓存复用、MIME 正确，不覆盖源书 |

夹具使用原创小片段，格式文件由测试构造。F05～F10 的 FakeProvider 只用于验证业务处理；要验证 LLM 本身，必须用真实调用与人工标签另行评测。

### 7.3 金标准与指标

标注保存 book_id、作品分组、完整场景范围、quote 范围/kind、匿名 group、可判定性、证据和 Gap 中必须保留的片段。范围规范与运行系统相同，避免评测时坐标转换错误。

按整部作品划分调参/校准/测试，冻结后不使用测试结果反复改提示。最优标签匹配在金标准场景内进行；预测场景的 group ID 必须带场景命名空间，不能因重复 S1 字样伪装一致。

报告：提取 precision/recall；错误切断/连接；匹配后分组准确率；同人 pairwise F1；已接受准确率与覆盖率；未知强标率；新人物误建/漏建；每万字 token、重试、复核和耗时。不可判定样本单列；拒答不计已接受准确率，但计入覆盖率分母。

首轮目标沿用 PLAN.md：已接受准确率 ≥97%、总体覆盖率 ≥70%，并单列无显式归属的难例。该目标必须用真实独立作品验证，不能以“结构校验通过率”替代。样本不足时给出样本量与不确定性，不能宣布达标。

## 8. 按顺序执行的任务

### 8.0 顺序与通用完成条件

```text
T00 → T01 → T02 → T03 → T04 → T05 → T06 → T07 → T08 → T09
    → T10 → T11 → T12 → T13 → T14 → T15 → T15A → T15B → T16 → T17 → T18 → T19
```

默认按此顺序，每个任务先做后端契约再接前端调用。缺真实数据/API 时可完成不依赖它的后续工程任务，但保留相应 live/quality BLOCKED 项；不能把真实验证门槛删除。

每项任务通用要求：改动可运行；相关测试通过；类型/schema 不漂移；文档与状态账本更新；明确哪些验证没有执行。以下文件名是预期产物，实施时若沿用已有等价路径，在状态账本说明映射。

### T00 — 工程骨架与执行账本

**前置**：读取全部约束，检查现有目录。对应 PLAN P0 的工程与契约准备。

**实施**：建立 pyproject、前端 package、开发端口与代理、应用健康检查、测试入口、.gitignore；创建 CONTRACTS、IMPLEMENTATION_STATUS、HANDOFF。记录依赖版本、运行前提和首个技术决策。定义金标准 schema 与原创样例，不伪造真实作品数据。

**产物**：`backend/src/ndr/app.py`、`frontend/src`、锁文件、基础脚本和文档。忽略 data、凭据、临时运行日志，不忽略必要迁移和原创测试夹具。

**验证**：启动后 `/api/health` 可访问；前端显示由后端返回的状态；`pytest backend/tests`、前端 typecheck/build 及一个连通测试通过。

**门槛**：用户能按 README 启动空应用；四状态进度账本中没有虚构 live/quality PASS。

### T01 — 领域模型、数据库迁移与公共契约

**前置**：T00。

**实施**：实现第 3 节枚举与核心表；建立迁移、事务服务和版本校验；定义标准错误、分页与 OpenAPI；导出前端类型。先做 books/book_versions、文档、quote、scene、annotation、profile、job 的迁移，其余表随任务追加迁移，不用删库重建代替升级。

**产物**：`domain/`、`storage/`、`migrations/`、`docs/openapi.json`、生成类型。

**验证**：空库迁移、现有测试库升级、外键错误、版本冲突及字段序列化；运行 `pytest backend/tests/integration/test_schema.py`，重新生成类型后 typecheck。

**门槛**：重复运行迁移安全；无明文密钥列；UNKNOWN、用户锁定和队列状态能独立表达。

### T02 — TXT 导入、编码与统一文档树

**前置**：T01。

**实施**：文件导入 API、IMPORT 任务记录和最小轮询；实现 TXT 解码、编码纠正、段落/标题、稳定版本、规范化文本及 source_map。重复导入在相同解析配置下复用版本，换编码生成新版本。

**产物**：`ingest/txt.py`、文档 schema、books/content 路由。导入任务执行器先只支持本地解析，T10 扩展推理调度。

**验证**：F01、F02、F11；`pytest backend/tests/unit/test_txt_ingest.py backend/tests/integration/test_book_import.py`。

**门槛**：没有 LLM 也可通过 API 完整读取原文；重复文本和特殊字符定位稳定。

### T03 — EPUB 导入与资源映射

**前置**：T02。

**实施**：按 container/OPF/spine 读取 EPUB，目录和正文顺序分离；转换为统一节点树，登记图片，处理 ruby，建立章节/节点/码点映射。实现资源端点、解压与节点限制。

**产物**：`ingest/epub.py`、安全节点转换器、资源服务、EPUB 原创夹具。

**验证**：F03、F04、F19；`pytest backend/tests/unit/test_epub_ingest.py backend/tests/integration/test_resources.py`。

**门槛**：TXT 和 EPUB 输出同一契约；复杂样式可降级但不能丢段、改变阅读顺序或把注音重复到正文。

### T04 — 书架、导入与无模型阅读器

**前置**：T03。

**实施**：LibraryPage、导入进度、编码选择、章节列表、DocumentRenderer、阅读位置保存、图片/ruby 与响应式布局。预留 AnnotationLayer 和点击 quote 的接口，不伪造识别结果。

**产物**：pages/library、pages/reader、文档渲染组件、API 查询封装。

**验证**：前端组件测试与导入阅读 E2E；运行 typecheck/build，以及 `npm --prefix frontend run test:e2e -- import-reader.spec.ts`。

**门槛**：用户不填写 API 即可阅读 TXT/EPUB；错误格式和编码问题可理解、可恢复。

### T05 — 候选引语、Gap 与标注样例工具

**前置**：T04。

**实施**：引号栈、嵌套与跨段扫描、quote/utterance/Gap 数据；原文定位接口和候选覆盖显示。建立标注样例导入导出与范围检查工具，便于制作真实金标准。

**产物**：`quotes/`、quotes 查询服务、`evaluation/schemas/` 和 annotation-guide。

**验证**：F05～F12 的解析部分，不要求模型效果；`pytest backend/tests/unit/test_quote_scanner.py backend/tests/unit/test_source_map.py`。

**门槛**：只是提取候选，不通过轮流分配或人名匹配假装完成说话人识别；异常引号不会吞章。

### T06 — 模型配置后端与设置页

**前置**：T05。

**实施**：profile CRUD、版本、session/system 凭据服务；设置页面输入和错误状态；定义 adapter 接口。先用测试适配器验证设置流程，页面明确实际协议能力。

**产物**：`llm/profiles.py`、凭据服务、settings/models 页面与表单。

**验证**：GET 不泄露 Key、keep/replace/remove、系统凭据不可用退回会话；`pytest backend/tests/integration/test_model_profiles.py` 与设置页测试。

**门槛**：不改源码即可配置 URL/模型/Key；默认没有任何真实调用。连接测试实际请求在 T07 完成。

### T07 — 模型适配器、输出契约与连接测试

**前置**：T06。

**实施**：实现一个真实兼容协议与 FakeProvider；核对接入提供方文档，记录 capability；完成测试连接路由与页面；实现第 4.4 节 schema 和程序校验。提示模板版本化，小说作为数据传入。

**产物**：`llm/adapters/`、`llm/prompts/`、`llm/schemas.py`、`llm/validation.py`。

**验证**：F13、F20，模拟 401/429/超时/错 schema；`pytest backend/tests/unit/test_llm_contract.py backend/tests/integration/test_provider_adapter.py`。有凭据和预算才单独运行真实微型测试。

**门槛**：适配器异常不会回退到假成功；短结构化响应可可靠解析；usage 按提供方口径保存。

### T08 — 上下文、预算与证据范围

**前置**：T07。

**实施**：完整发言窗口、Gap 保守保留、重叠、候选状态裁剪、证据 ID 集合、省略记录、token 估算和输出预留。按初读/重读与 horizon 限制范围；默认不启用激进 Gap 压缩。

**产物**：`context/window_builder.py`、`budget.py`、`source_selection.py`。

**验证**：F05、F06、F12、F17、超长单条引语；`pytest backend/tests/unit/test_context_windows.py backend/tests/unit/test_budget.py`。

**门槛**：预算边界不是场景边界；不丢目标对白；上下文来源可回溯；补入任何片段均计入预算。

### T09 — 联合场景与匿名分组引擎

**前置**：T08。

**实施**：组装单窗口推理流程，处理 Gap 四状态、EXISTING/NEW/UNKNOWN、临时 ID 映射、场景状态、声音先出现、身份修订提议与接受策略。对锁定结果、证据及跨窗口依赖进行校验。

**产物**：`scenes/`、`speakers/`、推理服务和对应状态转换测试。

**验证**：F05～F10、F12、F14、F17；`pytest backend/tests/unit/test_scene_state.py backend/tests/integration/test_attribution_engine.py`。FakeProvider 验证状态机，真实模型效果留给 T16。

**门槛**：同一场景跨窗口保持身份；未知不制造新人；UPDATE 不切场景；后文证据具备可见时点。

### T10 — 持久化推理任务、缓存与用量

**前置**：T09。

**实施**：在 IMPORT 任务基础上加入 INFERENCE/RECHECK/RECOMPUTE；实现调度、窗口检查点、profile 快照、缓存指纹、用量记录、预算预留、幂等创建和状态查询。网络调用与数据库事务分离。

**产物**：`jobs/`、`storage/cache.py`、任务与估算/usage API。

**验证**：F15、F16、F20；`pytest backend/tests/integration/test_jobs.py backend/tests/integration/test_cache.py`。用可控客户端验证发送次数，检查 preview/process 同输入命中缓存。

**门槛**：已完成窗口不重复调用；未知远程结果不会当作确定失败自动重发；每个尝试可追溯用量状态。

### T11 — 真实效果预览与按章处理

**前置**：T10。

**实施**：PreviewPage、范围选择、估算、小范围试运行、任务面板、原文/标注切换、图例和 usage；正式阅读接入相同 annotations 投影。预览结果直接复用到正式处理，不创建另一套临时识别存储。

**产物**：preview 页面、AnnotationLayer、SpeakerLegend、RangePicker、JobPanel。

**验证**：前端组件和 `npm --prefix frontend run test:e2e -- preview.spec.ts`；核对范围正确、原文不变、样式切换的模型调用数为零、重复请求缓存命中。真实提供方联调另记状态。

**门槛**：UI 由后端实际任务结果驱动；测试可用 FakeProvider，但正常配置无法显示伪造的成功结果。TXT/EPUB 均能着色。

### T12 — 人工更正、分组修订与撤销后端

**前置**：T11。

**实施**：已有角色、新角色、类型、锁定未知、Gap 更正、merge/split、撤销；用版本检查和事务更新历史、有效投影、队列、stale 依赖。提供普通对白详情和主动标记接口，不能仅支持已在队列中的对白。

**产物**：corrections/review/speaker-revisions 路由、事务服务、依赖失效器。

**验证**：F14、F18；`pytest backend/tests/integration/test_corrections.py backend/tests/integration/test_identity_revisions.py`。对并发旧版本、撤销越过新修订、跨场景误关联和人工锁定逐项检查。

**门槛**：一个事务得到一致结果；手动更正和撤销没有模型请求；不能通过硬删除历史实现撤销。

### T13 — 待确认队列与阅读页确认抽屉

**前置**：T12。

**实施**：ReviewPage、筛选分页、上下文、候选证据、共享 CorrectionForm；支持确认已有、新建人物、改类型、未知、跳过、复核、撤销、明确范围的分组修订。更正后同步刷新阅读页、图例和数量。

**产物**：review 页面、QuoteDetailDrawer、QuoteContext、CorrectionForm。

**验证**：`npm --prefix frontend run test:e2e -- review.spec.ts` 和组件测试；测试普通对白入口、待定入口、空候选、旧版本 409、DEFERRED 找回和撤销。

**门槛**：用户可不写代码完成所有人工确认；队列清空不被等同于全部识别正确；展开原文与模型复核是不同操作。

### T14 — 暂停恢复、预算到顶与故障闭环

**前置**：T13。

**实施**：完善暂停/恢复和重启扫描；实现 NEEDS_RECONCILIATION 处理、缺失凭据恢复、限流退避上限、预算预留与未知用量展示。提供受影响范围重算的明确入口，默认关闭自动付费重算。

**产物**：recovery 服务、暂停/预算 UI、任务错误映射、进程重启测试。

**验证**：F15、F20；`pytest backend/tests/integration/test_recovery.py`；E2E 模拟断线、重启、预算耗尽与缺 Key。验证已完成窗口保持有效、暂停中显示准确。

**门槛**：任务失败不会让原文不可读；未知付费结果不自动重发；每一种非完成状态均有可理解的恢复动作。

### T15 — 证据时点、阅读投影与最终定位回归

**前置**：T14。

**实施**：整合 initial/reread、visible_horizon、annotation history 和 identity revisions；严格上下文限制与整章预计算的受限展示分别验证。完善跨节点 quote、ruby、特殊字符和页面重新布局时的定位。

**产物**：投影查询、阅读进度 API、horizon 状态和映射回归。

**验证**：F04、F11、F17；`pytest backend/tests/integration/test_visibility.py`，`npm --prefix frontend run test:e2e -- reading-visibility.spec.ts`。

**门槛**：后文身份揭示前不会因颜色、图例或候选说明泄露合并关系；投影查询只读，不暗中重新推理。

### T15A — EPUB/HTML 导出后端与标准校验

**前置**：T15，以及 T12 的更正/版本和 T10 的持久化任务能力。

**实施**：实现 export snapshots/artifacts 迁移、冻结投影、position_safe/reread、统一文本样式渲染、EPUB 打包、单文件 HTML、内部校验、EPUBCheck 包装器、幂等任务和受控下载。导出依赖只能访问已存数据与本地资源，不能走 LLM adapter。

**产物**：`exports/snapshot.py`、`projection.py`、`render.py`、`epub.py`、`html.py`、`validation.py`、export 路由与迁移；`backend/scripts/validate_exports.py` 及原创导出样例。

**验证**：F21～F30 的后端部分；执行 `uv run --project backend pytest backend/tests/unit/test_export_render.py backend/tests/integration/test_exports.py`。校验四种输入/输出组合、剔除辅助标签后的正文一致性、资源闭包、快照隔离和调用计数。运行标准检查并保存工具版本；缺工具如实记录，不跳过后伪装 PASS。

标准检查目标命令（T15A 建立脚本，jar 路径可由显式配置指定）：

```powershell
uv run --project backend python backend/scripts/validate_exports.py --epub data/test-exports/sample.epub --epubcheck-jar tools/epubcheck/epubcheck.jar
```

脚本使用参数数组调用 Java，不拼接不可信 shell 命令；错误返回非零，工具缺失用独立状态报告。安装和锁定工具版本在实施阶段完成，本次文档不表示工具已经安装。

**门槛**：输出为离线可读的真实文本文件，原文与源书不被覆盖；未知保持原样；导出无模型调用；内部检查有证据，标准检查状态真实。

### T15B — 导出对话框、样张与下载闭环

**前置**：T15A。

**实施**：在阅读与预览页接入 ExportDialog；格式/范围/样式/初读策略选择、覆盖统计、后端样张、快照过期提示、生成进度、校验报告与下载。样张与实际文件共享渲染逻辑。

**产物**：ExportDialog、ExportScopePicker、ExportStylePreview、ExportProgress、ExportDownload 组件及 API 查询；README 的导出与阅读器设置说明。

**验证**：`npm --prefix frontend run test:e2e -- export.spec.ts` 和相关组件测试；完整验证 TXT/EPUB→EPUB/HTML、未知/部分处理、并发纠正、失败报告、中文文件名和重复下载。HTML 断网打开；至少两款独立 EPUB 阅读器试读并记录版本、设置和表现。

**门槛**：用户不运行脚本即可选择、预览、生成和下载带格式书籍；关闭应用后仍可阅读；颜色被覆盖时可见编号仍起作用。未完成实际阅读器验证就保留其验证状态，不用浏览器样张代替。

### T16 — 真实样本评测与可复现实验

**前置**：T15B；真实质量验证需要人工确认样本、模型配置和预算。导出真实阅读器验证受环境限制时可先做评测工具，但保留相应未完成项。

**实施**：落实 annotation-guide、作品级划分、不可判定标签和评测脚本。完成 B0/B1/B2 对比，输出按作品和难例类别的指标、usage 和配置指纹。实现只校验数据的离线命令与显式允许真实调用的运行命令。

**产物**：`backend/src/ndr/evaluation/`、`evaluation/manifests/`、`configs/`、reports 与复现说明。

**目标命令**：

```powershell
uv run --project backend python -m ndr.evaluation validate --manifest evaluation/manifests/dev.json
uv run --project backend python -m ndr.evaluation run --manifest evaluation/manifests/dev.json --config evaluation/configs/b2.json --output evaluation/reports/b2.json --allow-live
```

**验证**：用手算的小型聚类样例检验匿名标签置换、错误分场、全拒答、全合并及漏提取的指标；真实运行保存模型/提示/schema/数据版本和实际用量。

**门槛**：算法指标不是前端模拟数据；未达到 97%/70% 或样本不足如实报告，不能把该任务的脚本完成标记成质量达标。

### T17 — 上下文压缩、局部复核与成本路由

**前置**：T16 的评测工具；启用为默认配置前必须有真实对比证据。

**实施**：长 Gap 保守筛选、必要证据补回、有限局部复核和可选强模型路由；保持 baseline 配置可回滚。完成 B3/B4 与消融实验，记录每次压缩丢失的金标准证据。

**产物**：版本化 context policy、recheck policy、对比报告和默认配置选择理由。

**验证**：上下文删减/补回回归，以及真实准确率—覆盖率—总费用对比；包括预处理和重试成本，不能只统计最后一次调用。

**门槛**：有收益才改变默认策略；没有数据或没有收益就保留保守策略，并标注优化未验证，不扩大功能范围补偿失败。

### T18 — 完整联调、体验与发布检查

**前置**：T17；可以保留优化关闭，但必需产品功能全部完成。

**实施**：执行 TXT/EPUB 导入至 EPUB/HTML 导出的全流程、可访问性、360/1280px 布局、密钥与资源边界、任务恢复和接口类型一致性检查；修复真实发现的问题，避免无依据增加新功能。

**产物**：`docs/verification-report.md`、离线/E2E 报告索引、真实联调记录、已修复问题及残余阻塞清单。

**验证**：后端 Ruff/pytest、前端 typecheck/test/build、全部 E2E、OpenAPI 生成无意外差异。对真实提供方做一次受预算约束的端到端试用，真实数据和账号缺失则明确 BLOCKED。

**门槛**：原有四项核心交互与导出功能均连接可用后端；不靠隐藏按钮、跳过用例或 FakeProvider 伪装真实能力。报告功能、稳定性、live 和质量四类结果。

### T19 — 启动交付、操作文档与最终交接

**前置**：T18。

**实施**：完善 README、依赖检查、初始化/迁移、dev.ps1、生产构建后同源启动、数据与版本升级说明；建立不含密钥和私人小说的示例配置。整理所有未完成项和复现步骤。

**产物**：可执行启动方式、用户操作说明、最终 IMPLEMENTATION_STATUS、HANDOFF、评测与验证报告索引。

**验证**：在新的测试数据目录按 README 从零初始化；导入原创 TXT/EPUB 样例并走通试运行、确认和 EPUB/HTML 导出，校验下载文件离线可读。升级测试保留原书籍和人工标注。

**门槛**：用户可自行启动使用；其他 AI 可从记录继续。没有真实服务/效果证据时只能称“工程交付完成，真实验证待完成”，不能宣称产品已通过全部验收。

## 9. 每次交接必须更新的文档

### 9.1 IMPLEMENTATION_STATUS.md

T00 创建下列表格，T00～T19 及 T15A、T15B 各一行，共 22 项；初始未执行状态不得预先打勾。已有账本按 T15 后插入新增任务，不改已有任务编号：

```markdown
| Task | Implementation | Offline | Live | Quality | Evidence | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| T00 | IN_PROGRESS | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 完成健康检查 |
```

Evidence 填具体测试命令、结果文件和必要的提交/文件版本。禁止只填“已测试”。新增任务使用明确后缀或新编号并说明依赖，不悄悄跳过原任务。

### 9.2 HANDOFF.md 模板

```markdown
# 开发交接

更新时间：
当前任务：
最近完成任务：
下一任务与理由：

## 实际运行方式
- 前置环境与已锁定版本：
- 启动命令与访问地址：
- 数据目录、迁移版本与服务状态：

## 本次改动
- 文件/模块及目的：
- 契约或数据库变化：

## 验证证据
- 执行命令：
- 结果与报告位置：
- 使用 FakeProvider 还是真实服务：
- 真实 token/费用口径（若有）：

## 未完成与已知问题
- 问题、影响、复现、下一步：
- 缺失输入与可继续的独立工作：
- 未验证的效果或兼容性：

## 后续约束
- 必须保留的用户修改：
- 不可覆盖的人工标注：
- 当前接口/提示/schema/数据版本：
```

不记录 API Key、完整私人小说、无需留存的原始模型请求。记录使用的配置 ID 与模型名即可。

### 9.3 技术决策记录

重大变更写 `docs/decisions/NNNN-title.md`：问题、选择、被放弃的方案、验证、迁移和回滚方式。尤其包括坐标规范、EPUB 表示、模型协议、缓存指纹、分组版本、接受策略和预算口径。

不为普通变量命名、简单样式调整创建冗长决策文档。仅保留会影响后续 AI 理解和继续实施的决定。

## 10. 可直接复制给其他 AI 的执行提示词

### 10.1 首次开始

> 请在 F:\Code\novel-dialogue-reader 开发轻小说对话辅助阅读器。先阅读适用的 AGENTS.md、PLAN.md 和 DEVELOPMENT.md，检查实际代码与状态，按 DEVELOPMENT.md 从 T00 开始执行首个未完成且依赖满足的任务。不要只重新写计划，请实现该任务、运行对应验证、修复发现的问题，并更新 docs/IMPLEMENTATION_STATUS.md 与 docs/HANDOFF.md。没有真实 API 或小说时继续完成可离线验证的工作，明确标记真实联调和效果评测未完成，不伪造结果。完成当前任务后报告改动、验证证据和下一任务。

### 10.2 接续指定任务

> 继续 F:\Code\novel-dialogue-reader 的开发。阅读 PLAN.md、DEVELOPMENT.md、docs/IMPLEMENTATION_STATUS.md、docs/HANDOFF.md 和已有实现，执行任务 TXX（将 TXX 替换为实际编号）。先核对前置依赖，保留已有工作，完成该任务的代码、接口、验证与交接。发现前置缺陷时修复必要部分，不能通过删除功能或绕过测试来标记完成。模型调用必须遵守已配置预算，人工确认不得被覆盖。

### 10.3 连续执行多个任务

> 按 DEVELOPMENT.md 从状态账本中首个未完成任务开始，依序执行到我指定的终点。每完成一个任务都运行相关验证并更新交接，再进入下一项。真实数据或凭据缺失时记录对应阻塞，继续不依赖它们的工程任务。不要自动部署或扩大模型调用范围。最后分别报告代码、离线验证、真实联调和效果评测状态。

### 10.4 独立验收

> 请独立验收 F:\Code\novel-dialogue-reader。以 PLAN.md 和 DEVELOPMENT.md 为需求，读取实际代码、迁移和任务记录，执行可运行检查，并验证 TXT/EPUB 导入、API 设置、真实预览、待定确认、EPUB/HTML 导出、预算恢复与人工锁定。区分 FakeProvider 验证和真实服务证据，检查状态账本是否夸大完成度。列出可复现问题、影响范围和修复优先级，不凭文档中的勾选认定通过。

## 11. 最终交付判定

### 11.1 工程交付

- [ ] T00～T19 及 T15A、T15B 的必需代码和离线验证有实际证据，未验证优化可保持关闭并明确说明。
- [ ] TXT/EPUB 导入、API 配置、效果预览、待定确认、正式阅读与 EPUB/HTML 导出形成闭环。
- [ ] 数据升级保留原文和人工修订；解析、缓存、撤销、暂停/恢复符合约定。
- [ ] 输入正文和 Key 不进入前端持久存储或无关日志；EPUB 资源读取受控。
- [ ] 真实与模拟状态明确，没有伪造完成进度或准确率。
- [ ] 按 README 可在新的数据目录启动，代码和交接不依赖前一 AI 的聊天记忆。
- [ ] 导出四条转换路径、原文一致性、静态证据时点、快照隔离和零 LLM 调用有验证；EPUBCheck 与真实阅读器检查状态可追溯。

### 11.2 真实可用性

- [ ] 用户选择的模型服务完成受预算约束的真实联调，费用与 usage 可核对。
- [ ] 真实作品分开用于调参与最终评测，结果附样本数和分类型统计。
- [ ] 达到 PLAN.md 的效果目标，或用户了解并接受明确记录的实际能力边界。
- [ ] 实际阅读试用记录了误导、人工更正负担及待定处理体验。
- [ ] 导出 EPUB 通过标准检查和两款独立阅读器试读，HTML 离线可用；记录样式覆盖、资源与阅读器差异。

工程完成和真实可用性分开签收。没有证据的项目保持未完成；后续 AI 应从进度账本继续，不重启项目、不掩盖缺口。
