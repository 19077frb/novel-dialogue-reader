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
| T09 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 联合场景与匿名分组引擎 |
| T10 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 持久化任务、缓存与用量 |
| T11 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | 待填写 | 真实效果预览与按章处理 |
| T12 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 人工更正、分组修订与撤销 |
| T13 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 待确认队列与阅读页确认抽屉 |
| T14 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 暂停恢复、预算到顶与故障闭环 |
| T15 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 证据时点、阅读投影与最终定位回归 |
| T15A | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | EPUB/HTML 导出后端与标准校验 |
| T15B | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 导出对话框、样张与下载闭环 |
| T16 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 真实样本评测与可复现实验 |
| T17 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 上下文压缩、局部复核与成本路由 |
| T18 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 完整联调、体验与发布检查 |
| T19 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 启动交付、操作文档与最终交接 |

## 说明

- T00～T06、T08 的 Live 为 NOT_APPLICABLE：这些任务不涉及真实模型或真实阅读器。
- T07 的 Live = BLOCKED：适配器、错误映射与连接测试已用 MockTransport/FakeProvider 离线验证，
  但**没有真实提供方凭据与预算**，真实模型兼容性未验证；T16 的效果评测同样未开始。
- “真实联调”指本机真实前后端进程 + 真实浏览器 + 真实 TXT/EPUB 文件（含候选覆盖显示），
  仍不使用模型，因此只记入 Offline。
- 导入、阅读、候选覆盖与模型配置已可用，但**仍没有任何识别结果**：场景、说话人分组、颜色/编号、
  待确认队列要等 T07 起的适配器与 T09/T11/T12/T13。
- `data/run/*.log` 是本地运行产物（已忽略提交），可用账本列出的命令复现。
- 已知命令偏差见 README“已知命令偏差”与决策 0001/0003；EPUB 表示见 0005；前端结构见 0006；
  扫描与金标准见 0007；凭据处理见 0008；适配器与输出契约见 0009；上下文与预算见 0010。