# 实现进度账本

更新时间：2026-09-28

四种状态分开记录：`implementation`（实现）、`offline_verification`（离线验证）、
`live_verification`（真实服务/阅读器验证）、`quality_evaluation`（真实作品效果）。
取值为 `NOT_STARTED / IN_PROGRESS / PASS / FAIL / BLOCKED / NOT_APPLICABLE`。

当前没有真实模型凭据与真实小说数据，因此 Live 与 Quality 一列不会出现 PASS；
FakeProvider 或自造样例通过只记入 Offline。

| Task | Implementation | Offline | Live | Quality | Evidence | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| T00 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | `ruff check backend/src backend/tests backend/scripts` → All checks passed；`pytest backend/tests` → 9 passed；`npm --prefix frontend run typecheck` 通过；`npm --prefix frontend run test -- --run` → 2 passed；`npm --prefix frontend run build` 成功；`playwright test`（真实 Chromium + 真实后端，独立数据目录 frontend/.e2e/data、端口 8795/5273）→ 2 passed（日志 data/run/e2e-t00.log）；`scripts/dev.ps1` 真实启动前后端，经 Vite 代理 `GET /api/health` 返回 200，`-Stop` 后 8765/5173 无监听；金标准样例坐标/摘要/引用校验全部 PASS | 进入 T01：领域模型、数据库迁移与公共契约 |
| T01 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 实现第 3 节枚举与核心表、Alembic 迁移与版本校验 |
| T02 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | TXT 导入、编码纠正与统一文档树 |
| T03 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | EPUB 导入、资源映射与节点限制 |
| T04 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 书架、导入与无模型阅读器 |
| T05 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 候选引语、Gap 与标注样例工具 |
| T06 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | 待填写 | 模型配置后端与设置页 |
| T07 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | 待填写 | 适配器、输出契约与连接测试 |
| T08 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 上下文、预算与证据范围 |
| T09 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 联合场景与匿名分组引擎 |
| T10 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 持久化任务、缓存与用量 |
| T11 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | 待填写 | 真实效果预览与按章处理 |
| T12 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 人工更正、分组修订与撤销 |
| T13 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 待确认队列与阅读页确认抽屉 |
| T14 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 暂停恢复、预算到顶与故障闭环 |
| T15 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 证据时点、阅读投影与定位回归 |
| T15A | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | EPUB/HTML 导出后端与标准校验 |
| T15B | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 导出对话框、样张与下载闭环 |
| T16 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 真实样本评测与可复现实验 |
| T17 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 上下文压缩、局部复核与成本路由 |
| T18 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 完整联调、体验与发布检查 |
| T19 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 启动交付、操作文档与最终交接 |

## 说明

- T00 的 Live/Quality 为 NOT_APPLICABLE：该任务只建立工程骨架，不涉及真实模型或真实阅读器。
- 浏览器 E2E 使用真实 Chromium 与真实后端进程，但页面数据来自本地后端、不涉及模型效果，
  因此记入 Offline，不构成 Live 证据。
- 每条 Evidence 都对应仓库内可复现的命令或文件；缺凭据/真实作品的任务保持 NOT_STARTED，
  不预先打勾。
- 已知命令偏差（npm `--prefix … install`、受限沙箱中的 `uv run`）见 README“已知命令偏差”，
  并对应决策 0001。
- data/run/*.log、data/run/dev-pids.json 等是本地运行产物（已忽略提交），用于当场核对；data/run/ 中的日志可用账本列出的命令复现。
