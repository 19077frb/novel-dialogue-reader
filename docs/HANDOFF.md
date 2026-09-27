# 开发交接

更新时间：2026-09-28
当前任务：T16 真实样本评测与可复现实验（implementation / offline_verification 完成；live = BLOCKED，quality = BLOCKED）
最近完成任务：T16（此前 T00～T15B：骨架→迁移→TXT→EPUB→阅读器→候选与金标准→配置页→适配器→上下文预算→场景引擎→任务缓存用量→标注投影与预览→人工更正与撤销→确认队列与抽屉→暂停预算与故障恢复→初读身份与码点定位→导出后端与标准校验→导出界面与下载闭环）
下一任务与理由：T17 上下文压缩、局部复核与成本路由。前置是「T16 的评测工具」，现在已经就绪
（清单校验、B0 规则基线、指标、报告、显式 `--allow-live`）；T17 可以做长 Gap 保守筛选、证据补回、
有限局部复核与可选强模型路由，但**启用为默认配置前必须有真实对比证据**，没有数据就保留保守策略并标注未验证。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`。
- 评测命令：`python -m ndr.evaluation validate|run ...`（清单在 `evaluation/manifests/`，配置在 `evaluation/configs/`）。
- 数据目录与迁移：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0005`（= head）。
- 测试/E2E：`NDR_CREDENTIAL_BACKEND=session`；E2E 另开 `NDR_ALLOW_FAKE_PROVIDER=1` 与
  `NDR_FAKE_PROVIDER_LABELS=deterministic`。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`cd frontend; $env:NDR_E2E_BACKEND_CMD='..\backend\.venv\Scripts\python.exe -m ndr'; npx playwright test`。

## 本次改动

- `ndr/evaluation/`：
  - `metrics.py`：纯函数指标（一对一 IoU 匹配、场景错误切断/连接、匹配后分组准确率、同人 pairwise F1、
    覆盖率/拒答率/未知强标率、新人物误建漏建、退化标记、Wilson 区间、目标判定）。
  - `manifest.py`：清单加载 + 校验（作品级划分不重叠、book_id 唯一、金标准结构与引用、work_id 一致）。
  - `configs.py`：B0/B1/B2 配置加载 + 配置指纹（与缓存键同一套规范化哈希）。
  - `baselines.py`：**B0 规则基线**（显式归属表面形式 + 叙述长度切场景；找不到就拒答）。
  - `live.py`：显式真实运行（导入 → 建任务 → 引擎 → 读投影 → 预测），需 `--allow-live` + `--profile-id`。
  - `runner.py` / `__main__.py`：`validate` 与 `run` 子命令、按作品/难例/总体聚合、报告写出。
- `evaluation/`：`manifests/dev.json`（含 README）、`configs/b0|b1|b2.json`（含 README）、
  `reports/dev-b0-offline.json` 与 `dev-b1/b2-notrun.json`（含 README 复现说明）。
- 文档：决策 0020；CONTRACTS 第 28 节；README 增加「评测工具（T16）」。

## 验证证据

- 后端：`pytest backend/tests` → **346 passed**（T15B 时 328；新增 9 + 5 + 4）；`ruff` 全绿。
- 手算样例（`test_evaluation_metrics.py`）：完美预测各指标 1.0；**标签置换不变**；错误分场 `wrong_split=2`
  （场景准确率 0.5）；错误连接 `wrong_join=1`；**全拒答** → coverage 0、`accepted_accuracy=null`、
  `degenerate.all_refusal=true`、`targets_met=null`；**全合并** → pairwise F1 0.5、`missing_groups=1`、
  `targets_met=false`；**漏提取** → recall 0.8、coverage 0.6；**未知强标** → `unknown_force_rate=1.0`
  且不计入已接受准确率；`min_sample=30` 时 `targets_met=null`。
- 清单/配置：仓库清单 `validate` 通过（1 作品 / 1 书 / dev）；坏清单（跨 split + 重复 book_id + 缺金标准）
  三类 error 全部命中；B0 规则基线不再把「少年没有回答」误判成归属。
- 真实生成的报告：`dev-b0-offline.json` → `state=OFFLINE_BASELINE`、`accepted_accuracy=1.0`（2/2）、
  `coverage=0.4`、`pairwise_f1=null`、`sample_sufficient=false`、`targets_met=null`、
  `quality_evidence=false`、`usage_total.calls=0`；`dev-b1/b2-notrun.json` → `NOT_RUN` + 原因。
- 真实运行链路（离线验证）：`--allow-live` + FakeProvider 时评测命令真的跑完「导入 → 任务 → 引擎 → 投影」，
  并因提供方是测试用假提供方而保持 `quality_evidence=false`（全 UNKNOWN → coverage 0、`all_refusal=true`）。
- 门槛逐条核对：**算法指标不是前端模拟数据**✓（纯函数 + 手算用例 + 真实报告）；
  **未达标/样本不足如实报告**✓（`targets_met=null`、`quality_evidence=false`）；
  **不把脚本完成当成质量达标**✓（账本 Quality 列写 BLOCKED，报告里没有任何效果结论）。
- **未验证（BLOCKED）**：真实模型上的 B0/B1/B2 对比、真实作品的 97%/70% 结论——没有凭据、预算与人工样本。

## 未完成与已知问题

1. **真实效果数字为零**：`evaluation/reports/` 里只有离线基线；B1/B2 需要真实凭据与预算。
2. **B0 是粗糙基线**：表面形式规则会把「少女/她」拆成两组；它只用于低成本参照，不代表任何产品能力。
3. **`live.py` 的真实提供方路径未在真实环境跑过**：只验证了「导入→任务→引擎→投影」的链路与守卫
   （FakeProvider + 缺 `--profile-id` 两种情形）；真实提供方失败映射依赖 T07/T14 的错误映射。
4. **名单/划分仍只有一个原创样例**：`test` 划分为空；真实作品要另建清单并保证同一作品不跨 split。
5. **按难例类别的报告只有样本量**：类别级的准确率/覆盖率需要在有真实预测后再聚合（避免用假数据填表）。
6. 其余既有事项：`uv run` 在受限沙箱失败（回退 venv）；`npm --prefix frontend install/ci` 需在包目录内执行；
   `alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`；`apply_patch` 失效时用 `.tools/newfile.ps1` / `.tools/append.ps1`；
   **编辑多行文本前先统一换行符**；E2E 数据目录共享（新夹具内容必须与既有夹具不同，否则被 sha256 去重）。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**`、`frontend/e2e/fixtures/**` 的原始字节；已发布迁移 `0001`～`0005`；
  **`user_locked` 标注与用户密钥**；审计类表只追加；**已有的评测报告不要改写**（要保留历史结论）。
- 当前版本号（进入缓存键/依赖哈希）：API 契约 `1`；数据库 `0005`；输出契约 `1.0`；
  提示词 `labeling-1`/`connection-1`；上下文 `context-1`；场景状态 `scene-state-1`；接受策略 `acceptance-1`；
  引擎 `attribution-engine-1`；调度器 `scheduler-1`；缓存 `cache-1`；扫描器 `quote-scan-1`；
  更正服务 `correction-1`、恢复服务 `recovery-1`、导出快照 `export-snapshot-1`、导出器 `exporter-1`、
  评测 `evaluation-1`、B0 基线 `b0-rule-1`。
- 提交习惯：每完成一部分功能即用 git 提交。