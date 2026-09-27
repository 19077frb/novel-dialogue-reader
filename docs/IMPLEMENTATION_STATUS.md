# 实现进度账本

更新时间：2026-09-28

四种状态分开记录：`implementation`（实现）、`offline_verification`（离线验证）、
`live_verification`（真实服务/阅读器验证）、`quality_evaluation`（真实作品效果）。
取值为 `NOT_STARTED / IN_PROGRESS / PASS / FAIL / BLOCKED / NOT_APPLICABLE`。

当前没有真实模型凭据与真实小说数据，因此 Live 与 Quality 一列不会出现 PASS；
FakeProvider 或自造样例通过只记入 Offline。

| Task | Implementation | Offline | Live | Quality | Evidence | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| T00 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | `ruff` 全绿；前端 typecheck/test/build 通过；真实 Chromium E2E；`dev.ps1` 启动后 `/api/health` 200 | 已完成 |
| T01 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | 迁移 0001/0002 + 版本校验 + 错误/分页契约；当时 41 passed；`alembic upgrade head` 幂等 | 已完成 |
| T02 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | TXT 导入与编码（严格解码、候选+有损预演）；当时 76 passed；真实 GB18030 样例逐行一致、无 U+FFFD | 已完成 |
| T03 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | EPUB 按 spine 读取、受限节点树、ruby 注音不进正文、受控资源端点；当时 109 passed | 已完成 |
| T04 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | 书架/阅读器/阅读位置；当时 114 passed；E2E 覆盖导入→阅读→书签与编码纠错 | 已完成 |
| T05 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | 候选引语/Gap 扫描与查询、金标准工具；当时 164 passed；T00 样例候选覆盖 5/5；E2E 显示候选覆盖 | 已完成 |
| T06 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | `pytest backend/tests` → **189 passed**（新增 `test_credentials.py` 10 项、`test_model_profiles.py` 15 项：密钥不回传（响应文本断言无密钥/无 api_key/无 credential_ref）、has_key、keep/replace/remove 三态、409 版本冲突、系统凭据库不可用降级为会话并给出 warning、同名配置/被任务引用删除 409、完整端点 Base URL 422、params 密钥 422、**数据目录中不含密钥**、配置操作不产生任何任务、协议能力声明）；`ruff` 全绿；前端 `vitest` **25 passed**（表单三态、密钥不回显、JSON 本地校验、版本冲突刷新、降级警告）；Playwright **8 passed**（新增设置页两个用例：全流程 CRUD + 页面不出现密钥 + 完整端点报错）；`scripts/verify.ps1` 退出码 0。**本任务没有任何真实网络调用**（连接测试属 T07） | 进入 T07：模型适配器、输出契约与连接测试 |
| T07 | PASS | PASS | BLOCKED | NOT_APPLICABLE | `pytest backend/tests` → **222 passed**（新增 `test_llm_contract.py` 18 项 + `test_provider_adapter.py` 15 项）：输出契约解析（坏 JSON/代码块/额外字段/assignment 一致性）、漏目标与未知 ID 拒绝、场景必须由 BREAK 触发、临时人物声明、重试策略边界、提示词版本与数据转义、usage 三口径且未知保持 None、脱敏、FakeProvider 门禁、401/403/404/429/500 与超时映射、坏 JSON/错 schema 拒绝、**真实适配器连不上时报失败而非假成功**、连接测试写入 `inference_runs` 且未知用量为 NULL、草稿测试后密钥被清理且不落库；`ruff` 全绿；前端 `vitest` **29 passed**（含连接测试 UI 四项）、Playwright **9 passed**（含 FakeProvider 用例并断言“没有访问任何真实服务”）；`scripts/verify.ps1` 退出码 0。**Live=BLOCKED：没有真实提供方凭据与预算**，只用 MockTransport 验证了协议路径与错误映射，未做真实模型联调 | 进入 T08：上下文、预算与证据范围 |
| T08 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | `pytest backend/tests` → **246 passed**（新增 `test_budget.py` 10 项 + `test_context_windows.py` 11 项 + `test_context_from_book.py` 3 项）：token 估算统一口径与低置信标注、预留计入、**补入片段全部计入预算**、可选片段按序丢弃、超长引语独立窗口且**原文按完整长度计费不截断**、F05 长叙述不拆窗口也不改场景、F06 短 Gap 照常保留、F12 长场景拆窗口并带重叠与接力、**预算边界≠场景边界**、F17 horizon 只影响初读且依赖哈希不同、窗口 ID/哈希确定、真实导入产出的候选与 Gap 目标全覆盖且跨章节仍带 Gap；`ruff` 全绿；`scripts/verify.ps1` 退出码 0（无新增端点，OpenAPI 未变） | 进入 T09：联合场景与匿名分组引擎 |
| T09 | PASS | PASS | BLOCKED | NOT_APPLICABLE | `pytest backend/tests` → **271 passed**（新增 `test_scene_state.py` 16 项 + `test_attribution_engine.py` 9 项）：UPDATE 不切场景、BREAK 关旧开新并清空参与者、UNCERTAIN 保留待定边界、状态快照往返、编号按首次发言顺序、接受策略（DIRECT→ACCEPTED / 风格与指代→PROVISIONAL / 证据不足→UNKNOWN 且**不建新人**）、非 speech 不污染人物、可见时点取最靠后证据、身份修订四类判定；引擎侧 F09/F07/F05/F06/F14/F12/F08/F17/F13 全部覆盖（跨窗口沿用同一分组、后文合并记录可见时点、锁定对白不被覆盖、坏 JSON 有限重试且不留半成品）；`ruff` 全绿。Live=BLOCKED：**全部用 FakeProvider 验证状态机，没有真实模型**（真实效果属 T16） | 进入 T10：持久化任务、缓存与用量 |
| T10 | PASS | PASS | BLOCKED | NOT_APPLICABLE | `pytest backend/tests` → **283 passed**（新增 `test_cache.py` 4 项 + `test_jobs.py` 8 项）：同语义同缓存键且键里没有 job_id/purpose、提示/策略/模式/horizon 变化会换键、存储往返不覆盖；预览→处理同范围**命中缓存且发送次数不增加**、同幂等键同摘要复用任务、同键不同摘要 409、**已完成窗口不重复调用**、预算到顶不发调用、未知用量不写 0 且已知用量按口径结算、**未知结果不自动重发**且显式 retry 才回 QUEUED、暂停在窗口之间生效、估算纯本地；`ruff` 全绿；前端 typecheck/test/build 通过（类型跟随 JobDetailOut）；OpenAPI 25 条路径。Live=BLOCKED：真实提供方任务执行无凭据；后台工作循环/多进程属 T14 | 进入 T11：真实效果预览与按章处理 |
| T11 | PASS | PASS | BLOCKED | NOT_APPLICABLE | `pytest backend/tests` → **287 passed**（新增 `test_annotations_projection.py` 4 项：FakeProvider 默认 UNKNOWN 时无颜色/编号、有明确归属时给场景内稳定色号且初读 horizon 之下不下发、投影只读且多次请求不新增标注/推理尝试、确定性 FakeProvider 端到端产生 S1 颜色）；`ruff` 全绿。前端 `vitest` → **41 passed**（`DocumentRenderer` 标注 6 项 + `PreviewPage` 4 项 + `ReaderPage` 新增 2 项）、typecheck/build 通过；Playwright → **12 passed**（新增 `preview.spec.ts` 3 项：TXT 估算→试运行着色→原文/标注切换零调用→正式处理 `calls=0` 且缓存命中窗口数=窗口总数；EPUB 预览着色且 ruby 仍在 `<rt>`；按章切换范围同步）。Live=BLOCKED：**没有真实提供方凭据，预览用的是显式启用的确定性 FakeProvider**（离线信号，不是真实效果） | 进入 T12：人工更正、分组修订与撤销后端；T16 仍需真实作品效果评测 |
| T12 | PASS | PASS | BLOCKED | NOT_APPLICABLE | `pytest backend/tests` → **302 passed**（新增 `test_corrections.py` 9 项 + `test_identity_revisions.py` 6 项）：四种说话人更正（含**未处理对白**与主动标记幂等）、跨场景误关联 422、并发旧版本 409 且不改数据（F18）、**撤销越过新修订 409 且保留较新修改**（F18）、`mark_unknown` 不新建分组、模型结果**不覆盖人工锁定**（F14，换模型名绕过缓存后真实调用适配器）、下游 `stale` + `STALE_DEPENDENCY`、Gap `BREAK` 拆场景与撤销、merge/split 与撤销、历史只追加（行数断言）、更正/撤销**零模型调用**（`inference_runs` 行数不变）；`ruff` 全绿 | 进入 T13：待确认队列与阅读页确认抽屉（前端） |
| T13 | PASS | PASS | BLOCKED | NOT_APPLICABLE | 前端 `vitest` → **55 passed**（新增 `CorrectionForm` 5 + `QuoteDetailDrawer` 5 + `ReviewPage` 4）、typecheck/build 通过；Playwright → **15 passed**（新增 `review.spec.ts` 3 项：阅读页入口「标记待确认→锁定未知→撤销」且阅读页仍着色；队列入口「延后→已跳过找回」+ **未处理对白空候选只能新建说话人**；旧版本提交 409 并提示刷新）。新增 `POST /api/quotes/{id}/recheck`（RECHECK 任务、显式配置+预算，后端 `pytest` → **303 passed**）。Live=BLOCKED：复核走的是确定性 FakeProvider（离线），真实提供方复核效果属 T16 | 进入 T14：暂停恢复、预算到顶与故障闭环 |
| T14 | PASS | PASS | BLOCKED | NOT_APPLICABLE | `pytest backend/tests` → **309 passed**（新增 `test_recovery.py` 6 项）：进程重启扫描（超租约 DISPATCHED→未知结果、RUNNING→PARTIAL、PAUSING→PAUSED、已完成窗口保留）、**未知结果不自动重发**（F15）、预算到顶不发调用且恢复动作为 `new_job`（F20）、限流有上限退避重试（默认上限成功 / 上限 0 失败并给 `run`）、提供方超时→`NEEDS_RECONCILIATION` 且未知用量不写 0、缺凭据→`FAILED`+`requires_credential`+`open_settings` 且原文可读；新增 `GET /api/jobs/{id}/recovery`。前端 `vitest` → **59 passed**（新增 `JobPanel` 4 项）、typecheck/build 通过；Playwright → **18 passed**（新增 `recovery.spec.ts` 3 项：预算到顶→提高预算后显式重算成功；缺 Key→明确失败+指向模型配置+原文可读；超时→保留未知转 PARTIAL）。Live=BLOCKED：真实提供方的限流/超时行为无凭据可测（T16/T18） | 进入 T15：证据时点、阅读投影与最终定位回归 |
| T15 | PASS | PASS | BLOCKED | NOT_APPLICABLE | `pytest backend/tests` → **314 passed**（新增 `test_visibility.py` 4 项：F17 初读 horizon 还原「文末才合并」的身份（两种编号/颜色 + 图例两条，越过时点才合并，reread 直接合并，投影只读且行数不变）；F17 端到端离线（FakeProvider `split_then_merge`）；F11 重复对白 ID 不同 + astral 按码点计数（11 码点/13 UTF-16）且每段可 `locate` 回原文；F04 ruby 不进正文、插图成资源、脚本样式不渲染、跨块引语两节点且 synthetic 换行）。前端 `vitest` → **65 passed**（新增 `codepoints` 3 项 + `DocumentRenderer` astral 3 项）、typecheck/build 通过；Playwright → **23 passed**（新增 `reading-visibility.spec.ts` 3 项 + 空候选独立用例 + 无需密钥用例）。本轮修复 3 个真实缺陷：任务完成后阅读页可能显示旧的空投影（改按前缀失效整族查询）、前端按 UTF-16 下标切片导致 emoji/扩展汉字错位、设置页缺少 `credential_mode=none` 选项使本地无鉴权网关无路可走 | 进入 T15A：EPUB/HTML 导出后端与标准校验 |
| T15A | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | EPUB/HTML 导出后端与标准校验 |
| T15B | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 导出对话框、样张与下载闭环 |
| T16 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 真实样本评测与可复现实验 |
| T17 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 上下文压缩、局部复核与成本路由 |
| T18 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 完整联调、体验与发布检查 |
| T19 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 启动交付、操作文档与最终交接 |

