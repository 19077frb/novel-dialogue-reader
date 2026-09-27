# 实现进度账本

更新时间：2026-09-28

四种状态分开记录：`implementation`（实现）、`offline_verification`（离线验证）、
`live_verification`（真实服务/阅读器验证）、`quality_evaluation`（真实作品效果）。
取值为 `NOT_STARTED / IN_PROGRESS / PASS / FAIL / BLOCKED / NOT_APPLICABLE`。

当前没有真实模型凭据与真实小说数据，因此 Live 与 Quality 一列不会出现 PASS；
FakeProvider 或自造样例通过只记入 Offline。

| Task | Implementation | Offline | Live | Quality | Evidence | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| T00 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | `ruff` All checks passed；前端 typecheck/test/build 通过；真实 Chromium E2E 2 passed；`scripts/dev.ps1` 启动并代理访问 `/api/health` 200 后 `-Stop` 归零监听 | 已完成 |
| T01 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | `pytest backend/tests`（当时 41 passed，含空库迁移、0001→head 升级保留数据、外键违例、版本冲突、UTC 往返、无明文密钥列、UNKNOWN/锁定/队列独立）；`ruff` 通过；`alembic upgrade head` 可重复执行 | 已完成 |
| T02 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | 当时 76 passed（`test_txt_ingest.py` 24 + `test_book_import.py` 11）；真实 GB18030 样例经代理导入后 11 个节点与原文逐行一致、无 U+FFFD；错误编码 422 + candidates + lossy preview | 已完成 |
| T03 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | 当时 109 passed（`test_epub_ingest.py` 22 + `test_resources.py` 10）；真实 EPUB 经代理导入：ruby 基底在正文、注音不在正文，图片资源字节与 sha256 一致；越界/符号链接/超限/外链均有拒绝与告警 | 已完成 |
| T04 | PASS | PASS | NOT_APPLICABLE | NOT_APPLICABLE | `pytest backend/tests` → 114 passed（新增 `test_reading_progress.py` 5 项：保存/回读、409 版本冲突、跨书版本 422、越界 422、不产生任务与标注）；前端 `vitest` 13 passed（App/LibraryPage/DocumentRenderer/ReaderPage + `findCurrentStartCp`）；**真实浏览器 E2E 6 passed**（`data/run/e2e-t04.log`）：UTF-8 TXT 导入→阅读→切章→刷新仍停留同一章、EPUB 按 spine 打开且 `<rt>` 注音可见、插图 `naturalWidth>0`（真实经资源端点解码）、GB18030 声明为 UTF-8 → 候选+预演→一键换编码成功、`.md` 提示只支持 .txt/.epub；`ruff` 全绿；`scripts/verify.ps1` 退出码 0；OpenAPI/前端类型已重新生成（9 条路径） | 进入 T05：候选引语、Gap 与标注样例工具 |
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

- T00～T04 的 Live/Quality 为 NOT_APPLICABLE：这些任务不涉及真实模型或真实阅读器。
- “真实联调”指本机真实前后端进程 + 真实浏览器 + 真实 TXT/EPUB 文件，仍不使用模型，因此只记入 Offline；
  T06/T07 之后才会有 provider 相关的 live 项。
- 导入与阅读已可用，但**没有任何识别结果**：颜色/编号、场景、待定队列都要等 T05 起；
  `AnnotationLayer` 现在只是占位容器（有测试保证不出现颜色/编号）。
- `data/run/*.log` 是本地运行产物（已忽略提交），可用账本列出的命令复现。
- 已知命令偏差见 README“已知命令偏差”与决策 0001/0003；EPUB 表示见 0005；前端结构见 0006。