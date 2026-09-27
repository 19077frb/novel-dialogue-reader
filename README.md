# 轻小说对话辅助阅读器（Novel Dialogue Reader）

为中文轻小说译本的对白添加颜色/编号，帮助读者辨认说话人；原文不可变，识别结果单独保存，
不确定的对白交给用户确认。产品目标见 [PLAN.md](PLAN.md)，实现规格见 [DEVELOPMENT.md](DEVELOPMENT.md)。

> **当前状态（2026-09-28）**：仅完成 **T00 工程骨架与执行账本**。
> 导入、阅读、模型配置、预览、确认与导出功能均 **尚未实现**。
> 真实模型联调（live）与真实作品效果评测（quality）**均未开始**，也没有任何准确率数据。
> 进度与证据见 [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)，交接见 [docs/HANDOFF.md](docs/HANDOFF.md)。

## 运行前提

- Python 3.11+（本机验证于 CPython 3.11.4；见 [决策 0001](docs/decisions/0001-runtime-baseline-and-python-version.md)）
- [uv](https://docs.astral.sh/uv/) 0.9.0（用于后端依赖与 `uv.lock`）
- Node.js 22.14.0 / npm 10.9.2
- 本地服务只监听回环地址：后端 `127.0.0.1:8765`，开发前端 `127.0.0.1:5173`

## 安装

```powershell
uv sync --project backend --all-groups
Push-Location frontend; npm ci; Pop-Location
```

首次安装（尚无 `package-lock.json`）时用 `npm install` 代替 `npm ci`；两者都必须在 `frontend`
目录内执行，原因见“已知命令偏差”。

## 启动

```powershell
pwsh -File scripts/dev.ps1          # 检查依赖、迁移（若已建立）并后台启动前后端
pwsh -File scripts/dev.ps1 -Stop    # 结束由本脚本启动的进程
```

或分别手动启动：

```powershell
uv run --project backend python -m ndr                      # 后端 http://127.0.0.1:8765
npm --prefix frontend run dev -- --host 127.0.0.1           # 前端 http://127.0.0.1:5173（代理 /api）
```

打开 http://127.0.0.1:5173 应看到页面显示后端 `GET /api/health` 返回的真实状态
（应用版本、契约版本、数据库状态）。该路径不调用任何模型。

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

前端 `VITE_API_BASE` 可覆盖 API 根地址；开发环境留空并使用 Vite 的 `/api` 代理。

**不要把 API Key 写进 `.env` 或任何提交文件**：模型凭据在 T06/T07 通过系统凭据库或会话密钥管理。

## 目录

```text
backend/src/ndr/        后端包（app.py、config.py、api/…）
backend/tests/          pytest：unit/ 与 integration/
backend/scripts/        export_openapi.py 等运维脚本
frontend/src/           React + TypeScript 前端（页面与组件随任务补齐）
frontend/tests/         Vitest + React Testing Library
frontend/e2e/           Playwright（T04 起启用）
docs/                   CONTRACTS、IMPLEMENTATION_STATUS、HANDOFF、decisions、openapi.json
evaluation/             金标准 schema、原创样例、标注指南
scripts/                dev.ps1、verify.ps1
data/                   运行数据（忽略提交）
```

## 已知命令偏差（相对 DEVELOPMENT.md 2.3）

1. `npm --prefix frontend ci` / `npm --prefix frontend install` 在本仓库不生效：仓库根目录没有
   `package.json`，npm 10.9.2 会把安装目标解析到根目录并报 ENOENT。安装类命令请在 `frontend`
   目录内执行（`Push-Location frontend`）。`npm --prefix frontend run <script>` 正常可用。
2. 受限沙箱（如某些自动化环境）中 `uv run` 可能因无法打开 uv 缓存中的 `.git` 标记而失败。
   `scripts/verify.ps1` 会自动回退到已同步的 `backend\.venv`；请勿据此认为 `uv` 命令本身有误。

3. npm 依赖缓存写在 `frontend/.npm-cache`（由 `frontend/.npmrc` 指定，已忽略提交），
   避免受限环境访问用户级缓存目录。`backend` 同理使用 `.uv-cache`（见 `backend/pyproject.toml`
   的 `[tool.uv] cache-dir`）。
## 隐私与安全

- 原文、数据库、导出成品与日志都在 `data/`，已忽略提交。
- API 只监听回环地址；跨域仅允许配置的本地前端来源。
- 未经用户明确操作，不发起真实模型调用；测试默认使用 FakeProvider（T07 引入）。