## 说明

- T00～T06、T08 的 Live 为 NOT_APPLICABLE：这些任务不涉及真实模型或真实阅读器。
- T09 的 Live = BLOCKED：场景/分组/接受策略用 FakeProvider 离线验证，真实模型效果未验证（属 T16）。
- T07 的 Live = BLOCKED：适配器、错误映射与连接测试已用 MockTransport/FakeProvider 离线验证，
  但**没有真实提供方凭据与预算**，真实模型兼容性未验证；T16 的效果评测同样未开始。
- “真实联调”指本机真实前后端进程 + 真实浏览器 + 真实 TXT/EPUB 文件（含候选覆盖显示），
  仍不使用模型，因此只记入 Offline。
- T11 的 Live = BLOCKED：预览页与阅读页的着色链路已用**确定性 FakeProvider**离线验证，
  但**没有真实提供方凭据与预算**，所以“真实效果预览”只是协议与渲染链路打通，不代表真实识别质量。
- T12 的 Live = BLOCKED：更正/撤销是本地事务（不需要模型），已用真实 SQLite 事务与 FastAPI 请求验证；
  但“更正后重新推理”的真实效果仍无凭据可测（属 T16）。
- T13 的 Live = BLOCKED：队列/抽屉/更正都是本地操作，已用真实前后端 + 真实浏览器验证；
  「局部复核」是真实任务，但模型侧仍是确定性 FakeProvider（离线），真实复核质量属 T16。
