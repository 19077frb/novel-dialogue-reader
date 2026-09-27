# 实现进度账本

更新时间：2026-09-28

四种状态分开记录：`implementation`（实现）、`offline_verification`（离线验证）、
`live_verification`（真实服务/阅读器验证）、`quality_evaluation`（真实作品效果）。
取值为 `NOT_STARTED / IN_PROGRESS / PASS / FAIL / BLOCKED / NOT_APPLICABLE`。

当前没有真实模型凭据与真实小说数据，因此 Live 与 Quality 一列不会出现 PASS；
FakeProvider 或自造样例通过只记入 Offline。

| Task | Implementation | Offline | Live | Quality | Evidence | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| T00 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | `ruff` All checks passed；`pytest backend/tests` 9 passed（当时）；`npm --prefix frontend run typecheck/test/build` 通过；真实 Chromium E2E 2 passed；`scripts/dev.ps1` 启动并经 Vite 代理访问 `/api/health` 200 后 `-Stop` 归零监听 | 已完成 |
| T01 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | `pytest backend/tests` → 41 passed（`test_schema.py`：空库迁移、重复迁移安全、模型与库列不漂移、0001→head 升级保留数据、外键违例、版本冲突、UTC 往返、枚举字符串落库、无明文密钥列、UNKNOWN/锁定/stale/队列状态独立、review_items 目标唯一）；`ruff check backend/src backend/tests backend/scripts` All checks passed；`alembic upgrade head` 可重复执行；真实运行 `dev.ps1` 后 `GET /api/health` → `{"state":"READY","revision":"0002","head_revision":"0002"}`，404 返回契约错误体且 `X-Request-ID` 一致；Playwright 2 passed（`data/run/e2e-t01.log`）；OpenAPI 与前端类型已重新生成 | 进入 T02：TXT 导入、编码与统一文档树 |
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
| T15 | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 证据时点、阅读投影与最终定位回归 |
| T15A | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | EPUB/HTML 导出后端与标准校验 |
| T15B | NOT_STARTED | NOT_STARTED | NOT_APPLICABLE | NOT_APPLICABLE | 待填写 | 导出对话框、样张与下载闭环 |
| T16 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 真实样本评测与可复现实验 |
| T17 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 上下文压缩、局部复核与成本路由 |
| T18 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 完整联调、体验与发布检查 |
| T19 | NOT_STARTED | NOT_STARTED | NOT_STARTED | NOT_STARTED | 待填写 | 启动交付、操作文档与最终交接 |

## 说明

- T00/T01 的 Live/Quality 为 NOT_APPLICABLE：这两个任务不涉及真实模型或真实阅读器。
- 浏览器 E2E 使用真实 Chromium 与真实后端进程，数据来自本地后端、不涉及模型效果，
  因此记入 Offline，不构成 Live 证据。
- T02 起才会读入真实文本；在 T16 之前，任何“准确率/覆盖率”目标都没有证据，保持 NOT_STARTED。
- `data/run/*.log` 是本地运行产物（已忽略提交），可用账本列出的命令复现。
- 已知命令偏差（npm `--prefix … install`、受限沙箱中的 `uv run`、GBK 编码与 alembic.ini）
  见 README“已知命令偏差”与决策 0001/0003。