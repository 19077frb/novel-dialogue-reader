# T18 验证报告：完整联调、体验与发布检查

更新时间：2026-09-28

**结论**：功能 = 通过（离线 + 真实前后端/浏览器 E2E 证据齐全，含 T19 从零初始化与同源启动）；
稳定性 = 通过（含边界、故障路径与升级保留数据）；
**live = BLOCKED**（环境没有真实提供方凭据，网络受限）；**quality = BLOCKED**（没有人工确认的真实作品样本，
B1–B4 全部 `NOT_RUN`）。本报告不把 FakeProvider 或离线基线当作真实能力证据。
本报告同时记录 T18（联调/体验）与 T19（交付/升级）的验证结果；T19 的从零走查见第 10 节。

## 1. 范围与方法

- **真实链路**：真实后端进程（`python -m ndr`，隔离数据目录 + 迁移到 head）、真实 Chromium（Playwright）、
  真实 TXT/EPUB 文件。模型侧只用**显式启用**的确定性 FakeProvider（`NDR_ALLOW_FAKE_PROVIDER=1`，
  不发任何网络请求），因此「链路是否连通」可验证，「识别质量」不可验证。
- **离线链路**：`pytest`、`ruff`、OpenAPI 导出与比对、前端 typecheck/单测/build、API 类型再生成比对。
- 所有命令都在本机可重跑；E2E 每次使用全新数据目录（`.e2e/data-<run-id>`），不接触用户书库。

## 2. 功能（Functionality）

| 检查项 | 证据 | 结果 |
| --- | --- | --- |
| TXT：导入 → 估算 → 处理 → 阅读着色 → 导出 HTML/EPUB → 下载件离线可读 | `e2e/full-flow.spec.ts`（TXT 用例，新增） | 通过 |
| EPUB：导入（spine/ruby/插图）→ 处理 → 导出 EPUB/HTML | `e2e/full-flow.spec.ts`（EPUB 用例）+ `import-reader.spec.ts` + `export.spec.ts` | 通过 |
| 四项核心交互（导入 / 阅读 / 预览与处理 / 确认与更正）都连真实后端 | `import-reader.spec.ts`(4) `preview.spec.ts`(3) `review.spec.ts`(4) `reading-visibility.spec.ts`(3) | 通过 |
| 导出对话框全流程（范围/样式/初读策略/样张/生成/校验/下载） | `export.spec.ts`(5) | 通过 |
| 任务恢复（暂停/预算到顶/超时未知/缺 Key） | `e2e/recovery.spec.ts`(3) + `backend/tests/integration/test_recovery.py`(6) | 通过 |
| 接口类型一致性：前端类型 = 当前后端契约 | `npm --prefix frontend run check:api`（用 `docs/openapi.json` 重新生成 `src/api/schema.d.ts` 后逐字节比对） | 通过 |
| OpenAPI 与应用一致 | `scripts/verify.ps1` 的 `openapi 与 docs/openapi.json 一致` | 通过 |
| 导出件离线可读（无 http(s)、无 `<script>`，编号可辨认） | `export.spec.ts` + `full-flow.spec.ts` 断言下载件内容 | 通过 |

## 3. 稳定性（Stability）

| 检查项 | 证据 | 结果 |
| --- | --- | --- |
| 后端 lint 与测试 | `ruff check backend/src backend/tests backend/scripts` 全绿；`pytest backend/tests` → **373 passed** | 通过 |
| 前端 typecheck / 单测 / build | `npm run typecheck`；`npm run test -- --run` → **72 passed**；`npm run build` | 通过 |
| 全量 E2E | `npx playwright test` → **33 passed**（T15B 时 28，T18 新增 5） | 通过 |
| 资源/导出路径边界（`..`、编码越界、篡改记录） | `backend/tests/integration/test_boundaries.py`(3) | 通过 |
| 密钥边界 | `test_model_profiles.py`（响应无密钥/无 `credential_ref`、数据目录无密钥、`params` 密钥拒绝） | 通过 |
| 360px / 1280px 布局（5 个页面无横向溢出，关键控件可点） | `e2e/a11y-layout.spec.ts`（布局用例） | 通过 |
| 可访问性（语言/标题/可访问名称/alt/跳转链接/对话框键盘语义） | `e2e/a11y-layout.spec.ts`(2) + `tests/ExportDialog.test.tsx`/`tests/QuoteDetailDrawer.test.tsx` 各 1 项 | 通过 |
| 故障与未知结果不自动重发 | `test_recovery.py` + `recovery.spec.ts`（F15/F20） | 通过 |

未覆盖/受限的稳定性项：真实提供方的限流、超时与错误码映射仍只在 MockTransport/FakeProvider 上验证
（T07/T14 记录），真实环境未验证。

## 4. Live（真实联调）

- **本机真实联调：通过（不含真实模型）**。真实后端 + 真实浏览器 + 真实 TXT/EPUB 的全流程（导入、阅读、
  预览处理、更正、导出、下载、恢复）都已跑通，证据是上面的 E2E 清单与下载件内容断言。
