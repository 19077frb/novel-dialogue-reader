# 开发交接

更新时间：2026-09-28
当前任务：T09 联合场景与匿名分组引擎（implementation / offline_verification 完成；live = BLOCKED）
最近完成任务：T09（此前 T00～T08：骨架→模型迁移→TXT→EPUB→阅读器→候选与金标准→配置页→适配器与连接测试→上下文预算）
下一任务与理由：T10 持久化任务、缓存与用量。T09 已把「一次模型输出 → 场景/分组/标注」这条路走通，
T10 需要把它任务化：调度与窗口检查点、profile 快照、缓存指纹、用量与预算预留、幂等创建、状态查询，
并复用 T08 的 `job_windows` 与 `dependency_hash`。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、
  前端 `http://127.0.0.1:5173`（代理 `/api`）；模型配置页 `/settings/models`。
- 数据目录、迁移版本与服务状态：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0004`（= head）。
  测试/E2E 用 `NDR_CREDENTIAL_BACKEND=session`；E2E 另开 `NDR_ALLOW_FAKE_PROVIDER=1`（仅测试）。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`npm --prefix frontend run test:e2e`。

## 本次改动

- `ndr/scenes/`：
  - `state.py`：`SceneState`（可序列化，`SCENE_STATE_VERSION="scene-state-1"`）与转移规则
    （CONTINUE/UPDATE 不切、BREAK 关旧开新并清空参与者、UNCERTAIN 记 `PENDING_BOUNDARY` 与 `unresolved`）。
  - `acceptance.py`：`acceptance-1` 冷启动策略（DIRECT→ACCEPTED、风格/指代→PROVISIONAL、
    证据不足→UNKNOWN 且不建人、非 speech 无 speaker）与 `compute_visible_from_cp`（后端算可见时点）。
  - `engine.py`：`apply_window` 按 §4.5 六步执行（解析→程序校验→锁定检查→可见时点→接受→事务落库），
    写 scenes / speaker_groups / annotations / annotation_history / scene_memberships /
    identity_revisions / review_items；支持身份合并（带证据才自动应用）。
  - `runner.py`：`run_window` 组装提示 → 调用适配器 → 校验 → **有限重试（额外 1 次）** → 应用；
    每次尝试记录 usage（T10 落库）。
- `ndr/speakers/`：`groups.py`（匿名分组注册表：编号按首次可靠发言顺序、UNKNOWN 不建组、
  临时引用映射）、`revisions.py`（身份修订接受策略：明确证据且不碰人工锁定才自动应用）。
- 校验与适配器修正：`LabelingTargets` 允许 `EXISTING` 引用**同一窗口刚声明**的临时人物；
  `RetryPolicy.should_retry` 改为“额外重试次数”语义；FakeProvider 修复重复取件 bug 并支持返回原始字符串
  （模拟坏 JSON）；`run_window` 在校验前剥离适配器的 `_usage` 旁路字段。
- 测试：`tests/unit/test_scene_state.py` 16 项、`tests/integration/test_attribution_engine.py` 9 项。
- 文档：新增决策 0011；CONTRACTS 增加第 18 节（含 T10 需要落库的字段）；README 增加“场景与匿名分组（T09）”。
- 本任务**不新增端点与迁移**。

## 验证证据

- `pytest backend/tests` → **271 passed**（T08 时 246；新增 16 + 9）；`ruff` 全绿；`scripts/verify.ps1` 退出码 0。
- 门槛逐条核对（均以 FakeProvider 驱动、数据库真实事务）：
  - **同一场景跨窗口保持身份**：窗口 1 建立 S1，窗口 2 用稳定 ID 继续标注 → 只有一个 `speaker_groups` 行，
    两条标注 `speaker_id` 相同，`scene_id` 不变。
  - **未知不制造新人**：三条证据不足的对白 → 全部 `UNKNOWN`、`speaker_groups` 为空、
    产生 `UNKNOWN_SPEAKER` 待确认项。
  - **UPDATE 不切场景**：UPDATE 后仍只有一个 `scenes` 行且状态 OPEN；BREAK 才关闭旧场景（写 `end_cp`）
    并开新场景（`state.scene_ref` 采用模型声明的 `scene_2`）。
  - **后文证据具备可见时点**：带证据的合并写 `identity_revisions.visible_from_cp = 揭示位置`，
    合并后标注指向幸存分组；初读投影（T15）据此不会提前同色。
  - 另有：F07 三人与连续同人、F14 锁定对白不被覆盖且不写历史、F13 坏 JSON 只重试一次并拒绝提交、
    坏→好重试后接受、整书窗口规划回归。
- **未验证（BLOCKED）**：真实模型效果（T16 的独立作品评测）、真实提供方联调（T07 已记录无凭据）。

## 未完成与已知问题

1. **界面还没有颜色/编号**：T09 只写数据；阅读页显示标注是 T11/T15，待确认队列是 T13。
2. **接受策略未校准**：`cold_start=True` 固定生效；T16 拿到真实评测后才能放宽 COREFERENCE 等分支。
3. **身份拆分（split）只记录不自动应用**：当前只在明确证据下自动合并；拆分留给 T12 的人工修订路径。
4. **评审项没有去重策略细化**：同一 quote 的多个 reason 会各建一条；T13 需要在 UI 上合并展示。
5. **`review_items.candidates_json` 结构随 reason 变化**：T13 需要按 reason 定义展示契约。
6. **T10 尚未落地**：窗口/检查点/usage 仍未持久化到 `job_windows`/`jobs`/`inference_runs`。
7. 已知环境事项：受限沙箱中 `uv run` 失败（脚本回退 `backend\.venv`）；`npm --prefix frontend install/ci`
   须在 frontend 内执行；`alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`。
8. `apply_patch` 与沙箱内命令执行器在本会话失效，文件改用 `.tools/newfile.ps1`（无 BOM UTF-8）。
   注意：PowerShell 里不要把多行 here-string 直接当函数参数；提交信息不要带双引号；
   **测试窗口里的目标必须全部被标注**（否则校验报 `missing_targets`，本任务踩过这个坑）。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**` 与 `frontend/e2e/fixtures/**` 的原始字节；
  已发布迁移 `0001`～`0004` 不修改；**`user_locked` 的标注与用户密钥永不被自动结果覆盖或回传**。
- 当前接口/schema/数据版本：API 契约版本 `1`；数据库 revision `0004`；输出契约 `schema_version="1.0"`；
  提示词 `labeling-1`/`connection-1`；上下文 `context-1`；场景状态 `scene-state-1`；接受策略 `acceptance-1`；
  引擎 `attribution-engine-1`；`SCANNER_VERSION=quote-scan-1`。**这些版本号进入缓存键/依赖哈希。**
- 修改公共契约时同步 `docs/CONTRACTS.md`、`docs/openapi.json`、`frontend/src/api/schema.d.ts`、调用方与测试。
- 提交习惯：每完成一部分功能即用 git 提交。