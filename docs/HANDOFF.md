# 开发交接

更新时间：2026-09-28
当前任务：T15 证据时点、阅读投影与最终定位回归（implementation / offline_verification 完成；live = BLOCKED）
最近完成任务：T15（此前 T00～T14：骨架→迁移→TXT→EPUB→阅读器→候选与金标准→配置页→适配器→上下文预算→场景引擎→任务缓存用量→标注投影与预览→人工更正与撤销→确认队列与抽屉→暂停预算与故障恢复）
下一任务与理由：T15A EPUB/HTML 导出后端与标准校验。T15 已经把「按时点展示的有效投影」、
身份还原、码点定位与阅读进度 API 打通，导出要在此基础上冻结快照（`export_snapshots`/`export_artifacts`）、
按 `position_safe`/`reread` 渲染统一文本样式、打包 EPUB、生成单文件 HTML，并做内部校验 + EPUBCheck 包装。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`）；Node.js 22.14.0 / npm 10.9.2；
  React 18.3 / Vite 5.4 / Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、前端 `http://127.0.0.1:5173`。
- 页面：`/library`、`/books/:id/read`、`/books/:id/preview`、`/books/:id/review`、`/settings/models`。
- 数据目录与迁移：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0004`（= head）。
  测试/E2E：`NDR_CREDENTIAL_BACKEND=session`；E2E 另开 `NDR_ALLOW_FAKE_PROVIDER=1` 与
  `NDR_FAKE_PROVIDER_LABELS=deterministic`。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`cd frontend; $env:NDR_E2E_BACKEND_CMD='..\backend\.venv\Scripts\python.exe -m ndr'; npx playwright test`。

## 本次改动

- 后端：
  - `scenes/engine.py` 与 `corrections/identity.py` 在写 `identity_revisions` 时记录
    `snapshot.revert`（`quotes`: 哪一句原本属于哪个分组；`groups`: 新分组来自哪个旧分组）。
  - `scenes/projection.py` 新增 `horizon_identity_reverts()`：初读且 `visible_from_cp > horizon` 的身份修订
    在**读时**被反向应用（最新优先）；响应新增 `identity_reverts` 计数；投影仍然只读。
  - `llm/adapters/fake.py` 增加仅测试用的 `params.script="split_then_merge"`（先判两个声音、再用窗口末尾证据合并）。
- 前端：
  - 新增 `src/text/codepoints.ts`（`cpLength`/`utf16IndexForCp`/`sliceByCodepoints`）；
    `DocumentRenderer` 的候选、标注、ruby 切片全部改走码点换算，节点范围改用后端权威 `end_cp`。
  - `ReaderPage` 初读时提交 `visible_horizon_cp = 本章末端`，并显示
    `data-testid="reader-horizon"`（含 withheld 数量与身份还原数量）；重读不提交 horizon。
  - `PreviewPage` 任务终态改为按前缀失效 `['annotations']`（此前只失效 horizon=null 的键）。
  - `ModelSettingsPage` 增加「不需要密钥（本地服务）」（`credential_mode=none`）选项。
- 文档：决策 0017；CONTRACTS 第 24 节；README 增加「初读与后文证据（T15）」。

## 验证证据

- 后端：`pytest backend/tests` → **314 passed**（T14 时 309；新增 `test_visibility.py` 4 项）；`ruff` 全绿；
  OpenAPI 与 `docs/openapi.json` 一致。
- 前端：`typecheck` 通过；`vitest` → **65 passed**（T14 时 59）；`vite build` 通过。
- E2E：**23 passed**（T14 时 18）。新增覆盖：
  - `reading-visibility.spec.ts` 3 项：F17（初读第一章两个声音两种颜色/编号 + 提示「身份合并」，
    切重读后合并为同一编号）；F11（`「😀𠮷！」` 与下一句的着色文本逐字正确）；F04（ruby 仍在 `<rt>`，
    「对白」着色正确，跨块发言只出一个编号）。
  - `review.spec.ts` 新增「未处理章节的空候选」独立用例（专用夹具，不再依赖其它用例是否处理过本书）。
  - `settings.spec.ts` 新增「本地无鉴权服务可以显式选择不需要密钥」。
- 门槛逐条核对：
  - **后文身份揭示前不因颜色/图例/候选说明泄露合并关系**：初读 horizon 之下两个分组分开着色与编号，
    图例两条；`identity_reverts` 只用于说明，不泄露分组关系。
  - **投影查询只读、不暗中重新推理**：`test_visibility.py` 断言查询前后标注/历史/身份修订行数不变，
    且投影路径不导入任何适配器。
- 本轮修复的**真实缺陷**（都是用户可见的）：
  1. 任务完成后阅读页可能继续显示旧的空投影（只失效了 horizon=null 的查询键）；
  2. 前端按 UTF-16 下标切片导致 emoji/扩展汉字处颜色与编号错位；
  3. 设置页没有 `credential_mode=none`，使 T14 的「缺凭据」规则对本地无鉴权网关变成死路。
- 未验证（BLOCKED）：真实模型在长文中揭示身份的时点行为（无凭据，属 T16）。

## 未完成与已知问题

1. **Live 仍未打通**：T07/T09～T15 的 Live 都是 BLOCKED；T16 效果评测未开始。
2. **身份还原只覆盖有点（`visible_from_cp`）的修订**：没有时点的旧数据按“已生效”处理（保守，不猜）。
3. **horizon 取「本章末端」**：跨章长场景里，读者滚动到下一章前不会解除该章的遮断；逐屏 horizon（T15 之后的体验优化）可再细化。
4. **`identity_reverts` 只给数量**：界面不展示“哪些合并被隐藏”，避免间接泄露分组关系（需要时可在评审抽屉里单独说明）。
5. **前端码点换算按码点迭代**：对超长文本是 O(n) 线性扫描（每段引语一次），必要时可建索引缓存。
6. **测试夹具新增两个文件**：`frontend/e2e/fixtures/sample-astral.txt`（astral 定位）与
   `sample-review.txt`（未处理章节的空候选）——都是新增，未改动既有夹具字节。
7. 其余既有事项：`uv run` 在受限沙箱失败（回退 venv）；`npm --prefix frontend install/ci` 需在包目录内执行；
   `alembic.ini` 保持 ASCII；脚本设置 `PYTHONUTF8=1`；`apply_patch` 失效时用 `.tools/newfile.ps1` / `.tools/append.ps1`；
   **编辑多行文本前先统一换行符**；E2E 数据目录在同一次运行里共享（新用例要自带夹具/显式选章节/独立 profile）；
   中断的 Playwright 可能留下占用 8795 的进程，重跑前先确认端口空闲。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**`、`frontend/e2e/fixtures/**` 的原始字节；已发布迁移 `0001`～`0004`；
  **`user_locked` 标注与用户密钥**；`corrections` / `annotation_history` / `identity_revisions` / `inference_runs` 只追加。
- 当前版本号（进入缓存键/依赖哈希）：API 契约 `1`；数据库 `0004`；输出契约 `1.0`；
  提示词 `labeling-1`/`connection-1`；上下文 `context-1`；场景状态 `scene-state-1`；接受策略 `acceptance-1`；
  引擎 `attribution-engine-1`；调度器 `scheduler-1`；缓存 `cache-1`；扫描器 `quote-scan-1`；
  更正服务 `correction-1`、恢复服务 `recovery-1`（内部常量，不进入缓存键）。
- 提交习惯：每完成一部分功能即用 git 提交。