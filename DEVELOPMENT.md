# 开发说明

本文只记录维护项目所需的核心信息。安装、启动和使用方式见 [README.md](README.md)。

Windows 免安装包由 GitHub Actions 手动构建和发布，流程与本地验收见 [发布指南](docs/RELEASING.md)。二进制仅发布为 Release 附件，不提交构建成品。

## 1. 开发原则

- 原文不可变；模型结果、人工更正和导出快照单独保存。
- 用户确认的结果优先，后续模型任务不能覆盖 `user_locked` 标注。
- 无法判断时保留为未知，不能为了覆盖率自动创建人物。
- 章节、场景和单次模型窗口是不同边界。
- 模型只返回结构化判断，不负责改写原文或直接操作数据库。
- 模型调用必须有明确预算，并记录实际或未知的 token 用量。
- 修改公共 API 时同步更新后端 schema、`docs/openapi.json`、前端生成类型和测试。

## 2. 本地开发

```powershell
uv sync --project backend --all-groups
Push-Location frontend
npm ci
Pop-Location
pwsh -File scripts/dev.ps1
```

数据库迁移：

```powershell
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend alembic -c backend/alembic.ini current
```

生成 API 文件和前端类型：

```powershell
uv run --project backend python backend/scripts/export_openapi.py --output docs/openapi.json
npm --prefix frontend run generate:api
```

完整验证：

```powershell
pwsh -File scripts/verify.ps1
```

## 3. 数据和持久化约束

- 数据库默认位于 `data/ndr.sqlite3`，可通过 `NDR_DATA_DIR` 修改。
- SQLite schema 由 `backend/src/ndr/storage/models/` 定义。
- 数据库变更必须新增 Alembic 迁移，不能修改已经提交的迁移。
- 文本坐标统一使用规范化全文的 Unicode 码点区间 `[start_cp, end_cp)`。
- 时间统一保存为带时区的 UTC 时间。
- 稳定 ID 和界面显示名分离；人物改名不能改变历史引用。
- 缓存键只包含语义输入，不能包含任务 ID 等运行时字段。

## 4. 识别流程

1. TXT/EPUB 导入并转换为统一文档树。
2. 规则扫描候选对白和对白间叙述。
3. 按 token 预算构建处理窗口，同时携带章节人物表、主视角人物、场景状态和最近对白。
4. 模型返回 JSON 结构化结果。
5. 后端校验引用、人物编号、场景切换和输出完整性。
6. 可安全确定的格式问题由程序修复；语义冲突进入有限重试或待确认。
7. 接受的标注写入投影；低置信、未知和失效依赖进入待确认队列。
8. 用户更正会锁定结果，并使依赖它的自动判断失效。

关键规则：

- 场景切换后，旧场景编号不能直接复用；已确认人物应在新场景重新声明。
- 章节人物表是候选目录，不表示所有人物都已在当前场景发言。
- 已确认人物身份不可因模型名称或编号冲突被反向改写。
- 初读模式不能使用阅读位置之后的证据；重读模式可以使用后文。
- 导出读取冻结快照，不调用模型，也不覆盖原始书籍。

## 5. API 和模块边界

- `backend/src/ndr/api/`：HTTP 路由和请求/响应转换。
- `backend/src/ndr/domain/`：Pydantic schema 与枚举，是 API 类型的权威来源。
- `backend/src/ndr/ingest/`：TXT/EPUB 导入。
- `backend/src/ndr/quotes/`：候选对白和 Gap 扫描。
- `backend/src/ndr/context/`：窗口、预算和证据选择。
- `backend/src/ndr/llm/`：模型协议、提示词、schema 与输出校验。
- `backend/src/ndr/scenes/`：场景状态和标注投影。
- `backend/src/ndr/characters/`、`speakers/`：章节人物与说话人身份。
- `backend/src/ndr/corrections/`：人工确认、撤销与失效传播。
- `backend/src/ndr/jobs/`：任务、缓存、重试、恢复和用量。
- `backend/src/ndr/exports/`：EPUB/HTML 快照与生成。
- `frontend/src/api/`：API 调用与生成类型。
- `frontend/src/pages/`、`components/`：页面和交互组件。

完整机器可读接口见 [docs/openapi.json](docs/openapi.json)。

## 6. 模型、安全与成本

- 正式适配器使用 OpenAI Chat Completions 兼容协议。
- Base URL 是 API 根地址，由适配器追加 `/chat/completions`。
- API Key 只存入系统凭据库或会话内存，不进入数据库、日志、缓存或响应。
- 自动测试默认不能访问真实付费模型。
- 重试必须有次数上限；请求结果未知时不能静默自动重发。
- 日志和错误响应必须对密钥、授权头和提供方响应做脱敏。
- 导入的小说和导出文件属于本地数据，不得加入版本控制。

## 7. 测试要求

按改动范围运行最小相关测试，并在提交前运行：

```powershell
pwsh -File scripts/verify.ps1
```

重要行为需要覆盖：

- 原文和码点范围不漂移。
- 用户锁定结果不被覆盖。
- 场景切换和人物编号保持一致。
- 未知结果不会被强行归入某个人物。
- 相同请求幂等，不同请求不会错误共用幂等键。
- 数据库迁移保留已有书籍和人工更正。
- 前端 API 类型与 `docs/openapi.json` 一致。
- 导出正文与原文一致，未处理对白保持原样。

Git 提交格式和自动化修改要求见 [AGENTS.md](AGENTS.md)。
