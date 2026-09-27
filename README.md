# 轻小说对话辅助阅读器（Novel Dialogue Reader）

为中文轻小说译本的对白添加颜色/编号，帮助读者辨认说话人；原文不可变，识别结果单独保存，
不确定的对白交给用户确认。产品目标见 [PLAN.md](PLAN.md)，实现规格见 [DEVELOPMENT.md](DEVELOPMENT.md)。

> **当前状态（2026-09-28）**：已完成 **T00 工程骨架** 与 **T01 领域模型/数据库迁移/公共契约**。
> 导入、阅读、模型配置、预览、确认与导出功能 **尚未实现**。
> 真实模型联调（live）与真实作品效果评测（quality）**均未开始**，没有任何准确率数据。
> 进度与证据见 [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)，交接见 [docs/HANDOFF.md](docs/HANDOFF.md)。

## 运行前提

- Python 3.11+（本机验证于 CPython 3.11.4；见 [决策 0001](docs/decisions/0001-runtime-baseline-and-python-version.md)）
- [uv](https://docs.astral.sh/uv/) 0.9.0（后端依赖与 `uv.lock`）
- Node.js 22.14.0 / npm 10.9.2
- 本地服务只监听回环地址：后端 `127.0.0.1:8765`，开发前端 `127.0.0.1:5173`

## 安装

```powershell
uv sync --project backend --all-groups
Push-Location frontend; npm ci; Pop-Location
uv run --project backend alembic -c backend/alembic.ini upgrade head   # 初始化数据库
```

首次安装（尚无 `package-lock.json`）时用 `npm install` 代替 `npm ci`；两者都必须在 `frontend`
目录内执行，原因见“已知命令偏差”。

## 启动

```powershell
pwsh -File scripts/dev.ps1          # 检查依赖 → 执行迁移 → 后台启动前后端
pwsh -File scripts/dev.ps1 -Stop    # 结束由本脚本启动的进程
```

或分别手动启动：

```powershell
uv run --project backend python -m ndr                      # 后端 http://127.0.0.1:8765
npm --prefix frontend run dev -- --host 127.0.0.1           # 前端 http://127.0.0.1:5173（代理 /api）
```

打开 http://127.0.0.1:5173 应看到页面显示后端 `GET /api/health` 返回的真实状态：应用/契约版本，
以及数据库迁移状态（`READY` = 已迁移到仓库 head）。该路径不调用任何模型。

## 数据库与迁移

- 数据库默认位于 `data/ndr.sqlite3`（`NDR_DATA_DIR` 可覆盖），不提交。
- schema 由 `backend/src/ndr/storage/models/` 的 SQLAlchemy 模型定义，迁移在 `backend/migrations/versions/`；
  模型是唯一权威来源，测试会比对模型与实际数据库列是否漂移。
- `0001` 建立核心表（书籍/版本、章节/节点/资源、引语/Gap、场景/分组、标注/历史/身份修订、
  模型配置、任务/窗口/推理尝试/缓存），`0002` 建立待确认队列与人工更正表。
- 迁移只追加。结构变化请新增 `000N_*.py`，不要修改已发布的迁移或用删库重建代替升级。
- 迁移命令（从项目根目录）：

```powershell
uv run --project backend alembic -c backend/alembic.ini upgrade head   # 升级到 head（可重复执行）
uv run --project backend alembic -c backend/alembic.ini current        # 查看当前版本
```

- 应用默认**不会**自动迁移（避免隐式改动用户数据）；需要时用 `NDR_AUTO_MIGRATE=1` 显式开启
  （E2E 用隔离数据目录时使用）。

## 验证

```powershell
pwsh -File scripts/verify.ps1
```

等价的分项命令：

```powershell
uv run --project backend ruff check backend/src backend/tests backend/scripts
uv run --project backend pytest backend/tests
uv run --project backend python backend/scripts/export_openapi.py --output docs/openapi.json --check
npm --prefix frontend run typecheck
npm --prefix frontend run test -- --run
npm --prefix frontend run build
npm --prefix frontend run test:e2e      # Playwright：独立数据目录/端口，真实浏览器
uv run --project backend python backend/scripts/export_openapi.py --output docs/openapi.json
npm --prefix frontend run generate:api
```

## 配置

环境变量（前缀 `NDR_`，也可写入仓库根 `.env`，该文件已被忽略）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `NDR_HOST` | `127.0.0.1` | 后端监听地址，默认仅回环 |
| `NDR_PORT` | `8765` | 后端端口 |
| `NDR_DATA_DIR` | `<仓库>/data` | 原文、SQLite、导出与日志目录，不提交 |
| `NDR_ENVIRONMENT` | `local` | 健康检查中回显的运行环境 |
| `NDR_CORS_ORIGINS` | `["http://127.0.0.1:5173","http://localhost:5173"]` | 允许的开发前端来源（JSON 数组） |
| `NDR_AUTO_MIGRATE` | `false` | 启动时自动迁移到 head（仅建议测试/演示使用） |

前端 `VITE_API_BASE` 可覆盖 API 根地址；开发环境留空并使用 Vite 的 `/api` 代理。

**不要把 API Key 写进 `.env` 或任何提交文件**：模型凭据在 T06/T07 通过系统凭据库或会话密钥管理，
`model_profiles` 表没有任何存放密钥的列（有测试守住这一点）。

## 目录

```text
backend/src/ndr/
  app.py config.py         应用工厂与 NDR_* 配置
  api/                     HTTP 层：健康检查、错误契约、分页、OpenAPI、依赖
  domain/                  枚举与 API schema（契约由后端定义）
  storage/                 ORM 模型、引擎、事务/版本校验、迁移执行
backend/migrations/        Alembic 迁移（0001 核心表、0002 待确认与更正）
backend/tests/             pytest：unit/ 与 integration/
backend/scripts/           export_openapi.py 等运维脚本
frontend/src/              React + TypeScript 前端（页面与组件随任务补齐）
frontend/tests/            Vitest + React Testing Library
frontend/e2e/              Playwright（独立数据目录与端口）
docs/                      CONTRACTS、IMPLEMENTATION_STATUS、HANDOFF、decisions、openapi.json
evaluation/                金标准 schema、原创样例、标注指南
scripts/                   dev.ps1、verify.ps1
data/                      运行数据（忽略提交）
```

## 已知命令偏差（相对 DEVELOPMENT.md 2.3）

1. `npm --prefix frontend ci` / `npm --prefix frontend install` 在本仓库不生效：仓库根目录没有
   `package.json`，npm 10.9.2 会把安装目标解析到根目录并报 ENOENT。安装类命令请在 `frontend`
   目录内执行（`Push-Location frontend`）。`npm --prefix frontend run <script>` 正常可用。
2. 受限沙箱（如某些自动化环境）中 `uv run` 可能因无法打开 uv 缓存中的 `.git` 标记而失败。
   `scripts/verify.ps1` 与 `scripts/dev.ps1` 会自动回退到已同步的 `backend\.venv`；
   请勿据此认为 `uv` 命令本身有误。
3. npm 依赖缓存写在 `frontend/.npm-cache`（由 `frontend/.npmrc` 指定，已忽略提交）；
   uv 缓存写在 `.uv-cache`（`backend/pyproject.toml` 的 `[tool.uv] cache-dir`）。
4. 项目正文与文档都是 UTF-8，而 Windows 默认编码可能是 GBK：`scripts/*.ps1` 会设置
   `PYTHONUTF8=1`；`backend/alembic.ini` 因此保持纯 ASCII（Alembic 用平台编码读配置）。

## 隐私与安全

- 原文、数据库、导出成品与日志都在 `data/`，已忽略提交。
- API 只监听回环地址；跨域仅允许配置的本地前端来源。
- 未经用户明确操作，不发起真实模型调用；测试默认使用 FakeProvider（T07 引入）。