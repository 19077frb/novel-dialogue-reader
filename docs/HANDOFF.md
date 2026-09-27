# 开发交接

更新时间：2026-09-28
当前任务：T06 模型配置后端与设置页（已完成 implementation / offline_verification）
最近完成任务：T06（此前 T00 骨架、T01 模型与迁移、T02 TXT、T03 EPUB、T04 阅读器、T05 候选与金标准）
下一任务与理由：T07 模型适配器、输出契约与连接测试。T06 已提供 `model_profiles` CRUD、
凭据服务与 `ProviderAdapter` 接口/能力声明，页面也已就绪；T07 在此之上实现真实
`chat-completions-compatible` 适配器、FakeProvider、输出 schema 校验与连接测试端点。

## 实际运行方式

- 前置环境与已锁定版本：CPython 3.11.4；uv 0.9.0（`backend/uv.lock`：fastapi 0.141.1、
  pydantic 2.13.5、SQLAlchemy 2.1.1、Alembic 1.20.0、python-multipart 0.0.32、keyring（含 pywin32-ctypes）、
  jsonschema（dev）、pytest 9.1.1、ruff 0.16.9）；Node.js 22.14.0 / npm 10.9.2；React 18.3 / Vite 5.4 /
  Vitest 2.1 / Playwright 1.63 + Chromium。
- 启动命令与访问地址：`pwsh -File scripts/dev.ps1` → 后端 `http://127.0.0.1:8765`、
  前端 `http://127.0.0.1:5173`（代理 `/api`）；停止用 `-Stop`。模型配置页：`/settings/models`。
- 数据目录、迁移版本与服务状态：`data/`（`NDR_DATA_DIR` 可覆盖），数据库 revision `0004`（= head）。
  凭据后端默认 `system`（keyring）；测试/E2E 用 `NDR_CREDENTIAL_BACKEND=session` 隔离。
- 验证命令：`pwsh -File scripts/verify.ps1`；E2E：`npm --prefix frontend run test:e2e`。

## 本次改动

- 后端 `ndr/llm/`：
  - `adapter.py`：`ProviderAdapter` 接口（test_connection / generate_labels / estimate_tokens /
    normalize_usage / capabilities）、`AdapterCapabilities`、`ConnectionTestResult`、`UsageRecord`
    （未知用量保持 None）、协议能力表（`chat-completions-compatible` 如实声明不保证 json_schema；
    `fake-provider` 标注仅供测试）。
  - `credentials.py`：`SessionCredentialStore`（进程内存）、`SystemCredentialStore`（keyring 封装，
    不可用即 available=False）、`CredentialService`（store/load/has/remove；系统不可用或写入失败时
    降级为会话并返回 warning；切换/删除时清理另一存储）。
  - `profiles.py`：配置 CRUD（版本校验、同名冲突 409、Base URL 根路径校验、params 禁止密钥字段、
    删除前检查任务引用）、`ProfileView(has_key)`。
- `domain/profiles.py`：创建/变更/输出 schema（响应无任何密钥字段）；`ErrorCode` 增加
  `RESOURCE_CONFLICT`（409）。
- `api/model_profiles.py`：`GET/POST/PATCH/DELETE /api/model-profiles` +
  `GET /api/model-profiles/protocols`；`config.py` 增加 `credential_backend`；
  `app.py` 挂载 `CredentialService` 与新路由；依赖新增 `keyring`。
- 前端：`api/profiles.ts`（查询/写操作）、`pages/ModelSettingsPage.tsx`（配置表单 + 列表 +
  密钥三态 + 错误/冲突/降级提示 + 明确说明连接测试属 T07）、路由与导航新增 `/settings/models`、
  配置页样式（360px～1280px 可用）。
- 契约产物：`docs/openapi.json` 与 `frontend/src/api/schema.d.ts` 重新生成（20 条路径）。
- 文档：新增决策 0008；CONTRACTS 增加第 15 节；README 增加“模型配置（T06）”。
- 测试隔离：`tests/conftest.py` 的 `tmp_settings` 固定 `credential_backend=session`；
  E2E 配置同样设置 `NDR_CREDENTIAL_BACKEND=session`（绝不触碰真实系统凭据库）。

