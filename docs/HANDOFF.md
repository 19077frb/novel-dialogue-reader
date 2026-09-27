# 开发交接

更新时间：2026-09-28
当前任务：T07 模型适配器、输出契约与连接测试（implementation / offline_verification 完成；live = BLOCKED）
最近完成任务：T07（此前 T00 骨架、T01 模型与迁移、T02 TXT、T03 EPUB、T04 阅读器、T05 候选与金标准、T06 配置页）
下一任务与理由：T08 上下文、预算与证据范围。T05 已产出候选与 Gap，T07 已定义输出契约与适配器；
T08 需要把候选组织成受预算约束的处理窗口（含 Gap 保留、证据 ID 集合、token 估算与输出预留），
是 T09 引擎与 T10 任务的直接前置。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`：fastapi 0.141.1、httpx（运行时）、
  keyring、pydantic 2.13.5、SQLAlchemy 2.1.1、Alembic 1.20.0、python-multipart、jsonschema（dev）、
  pytest 9.1.1、ruff 0.16.9）；Node.js 22.14.0 / npm 10.9.2；React 18.3 / Vite 5.4 / Vitest 2.1 /
  Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、
  前端 `http://127.0.0.1:5173`（代理 `/api`）；模型配置页 `/settings/models`。
- 数据目录、迁移版本与服务状态：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0004`（= head）。
  凭据后端默认 `system`（keyring）；测试/E2E 用 `NDR_CREDENTIAL_BACKEND=session`；
  E2E 另外显式启用 `NDR_ALLOW_FAKE_PROVIDER=1`（仅测试，不发网络请求）。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`npm --prefix frontend run test:e2e`。

## 本次改动

- `llm/schemas.py`：§4.4 输出契约（`scene_updates/gap_decisions/new_speakers/labels/identity_proposals/
  needs_context`，`extra="forbid"`，assignment 与 kind 一致性在模型层拒绝）+ JSON Schema 导出。
- `llm/validation.py`：`LabelingTargets`（程序生成的允许 ID 集合）、`parse_output`（只接受整段 JSON/代码块）、
  `validate_output`（目标覆盖、唯一性、引用合法性、新场景必须 BREAK、临时人物声明、证据范围）、
  `RetryPolicy`（只对 `INVALID_MODEL_OUTPUT` 重试且上限 1）。
- `llm/prompts/`：`labeling-1`（小说作为数据 + 标记转义 + 硬规则）与 `connection-1`（微型回显任务）。
- `llm/errors.py`：`ProviderError`/`InvalidModelOutput` 与 kind→ErrorCode 映射（新增 `PROVIDER_UNAVAILABLE`）。
- `llm/adapters/`：`chat_completions.py`（Base URL 根路径 + `/chat/completions`、Bearer 鉴权、超时、
  状态码映射、错误详情脱敏、usage 归一化、CJK 启发式 token 估算）、`fake.py`（可脚本化的 FakeProvider）、
  `registry.py`（`AdapterSpec` + `build_adapter`，FakeProvider 需显式启用）。
- API：`POST /api/model-profiles/test`（已存配置或草稿；网络调用在事务之外；每次调用写 `inference_runs`，
  PREPARED → SUCCEEDED/FAILED；未知用量保持 NULL）。`domain/profiles.py` 增加连接测试 schema。
- 配置：`NDR_LLM_TIMEOUT_SECONDS`、`NDR_ALLOW_FAKE_PROVIDER`；httpx 提升为运行时依赖。
- 前端：设置页增加“测试连接”（卡片）与“测试当前填写内容”（草稿）按钮、结果面板（成功/失败、适配器、
  协议、模型、用量或“未知”、错误码、说明），FakeProvider 结果带明确警告。
- 契约产物：`docs/openapi.json` 与 `frontend/src/api/schema.d.ts` 重新生成（22 条路径）。
- 文档：新增决策 0009；CONTRACTS 增加第 16 节；README 增加连接测试说明。

## 验证证据

- `pytest backend/tests` → **222 passed**（T06 时 189；新增 18 + 15）。
- `ruff check backend/src backend/tests backend/scripts` → All checks passed；`scripts/verify.ps1` → 退出码 0。
- 适配器（MockTransport，真实 httpx 路径）：成功后返回 usage 与延迟，请求带 Authorization 与
  `response_format`；401/403→`PROVIDER_AUTH_FAILED`、404→`MODEL_NOT_FOUND`、429→`RATE_LIMITED`、
  500→`PROVIDER_UNAVAILABLE`、超时→`PROVIDER_TIMEOUT`、坏 JSON/错 schema→`INVALID_MODEL_OUTPUT`；
  **失败时 ok=false 且 usage 为 None**（不伪造成功与用量）；错误详情里搜不到密钥。
- 连接测试端点：FakeProvider 成功 → `inference_runs` 记录 SUCCEEDED、`usage_json` 为 **NULL**（未知不写 0）、
  快照含 `purpose=connection-test` 且不含 `api_key`；未启用 FakeProvider → 422；
  草稿测试后会话密钥被清理且配置**未落库**；真实适配器指向不可达地址 → `ok=false`
  且记录 FAILED（不是假成功）。
- 前端：`vitest` → **29 passed**（新增连接测试成功/失败/FakeProvider 警告/草稿测试四项）；
  Playwright → **9 passed**（新增连接测试用例，页面显示“没有访问任何真实服务”）。
- **未验证（BLOCKED）**：真实提供方联调（无凭据与预算）、真实模型兼容性、小说标注效果（T16）。

## 未完成与已知问题

1. **没有识别结果**：T07 只做协议与校验；真正的标注引擎（场景状态、分组、接受策略）是 T09，
   预览与确认是 T11/T12/T13。
2. **真实模型未联调**：协议路径用 MockTransport 验证；`chat-completions-compatible` 只声明
   `supports_json_schema=False`，实际提供方能力需要 T16/联调时按文档核对后再打开。
3. **token 估算是启发式**（CJK≈1 token/字、其它≈1/4），置信度标为 low；接入真实分词器前不能当作计费依据。
4. **`inference_runs.job_id` 在连接测试时为 NULL**：T10 会把推理尝试与任务/窗口正式关联。
5. **删除配置的保护是模糊匹配** `jobs.profile_snapshot_json`（决策 0008 已知项），T10 改为结构化检查。
6. 导入仍在请求内同步执行；枚举无数据库级 CHECK（0003）；EPUB 的 encoding 用 `xml` 哨兵（0005）。
7. 受限沙箱中 `uv run` 失败，脚本自动回退 `backend\.venv`；`npm --prefix frontend install/ci` 须在 frontend 内执行；
   `alembic.ini` 保持 ASCII；脚本已设置 `PYTHONUTF8=1`。
8. 本会话中 `apply_patch` 与沙箱内命令执行器失效，文件改用 `.tools/newfile.ps1`（无 BOM UTF-8）。
   注意：PowerShell 里不要把多行 here-string 直接当成函数参数；提交信息里不要出现双引号。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**` 与 `frontend/e2e/fixtures/**` 的原始字节；
  已发布迁移 `0001`～`0004` 不修改；**用户标注与用户密钥永不被自动结果覆盖或回传**。
- 当前接口/schema/数据版本：API 契约版本 `1`；数据库 revision `0004`；输出契约 `schema_version="1.0"`；
  提示词版本 `labeling-1` / `connection-1`；`SCANNER_VERSION=quote-scan-1`；
  `PARSER_VERSION=txt-1`/`epub-1`。**这些版本号参与缓存键，改动必须提升版本。**
- 修改公共契约时同步 `docs/CONTRACTS.md`、`docs/openapi.json`、`frontend/src/api/schema.d.ts`、调用方与测试。
- 提交习惯：每完成一部分功能即用 git 提交。