# 开发交接

更新时间：2026-09-28
当前任务：T08 上下文、预算与证据范围（已完成 implementation / offline_verification）
最近完成任务：T08（此前 T00 骨架、T01 模型与迁移、T02 TXT、T03 EPUB、T04 阅读器、T05 候选与金标准、T06 配置页、T07 适配器与连接测试）
下一任务与理由：T09 联合场景与匿名分组引擎。T07 已给出输出契约与校验，T08 已给出受预算约束的窗口、
证据 ID 集合与依赖哈希；T09 要在单窗口上组装推理流程（场景状态、EXISTING/NEW/UNKNOWN、
临时 ID 映射、身份修订提议与接受策略），是 T10 任务化与 T11 预览的直接前置。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`：fastapi 0.141.1、httpx（运行时）、
  keyring、pydantic 2.13.5、SQLAlchemy 2.1.1、Alembic 1.20.0、python-multipart、jsonschema（dev）、
  pytest 9.1.1、ruff 0.16.9）；Node.js 22.14.0 / npm 10.9.2；React 18.3 / Vite 5.4 / Vitest 2.1 /
  Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、
  前端 `http://127.0.0.1:5173`（代理 `/api`）；模型配置页 `/settings/models`。
- 数据目录、迁移版本与服务状态：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0004`（= head）。
  凭据后端默认 `system`（keyring）；测试/E2E 用 `NDR_CREDENTIAL_BACKEND=session`；
  E2E 另外显式启用 `NDR_ALLOW_FAKE_PROVIDER=1`（仅测试）。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`npm --prefix frontend run test:e2e`。

## 本次改动

- 新增 `ndr/context/` 包：
  - `budget.py`：统一的启发式 token 估算（`estimate_tokens`，method/confidence 明确标注）与
    `BudgetLedger`（逐条记录每个片段、正文用量=记录之和）、`BudgetPolicy`（PLAN 7.3 默认值）、
    `RECHECK_POLICY`、丢弃优先级与 required 集合。
  - `source_selection.py`：`select_evidence` 按保留层级组织片段（目标 → 内部 Gap → 状态 → 重叠 → 外层 Gap），
    处理 `initial/reread` 与 `visible_horizon_cp`（越界证据写入省略记录，必留片段被挡时告警并截到 horizon），
    返回片段、省略记录、警告与账本。
  - `window_builder.py`：`plan_windows` 按必留成本贪心拆窗口（窗口间重叠 + `carry_last_quote_id`），
    超长单条引语独立窗口并标 `oversized_quote`（按完整长度计费、不截断），
    生成窗口/计划的 `dependency_hash` 与统计。
  - `service.py`：`load_window_inputs` / `plan_range` 从数据库（quote/gap/content_node + canonical 文本）
    装配输入并规划窗口（只读，不调用模型）。
- `llm/adapters/chat_completions.py` 的 `estimate_tokens` 改为委托 `context.budget.estimate_tokens`
  （统一口径，避免两套估算漂移）。
- 测试：`tests/unit/test_budget.py` 10 项、`tests/unit/test_context_windows.py` 11 项、
  `tests/integration/test_context_from_book.py` 3 项。
- 文档：新增决策 0010；CONTRACTS 增加第 17 节（内部契约 + 缓存键口径）；README 增加“上下文与预算（T08）”。
- 本任务**不新增端点与迁移**，OpenAPI 未变化。

## 验证证据

- `pytest backend/tests` → **246 passed**（T07 时 222；新增 10 + 11 + 3）。
- `ruff check backend/src backend/tests backend/scripts` → All checks passed；`scripts/verify.ps1` → 退出码 0。
- 门槛逐条核对：
  - **预算边界不是场景边界**：拆窗口后所有窗口 `scene_ref` 相同，窗口结构里没有任何场景主张。
  - **不丢目标对白**：真实书籍上目标覆盖顺序一致、无重复；超长目标独立窗口并报告 `oversized_quote`。
  - **上下文来源可回溯**：片段 ID 全部来自 quote/gap/node 或 `overlap:`/`state:` 前缀。
  - **补入片段计入预算**：窗口正文用量 = 所有片段估算之和；`total = prompt + context + output`。
  - **horizon**：初读模式下越界证据进入 `beyond_visible_horizon` 省略记录且不送模型；重读忽略 horizon，
    两者依赖哈希不同。
- 未验证：真实模型调用（无凭据，T07 已记录 BLOCKED）；本任务只用本地构造与真实导入数据。

## 未完成与已知问题

1. **没有识别结果**：T08 只组织输入；场景/分组/接受策略在 T09，预览与确认在 T11/T12/T13。
2. **token 仍是启发式**：CJK≈1 token/字，低置信度；接入真实分词器或提供方 usage 前不作为计费依据。
3. **`plan_range` 默认只把外层候选当目标**：嵌套引用作为上下文出现；若 T09 需要给嵌套引语打标签，
   用 `top_level_only=False`。
4. **窗口尚未持久化**：`job_windows` 表在 T01 已建好，T10 会把窗口、检查点与依赖哈希落库。
5. **Gap 压缩默认关闭**：`BudgetPolicy.gap_compression=False`；T17 依据真实对比实验才考虑开启。
6. **T07 的真实联调仍 BLOCKED**（无凭据）；系统凭据库真实交互仍未在真机验证（决策 0008）。
7. 导入仍在请求内同步执行；枚举无数据库级 CHECK（0003）；EPUB 的 encoding 用 `xml` 哨兵（0005）。
8. 受限沙箱中 `uv run` 失败，脚本自动回退 `backend\.venv`；`npm --prefix frontend install/ci` 须在 frontend 内执行；
   `alembic.ini` 保持 ASCII；脚本已设置 `PYTHONUTF8=1`。
9. 本会话中 `apply_patch` 与沙箱内命令执行器失效，文件改用 `.tools/newfile.ps1`（无 BOM UTF-8）。
   注意：PowerShell 里不要把多行 here-string 直接当成函数参数；提交信息里不要出现双引号；
   测试夹具不要手写字符偏移（本任务就因此踩坑，改为用 `TEXT.index()` 定位）。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**` 与 `frontend/e2e/fixtures/**` 的原始字节；
  已发布迁移 `0001`～`0004` 不修改；**用户标注与用户密钥永不被自动结果覆盖或回传**。
- 当前接口/schema/数据版本：API 契约版本 `1`；数据库 revision `0004`；输出契约 `schema_version="1.0"`；
  提示词版本 `labeling-1`/`connection-1`；`CONTEXT_POLICY_VERSION="context-1"`；
  `SCANNER_VERSION=quote-scan-1`；`PARSER_VERSION=txt-1`/`epub-1`。
  **这些版本号都会进入依赖哈希/缓存键，改动必须提升版本。**
- 修改公共契约时同步 `docs/CONTRACTS.md`、`docs/openapi.json`、`frontend/src/api/schema.d.ts`、调用方与测试。
- 提交习惯：每完成一部分功能即用 git 提交。