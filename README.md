# 轻小说对话辅助阅读器

一个本地运行的轻小说阅读与对白标注工具。它使用大语言模型判断对白说话人，以不同颜色和人物标签呈现结果，并允许用户确认或修正不确定内容。

## 主要功能

- 导入 TXT 和 EPUB，保留章节、段落、注音与本地插图。
- 按章节建立人物表，并由用户确认本章主视角人物。
- 识别对白说话人，处理叙述、心理描写和长对话场景。
- 在同一章节内合并同一人物的颜色；能够确认身份时显示人物姓名。
- 支持预览、按章处理、暂停恢复、有限复核和 token 预算。
- 提供待确认队列和人工更正，用户确认的结果优先于模型输出。
- 导出带颜色或人物标签的 EPUB 和离线 HTML。
- 原文、数据库和导出文件默认只保存在本机。

## 技术栈

- 后端：Python 3.11、FastAPI、SQLAlchemy、Alembic、SQLite
- 前端：React、TypeScript、Vite
- 模型接口：兼容 OpenAI Chat Completions 的 API，例如 DeepSeek

## 环境要求

- Windows 10/11
- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 和 npm 10

## 安装

克隆仓库后，在项目根目录运行：

```powershell
uv sync --project backend --all-groups
Push-Location frontend
npm ci
Pop-Location
```

如果仓库中没有 `package-lock.json`，将 `npm ci` 改为 `npm install`。

## 启动

### 最简单的方式

在 Windows 资源管理器中双击 `start.bat`。脚本会自动执行数据库迁移、构建前端并打开：

<http://127.0.0.1:8765>

停止服务时双击 `stop.bat`，或在启动窗口中按 `Ctrl+C`。

也可以在 PowerShell 中运行：

```powershell
.\start.bat
```

更换端口：

```powershell
.\start.bat 8800
```

### 单端口启动

```powershell
pwsh -File scripts/serve.ps1
```

常用参数：

```powershell
pwsh -File scripts/serve.ps1 -Port 8800
pwsh -File scripts/serve.ps1 -DataDir D:\novel-dialogue-data
pwsh -File scripts/serve.ps1 -SkipBuild
pwsh -File scripts/serve.ps1 -Stop
```

### 开发模式

开发模式会分别启动后端和 Vite 前端：

```powershell
pwsh -File scripts/dev.ps1
```

- 前端：<http://127.0.0.1:5173>
- 后端：<http://127.0.0.1:8765>
- 健康检查：<http://127.0.0.1:8765/api/health>

停止开发服务：

```powershell
pwsh -File scripts/dev.ps1 -Stop
```

## 首次使用

1. 在书架页面导入 TXT 或 EPUB。
2. 打开“模型配置”，填写 API 根地址、模型名称和 API Key，并测试连接。
3. 进入书籍的预览与处理页面，选择章节。
4. 确认或修改章节人物表，并选择本章主视角人物。
5. 设置处理预算和阅读模式，然后开始识别。
6. 在阅读页检查颜色和人物标签；通过待确认队列修正不确定结果。
7. 从阅读页或预览页导出 EPUB 或 HTML。

### DeepSeek 配置示例

- 协议：`chat-completions-compatible`
- Base URL：`https://api.deepseek.com`
- 模型：填写账号当前可用的模型名称
- API Key：在界面中填写

### 思考模式（可选）

模型配置页的「生成参数」旁边有 **关闭思考 / 自适应思考 / 开启思考** 三个按钮，会写入 `thinking.type`
（提供方只接受 `enabled` / `adaptive` / `disabled`；`high`、`medium` 这类是"强度"而不是类型，写了会得到 422，
本程序现在会在保存时就拦下并提示）。开启思考时按钮会同时把输出上限提到 `max_tokens=32000`、超时提到 300 秒。

```json
{
  "thinking": { "type": "disabled" },
  "max_tokens": 16000,
  "timeout_seconds": 300
}
```

实测（同一真实窗口，39 条候选，`deepseek-v4-flash`）：

| 思考模式 | 耗时 | 输出 token | 判定为新人物 | 说明 |
| --- | --- | --- | --- | --- |
| `disabled` | 6.9 秒 | 2,469 | 3 | 复用已有分组，颜色更容易在章节内合并 |
| `adaptive` | 55.5 秒 | 14,480 | 32 | 推理 token 计入 `max_tokens`，明显更慢更贵，且更容易把每句判成新人 |
| `enabled` | 51.4 秒 | 13,322 | 31 | 同上 |

因此**默认建议关闭思考**（更快、更省、章节内人物合并更稳定）；确实有难窗口需要复核时，再用「自适应思考」
或「开启思考」跑一次局部复核。

Base URL 应填写 API 根地址，不要包含 `/chat/completions`。模型能力和兼容格式可能变化，请以提供方当前文档为准。

## 数据与隐私

运行数据默认保存在仓库根目录的 `data/`，包括：

- 导入的原始书籍
- SQLite 数据库
- 解析后的正文和资源
- 导出的 EPUB/HTML
- 运行日志

`data/`、`.env`、数据库、日志、模型密钥和构建产物已在 `.gitignore` 中排除。模型密钥通过系统凭据库或当前进程内存保存，不应写入源码或提交到 Git。

升级或切换版本前，建议先备份整个 `data/` 目录。

## 环境配置

所有配置都有默认值。需要自定义时：

```powershell
Copy-Item .env.example .env
```

常用变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `NDR_HOST` | `127.0.0.1` | 后端监听地址 |
| `NDR_PORT` | `8765` | 后端端口 |
| `NDR_DATA_DIR` | `<仓库>/data` | 本地数据目录 |
| `NDR_CREDENTIAL_BACKEND` | `system` | `system` 使用系统凭据库，`session` 只在当前进程保存密钥 |
| `NDR_LLM_TIMEOUT_SECONDS` | `30` | 模型请求默认超时 |
| `NDR_AUTO_MIGRATE` | `0` | 是否在应用启动时自动迁移数据库 |

完整示例见 [`.env.example`](.env.example)。

## 测试

运行后端静态检查、后端测试、OpenAPI 一致性检查、前端类型检查、前端测试和生产构建：

```powershell
pwsh -File scripts/verify.ps1
```

单独运行：

```powershell
uv run --project backend ruff check backend/src backend/tests backend/scripts
uv run --project backend pytest backend/tests
npm --prefix frontend run typecheck
npm --prefix frontend run test -- --run
npm --prefix frontend run build
```

## 项目结构

```text
backend/              FastAPI 后端、数据库迁移和测试
frontend/             React 前端、组件测试和端到端测试
scripts/              启动、停止和验证脚本
docs/openapi.json     生成的 API 定义
evaluation/           评测工具、样例和标注说明
data/                 本地运行数据，不提交到 Git
```

开发约定和关键架构边界见 [DEVELOPMENT.md](DEVELOPMENT.md)。自动化修改规则见 [AGENTS.md](AGENTS.md)。

## 已知限制

- 对白归属取决于文本线索和所选模型，结果仍需要人工复核。
- 无引号对白、复杂嵌套引用和极长章节可能需要手动修正。
- EPUB 的复杂 CSS 不保证完全还原，但正文、章节和本地资源会被保留。
- 当前以本地单用户使用为目标，没有账号系统或云端同步。

## 许可证

本项目采用 [MIT 许可证](LICENSE)（SPDX: `MIT`），版权归 19077frb 所有。

许可证覆盖本仓库的源代码与仓库内自带的原创样例（`evaluation/examples/**`、`frontend/e2e/fixtures/**`）。
你自行导入的小说不属于本仓库内容；第三方依赖仍遵循各自的许可证。