- T14 的 Live = BLOCKED：暂停/恢复/预算/恢复动作都是本地状态机（不需要真实模型），
  故障注入用 FakeProvider 的失败脚本站住；“真实提供方的限流与超时行为”仍无凭据可测（属 T16/T18）。
- T15 的 Live = BLOCKED：初读还原、码点定位与定位回归都用真实前后端 + 真实浏览器 + 离线假提供方验证；
  “真实模型在长文中何时揭示身份”仍无凭据可测（属 T16）。
- 导入、阅读、候选覆盖、模型配置、任务与用量、颜色/编号投影均已可用：预览页可做范围估算、小范围试运行、
  原文/标注对比与按章处理，结果直接复用到正式阅读。
  仍缺：导出（T15A/T15B）、真实作品效果评测（T16）、发布前完整联调（T18/T19）。
- `data/run/*.log` 是本地运行产物（已忽略提交），可用账本列出的命令复现。
- 已知命令偏差见 README“已知命令偏差”与决策 0001/0003；EPUB 表示见 0005；前端结构见 0006；
  扫描与金标准见 0007；凭据处理见 0008；适配器与输出契约见 0009；上下文与预算见 0010；
  场景/接受/身份修订见 0011；任务/缓存/用量见 0012；标注投影与预览见 0013；人工更正与撤销见 0014；
  确认抽屉与复核边界见 0015；暂停/预算与故障恢复见 0016；初读身份还原与码点定位见 0017。