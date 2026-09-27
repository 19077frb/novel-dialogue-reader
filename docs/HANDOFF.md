# 开发交接

更新时间：2026-09-28
当前任务：T05 候选引语、Gap 与标注样例工具（已完成 implementation / offline_verification）
最近完成任务：T05（此前 T00 骨架、T01 模型与迁移、T02 TXT 导入、T03 EPUB 与资源、T04 书架与阅读器）
下一任务与理由：T06 模型配置后端与设置页。T05 已把原文变成可查询的候选与 Gap，且 `model_profiles`
表与 `credential_mode/credential_ref` 字段在 T01 已就绪；T06 是第 8 节顺序中的下一个任务，
也是后续 T07 适配器与 T11 预览的前置。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`：fastapi 0.141.1、
  pydantic 2.13.5、SQLAlchemy 2.1.1、Alembic 1.20.0、python-multipart 0.0.32、jsonschema（dev）、
  pytest 9.1.1、ruff 0.16.9）；Node.js 22.14.0 / npm 10.9.2；React 18.3 / Vite 5.4 / Vitest 2.1 /
  Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、
  前端 `http://127.0.0.1:5173`（代理 `/api`）；停止用 `-Stop`。
- 数据目录、迁移版本与服务状态：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0004`（= head）。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`npm --prefix frontend run test:e2e`。

## 本次改动

- 后端 `ndr/quotes/`：`delimiters.py`（配对表与弱类型提示）、`ids.py`（稳定 ID 派生）、
  `scanner.py`（栈式嵌套/跨段扫描 + 长度、跨段、硬上限、嵌套上限保护 + 警告与 stats）、
  `gaps.py`（只用外层候选构造 Gap，`decision=UNCERTAIN`，可跨章节）、`service.py`
  （落库/重扫/列表/详情/Gap/定位，含“有用户标注则拒绝覆盖”）。
- 导入流水线：`ingest/service.py` 在写入节点与 source_map 后立即扫描候选（同一事务、无模型调用）。
- API：`api/quotes.py` 新增 `GET /api/books/{id}/quotes|gaps|locate`、
  `POST /api/books/{id}/quotes/scan`（202 + RECOMPUTE 任务）、`GET /api/quotes/{id}`。
- 评测：`ndr/evaluation/gold_standard.py`（Schema 校验、跨字段引用/范围检查、扫描器覆盖率、模板生成）
  与 CLI `backend/scripts/gold_standard.py validate|template|scan`；`jsonschema` 加入 dev 依赖。
- 前端：`api/books.ts` 增加 `fetchQuotes`/`fetchQuoteDetail`；`DocumentRenderer` 支持候选范围
  （含嵌套候选建树）；`ReaderPage` 拉取本章候选、显示“候选引语 N 条（尚未判定说话人）”与显示开关；
  样式新增 `.ndr-candidate` 虚线标记。
- 契约产物：`docs/openapi.json` 与 `frontend/src/api/schema.d.ts` 重新生成（14 条路径）。
- 文档：新增决策 0007；CONTRACTS 增加第 13、14 节；README 增加“候选引语与金标准（T05）”。

## 验证证据

- `pytest backend/tests` → **163 passed**（T04 时 114；新增 23 + 7 + 9 + 10 = 49 项）。
- `ruff check backend/src backend/tests backend/scripts` → All checks passed。
- 金标准工具：`gold_standard.py validate --gold evaluation/examples/minimal-txt-001/gold.json
  --text evaluation/examples/minimal-txt-001/text.txt` → 通过；候选覆盖“精确 5 / 被更大候选包含 0 / 缺失 0”，
  说明 **T00 的坐标约定、扫描器与金标准三者一致**。
- 前端：`npm --prefix frontend run test -- --run` → 17 passed（新增候选标记、嵌套标记、ReaderPage 候选开关）；
  `npm --prefix frontend run test:e2e -- import-reader.spec.ts` → 4 passed（含“阅读页出现候选覆盖并可关闭”）。
- 门槛核对：扫描器不带任何说话人字段（有测试断言）；异常引号不吞章（长度/跨段/硬上限三类用例）；
  重扫幂等且**拒绝覆盖已有用户标注**（409 `USER_LABELING_PRESENT`）；扫描不产生 annotations/scenes/speaker_groups。
- 过程中修复的问题：长度保护原先只有一套阈值，导致 `quote_too_long` 分支不可达（拆成“长度上限丢弃候选”
  与“硬上限放弃开引号”）；`test_source_map.py` 用 mapping 范围（含行尾）去匹配节点范围（不含行尾）而失败
  （改为按 node_id 关联）；E2E 误以为 EPUB 的第一个候选是跨块对白（改用文本过滤定位）；若干行宽与嵌套 if 的 lint。
- 未执行/未验证：**真实模型联调与真实作品效果评测**（无凭据、无真实小说；T06/T07 才会引入适配器）。

## 未完成与已知问题

1. **还没有任何识别结果**：候选只是“这里有一段引号内容”，`kind_hint` 也不代表最终类型；
   场景、说话人分组、颜色/编号、待确认队列都要等 T06 起的模型接入（T09/T11/T12/T13）。
2. **`utterance_id` 恒为 NULL**：合并同一发言需要证据，扫描阶段合并会被 T09 用场景状态替换。
3. **候选扫描粒度为 canonical 行/块**：跨段引语允许 3 个换行以内；更长的跨段引语会被丢弃并警告。
4. **定位接口返回整段映射切片**：调用方若只需要精确 [start,end) 文本，可用响应里的 `text` 字段。
5. **导入仍在请求内同步执行**（返回 202 + job_id，任务多已终态）；T10 改为后台调度。
6. **枚举无数据库级 CHECK**（决策 0003）；`books.active_version_id`/`read_position_version_id` 无外键；
   EPUB 的 `encoding` 用 `"xml"` 哨兵（决策 0005）。
7. **受限沙箱中 `uv run` 失败**，脚本自动回退 `backend\.venv`；`npm --prefix frontend install/ci` 须在 frontend 内执行；
   `alembic.ini` 必须保持 ASCII；脚本已设置 `PYTHONUTF8=1`。
8. 本会话中 `apply_patch` 与沙箱内命令执行器失效，文件改用 `.tools/newfile.ps1`（无 BOM UTF-8）；
   注意：**PowerShell 里不要把多行 here-string 直接当成函数参数**（会破坏文件），要么先赋给变量，要么整文件重写。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/minimal-txt-001/*` 与 `frontend/e2e/fixtures/**` 的原始字节；
  已发布迁移 `0001`～`0004` 不修改；**任何已有用户标注都不能被自动候选/模型结果覆盖**。
- 当前接口/schema/数据版本：API 契约版本 `1`；数据库 revision `0004`；金标准 schema `1.0`；
  `SCANNER_VERSION=quote-scan-1`；`PARSER_VERSION=txt-1`/`epub-1`。
- 修改公共契约时同步 `docs/CONTRACTS.md`、`docs/openapi.json`、`frontend/src/api/schema.d.ts`、调用方与测试。
- 提交习惯：每完成一部分功能即用 git 提交。