- **真实提供方受预算端到端试用：BLOCKED**。本机检查证据：
  - 环境变量里没有任何提供方 API Key（`API_KEY|OPENAI|ANTHROPIC|DASHSCOPE|MOONSHOT|DEEPSEEK|GEMINI|AZURE` 全部为空）；
  - 仓库数据目录不存在（没有保存过的模型配置/凭据）；
  - 网络受限（`https://github.com` HEAD 请求 6 秒超时），无法下载 EPUBCheck 或安装阅读器。
- 因此 `python -m ndr.evaluation run … --allow-live --profile-id <id>` 无法执行；报告里保留 `NOT_RUN`
  与原因，不用 FakeProvider 冒充。

## 5. 质量（Quality）

- **BLOCKED**：没有真实模型凭据、预算与人工确认的真实作品样本。
  - `evaluation/reports/dev-b1-notrun.json` ～ `dev-b4-notrun.json` 全部 `NOT_RUN`；
  - `quality_evidence=false`、`targets_met=null`（97%/70% 目标既未达成也未宣布）；
  - T17 的压缩/复核/路由只有离线回归与 `loss` 证据账（`gaps_scanned=2, compressed_gaps=0`），
    默认策略仍保持保守 `context-1`。
- 质量结论必须等 B1–B4 真实运行后才能给出；本报告不做任何效果判断。

## 6. 本次发现并修复的问题

| 问题（真实发现） | 修复 | 证据 |
| --- | --- | --- |
| 导出对话框把 `role="dialog"` 放在背景层，没有 `aria-modal`、Escape 关闭与焦点管理 | 语义移到对话框本体 + `aria-labelledby`、打开移入焦点、Escape 关闭、关闭后归还焦点 | 提交 `81d5e30`；单测 + `a11y-layout.spec.ts` |
| 确认抽屉没有对话框语义、Escape 不能关闭 | `role="dialog"` + `aria-labelledby` + Escape + 焦点归还 | 同上 |
| 异步状态（任务状态、导出状态）对读屏用户不可感知 | `role="status"` + `aria-live="polite"` | 同上 |
| 没有跳转链接、导航不标明当前页、焦点环不统一 | 新增「跳到主要内容」、`NavLink` 的 `aria-current="page"`、统一 `:focus-visible` 外框 | 同上 |
| 越界路径（被篡改的记录/源文件）会让下载或资源读取抛 500 | 改为契约错误（404/409），且不回显磁盘路径 | 提交 `adc0592`；`test_boundaries.py`(3) |
| 前端 API 类型与 OpenAPI 可能悄悄漂移 | 新增 `check:api`（重新生成后逐字节比对，已验证篡改时会失败）并接入 `verify.ps1` | 同上 |

## 7. 残余阻塞与下一任务

1. **EPUBCheck 未运行**：`tools/epubcheck/epubcheck.jar` 不存在，且网络受限无法下载；导出报告里
   标准检查状态如实为 `NOT_RUN`（内部检查与结构断言仍全部通过）。
2. **≥2 款独立 EPUB 阅读器试读未做**：本机未安装任何独立阅读器（Calibre/Thorium/Adobe 均不存在），
   也无法联网安装；不使用浏览器样张代替。
3. **真实提供方试用与质量评测 BLOCKED**（见第 4、5 节）。
4. 非阻塞遗留：`uv run` 在受限沙箱不可用（回退 `backend\.venv`）；评测的强模型路由还没有配置入口
   （`strong_profile_id` 只能由任务范围传入）。
5. **T19 交付已完成**（见第 10 节）：README（依赖检查/初始化迁移/生产同源启动/示例配置/升级/未完成项与复现步骤）、
   `.env.example`（不含密钥）、`scripts/serve.ps1`（同源单端口）、升级保留数据的自动化测试都已就位。
   **T00–T19 计划内任务全部完成**；剩下的只有上表这些「需要外部条件」的验证项，不要把它们当作已通过。

## 8. 离线 / E2E 报告索引

| 报告 | 内容 | 状态 |
| --- | --- | --- |
| `evaluation/reports/dev-b0-offline.json` | B0 规则基线（无 LLM）真实离线指标 | `OFFLINE_BASELINE`，`quality_evidence=false` |
| `evaluation/reports/dev-b1-notrun.json` … `dev-b4-notrun.json` | B1–B4 真实运行 | `NOT_RUN`（缺少凭据/预算） |
| `evaluation/reports/dev-context-loss.json` | T17 压缩证据账（删了什么、是否删到 `must_keep`） | 离线证据，非效果数字 |
| `evaluation/ablations.md` | B2/B3/B4 消融口径与判定门槛 | 说明文档，结论未验证 |
| `docs/openapi.json` | 后端接口契约（`verify.ps1` 校验与应用一致） | 与代码一致 |
| Playwright（本报告第 2、3 节） | 33 项 E2E：功能、恢复、可访问性、布局 | 全部通过（真实前后端 + Chromium） |

