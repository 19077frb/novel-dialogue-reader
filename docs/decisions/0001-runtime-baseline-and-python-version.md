# 0001 运行基线与 Python 版本选择

日期：2026-09-28 · 状态：已采纳（T00）

## 问题

DEVELOPMENT.md 2.1 声明“Python 3.12 兼容基线”，但需要确定当前机器上实际可运行、可验证的版本
区间，并锁定后端/前端依赖解析方式，避免后续任务反复更换工具链。

## 选择

- 后端 `requires-python = ">=3.11"`，本机在 **CPython 3.11.4** 上完成 T00 验证；
  代码不使用 3.12 专有语法（`datetime.UTC` 自 3.11 起可用），保持对 3.12 的兼容。
- 后端：FastAPI + Pydantic v2 + pydantic-settings + uvicorn[standard]；测试用 pytest + httpx；
  静态检查用 Ruff。依赖由 `backend/uv.lock` 锁定。
- 前端：React 18 + TypeScript + Vite + Vitest + React Testing Library + TanStack Query；
  依赖由 `frontend/package-lock.json` 锁定，E2E 使用 Playwright（T04 起启用）。
- 端口：后端 `127.0.0.1:8765`，开发前端 `127.0.0.1:5173`，前端通过 Vite 代理转发 `/api`。
- 依赖缓存与虚拟环境放在仓库内（`.uv-cache/`、`.npm-cache/`、`backend/.venv/`，均已忽略提交），
  避免受限环境无法访问用户级缓存目录。

## 被放弃的方案

- **强制 3.12**：本机没有 3.12 解释器，无法生成可信锁文件与验证证据。
- **不建虚拟环境、直接用系统 Python**：会让后续任务依赖全局包状态，无法复现。
- **把 uv 缓存留在用户目录**：受限沙箱会拒绝打开 uv 写入的 `.git` 标记文件，导致 `uv run`
  无法启动；放进仓库后该问题消失。

## 验证

- `uv sync --project backend --all-groups` 生成 `backend/uv.lock`。
- `ruff check backend/src backend/tests backend/scripts`、`pytest backend/tests` 通过。
- `npm --prefix frontend run typecheck / test / build` 通过。

## 迁移与回滚

若安装 3.12，只需提高 `requires-python` 并重跑 `uv sync`，无需改代码。回滚方式是恢复
`backend/uv.lock` 与 `frontend/package-lock.json` 的上一版本并重新同步。