## 验证证据

- `pytest backend/tests` → **189 passed**（T05 时 164；新增 10 + 15）。
  重点：响应文本不含密钥/`api_key`/`credential_ref`；`has_key` 正确；keep/replace/remove 三态；
  409 版本冲突；系统凭据库不可用 → 降级为 session 且带 warning；同名配置与“被任务引用”删除 409；
  完整端点 Base URL 422；params 密钥 422；**数据目录里搜不到密钥**；配置操作不产生任何任务。
- `ruff check backend/src backend/tests backend/scripts` → All checks passed；`scripts/verify.ps1` → 退出码 0。
- 前端：`npm --prefix frontend run test -- --run` → **25 passed**（新增设置页 8 项）；
  `npm --prefix frontend run test:e2e` → **8 passed**（新增 `settings.spec.ts`：新建→编辑→清除→删除全流程，
  断言页面不出现密钥；完整端点 Base URL 报错可理解）。
- 门槛核对：不改源码即可配置 URL/模型/Key（设置页 + API）；默认**没有**任何真实调用
  （`test_creating_profiles_does_not_start_any_job` 断言任务表为空；连接测试端点在 T07）。
- 本轮修复的问题：`llm/__init__.py` 导出名写错（get_profile → get_profile_or_404）；
  `app.py` 路由导入未生效（ruff 拆分 import 后搜索串不匹配）；前端表单在 mutationFn 内做 JSON 校验
  导致错误信息被 onError 覆盖（改为提交前校验并传参）；测试里遗留死代码 helper；若干行宽与导入排序。
- 未执行/未验证：**真实模型连接与识别效果**（无凭据；属 T07 及以后），也未验证任何真实作品的准确率。

## 未完成与已知问题

1. **T07 才做连接测试**：`POST /api/model-profiles/test` 未实现；页面对此有明确说明。
2. **没有识别结果**：候选引语只是“引号内容”，场景/说话人/颜色/编号要等 T09/T11/T12/T13。
3. **系统凭据库行为只在替身上验证**：本机 keyring 可用（available=True），但测试与 E2E 一律用
   session 后端；真实系统凭据库的交互需要在用户机器上手动确认（记录为已知未验证项）。
4. **删除保护基于 jobs.profile_snapshot_json 的模糊匹配**：T10 引入正式的任务-配置关联后应改为结构化检查。
5. **导入仍在请求内同步执行**（202 + job_id，任务多已终态）；T10 改为后台调度。
6. **枚举无数据库级 CHECK**（决策 0003）；`books.active_version_id`/`read_position_version_id` 无外键；
   EPUB 的 encoding 用 xml 哨兵（决策 0005）。
7. **受限沙箱中 uv run 失败**，脚本自动回退 `backend\.venv`；`npm --prefix frontend install/ci` 须在 frontend 内执行；
   `alembic.ini` 必须保持 ASCII；脚本已设置 `PYTHONUTF8=1`。
8. 本会话中 `apply_patch` 与沙箱内命令执行器失效，文件改用 `.tools/newfile.ps1`（无 BOM UTF-8）。
   注意：PowerShell 里不要把多行 here-string 直接当成函数参数；提交信息里不要出现双引号（会让 git 解析失败）。

## 后续约束

- 必须保留的用户修改：`PLAN.md`、`DEVELOPMENT.md` 未改动；不得为通过检查而删减 TXT/EPUB、API 配置、
  真实预览、待定确认或导出功能。
- 不可覆盖的内容：`evaluation/examples/**` 与 `frontend/e2e/fixtures/**` 的原始字节；
  已发布迁移 `0001`～`0004` 不修改；**用户标注与用户密钥永不被自动结果覆盖或回传**。
- 当前接口/schema/数据版本：API 契约版本 `1`；数据库 revision `0004`；金标准 schema `1.0`；
  `SCANNER_VERSION=quote-scan-1`；`PARSER_VERSION=txt-1`/`epub-1`。
- 修改公共契约时同步 `docs/CONTRACTS.md`、`docs/openapi.json`、`frontend/src/api/schema.d.ts`、调用方与测试。
- 提交习惯：每完成一部分功能即用 git 提交。