## 9. 复现命令

```powershell
# 全量检查（ruff / pytest / OpenAPI / 前端类型一致性 / 前端单测 / 构建）
pwsh -File scripts/verify.ps1

# 全量端到端（真实后端 + 真实 Chromium + 隔离数据目录）
cd frontend
$env:NDR_E2E_BACKEND_CMD = '..\backend\.venv\Scripts\python.exe -m ndr'
npx playwright test

# 单独跑 T18 新增用例
npx playwright test e2e/full-flow.spec.ts e2e/a11y-layout.spec.ts

# 接口类型一致性（失败会提示重新生成）
npm --prefix frontend run check:api

# 真实提供方试用（需要凭据与预算；本机为 BLOCKED）
python -m ndr.evaluation run --manifest evaluation/manifests/dev.json `
  --config evaluation/configs/b2.json --profile-id <id> --allow-live `
  --output evaluation/reports/dev-b2-live.json
```

## 10. T19 交付验证（从零初始化、同源启动、升级）

### 10.1 全新数据目录 + 同源单端口启动（实测）

```powershell
$tmp = Join-Path $env:TEMP ("ndr-walkthrough-" + [guid]::NewGuid().ToString('N').Substring(0,8))
pwsh -File scripts/serve.ps1 -DataDir $tmp -Port 8899 -SkipBuild -AllowFakeProvider
```

实测结果（真实进程 + HTTP）：

| 检查 | 结果 |
| --- | --- |
| 迁移（全新目录） | `alembic upgrade head` 依次执行 `0001`～`0005`，无错误 |
| `GET /` | 200，返回前端构建产物（`text/html`） |
| `GET /library`（深链接） | 200，返回同一份 `index.html`（前端路由接管） |
| `GET /api/health` | 200：`database.state=READY`、`revision=0005`、`head_revision=0005` |
| `GET /api/unknown` | 404 契约错误信封（SPA 回退不会吞掉 `/api/**`） |

### 10.2 从零走通「导入 → 处理 → 确认 → 导出」（实测）

在同一实例上按 README 的 API 顺序执行（`evaluation/examples/minimal-txt-001/text.txt`，仓库原创样例）：

| 步骤 | 实测结果 |
| --- | --- |
| `POST /api/books/import` | 202，`chapter_count=1`、`node_count=7`、`canonical_length_cp=97` |
| `POST /api/model-profiles`（fake-provider，离线演示） | 201 |
| `POST /api/jobs`（process，`run_now=true`） | 终态 `COMPLETED`，`calls=1`（真实调度器 + 真实数据库） |
| `GET /api/books/{id}/quotes` | 返回 3 条候选，首条 `「雨停了。」` |
| `POST /api/quotes/{id}/corrections`（assign_existing S1） | 201，标注版本 1 → 2（真实人工确认写入） |
| `GET /api/books/{id}/annotations?reading_mode=reread` | 5 条投影项、图例 `S1` |
| `POST /api/books/{id}/exports/preview` | 快照 `78e4f6af-…`，警告如实给出「4 条对白已过期（stale）」 |
| `POST /api/books/{id}/exports`（html） | `COMPLETED`，下载 2494 字节；无 `http(s)`、无 `<script>`、含 `〔S1〕` 与 `「雨停了。」` |
| `POST /api/books/{id}/exports`（epub） | `COMPLETED`，下载 2780 字节；zip 头 `PK`，头部含 `mimetype` |

说明：模型侧用的是**显式启用的测试用假提供方**（`-AllowFakeProvider`，不联网），因此这一步证明的是
「启动、迁移、导入、调度、确认、导出、下载」这条链路可用，**不是**真实识别质量。

### 10.3 升级测试（自动化）

- `backend/tests/integration/test_upgrade_preserves_data.py`：导入书籍 → 写入 `user_locked` 人工确认 +
  标注历史 + 待确认项 → 重跑迁移 → 书籍/标注/历史/队列逐项核对，`GET /api/books/{id}` 与候选列表仍可用。
- `backend/tests/integration/test_schema.py::test_existing_database_upgrades_without_data_loss`：`0001 → head`
  的真实升级路径（旧工具链写入的数据保留）。
- 迁移策略与备份/回滚步骤见 README「数据与版本升级」：只追加、可重复执行、不自动迁移（需显式执行或
  `NDR_AUTO_MIGRATE=1`）。

### 10.4 T19 相关门禁

- `ruff` 全绿；`pytest backend/tests` → **376 passed**（新增 `test_static_site.py` 2 项、
  `test_upgrade_preserves_data.py` 1 项）。
- `scripts/verify.ps1` 退出码 0（OpenAPI 与前端 API 类型一致；前端 72 项单测与 build 通过）。
- 全量 E2E 仍为 **33 passed**（前端未改动）。
- 交付说明：[`README.md`](../README.md)（依赖检查/初始化迁移/同源启动/示例配置/升级/未完成项与复现步骤）、
  [`.env.example`](../.env.example)（不含密钥）。