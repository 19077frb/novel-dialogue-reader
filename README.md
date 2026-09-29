# 轻小说对话辅助阅读器

一个本地运行的轻小说阅读与对白标注工具。它使用大语言模型判断对白说话人，以不同颜色和人物标签呈现结果，并允许用户确认或修正不确定内容。

## 主要功能

- 导入 TXT 和 EPUB，保留章节、段落、注音与本地插图。
- 按章节建立人物表，并由用户确认本章主视角人物；批量处理时可自动确认人物并沿用前文人物。
- 识别对白说话人，处理叙述、心理描写和长对话场景。
- 在同一章节内合并同一人物的颜色；界面优先显示人物姓名或“男同学”等可读称呼，并可悬停查看人物描述。
- 单章处理前可预览模型窗口并多选需要处理的窗口，人物分析仍会读取整章。
- 支持按章节范围后台批量处理、实时章节进度、暂停恢复、有限复核和 token 上限。
- 提供待确认队列和人工更正，展示实际对白、中文原因和人物描述；用户确认的结果优先于模型输出。
- 分别展示本次任务与累计 token 用量；提供方缺少可靠价格资料时不显示金额。
- 导出带颜色或人物标签的 EPUB 和离线 HTML。
- 本工具导出的 EPUB 再次导入时，可恢复其中可见的对白人物标注和已处理章节状态，无需重新调用模型或重复批量处理。
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
4. 单章处理时，确认或修改章节人物表并选择本章主视角人物，然后从预览出的窗口中勾选要处理的窗口。
5. 需要连续处理时，选择开始和结束章节，查看 token 预估，按需填写 token 上限后启动批量处理。
6. 处理期间可以进入阅读页继续阅读；目录会用不同颜色显示未处理、处理中和已处理章节。
7. 在阅读页检查颜色和人物标签；通过待确认队列修正不确定结果。
8. 从阅读页或预览页导出 EPUB 或 HTML。

### 单章窗口处理

- 点击估算后，页面会提前列出本章的处理窗口，包括窗口序号、目标对白数、预估 token 和正文预览。
- 默认选中全部窗口，也可以只勾选其中多个窗口；没有选中任何窗口时不能开始处理。
- 窗口选择只限制对白归属推理的范围。人物分析始终读取整章，避免只看局部内容而漏掉人物身份。
- 预览与处理只保存一套最终标注，因此不再区分“初读”和“重读”。这两个模式仅在阅读页控制显示方式：初读尽量避免使用后文才能得知的身份信息，重读展示最终确认结果。

### 批量后台处理与 Token 上限

- 批量处理按章节顺序执行：每章先读取全文查找人物并自动确认人物表，再自动识别和确认对白归属。
- 后续章节会沿用本书前面已经确认的人物，同时仍会分析当前章节中新出现的人物。
- 批量任务运行时可以在应用内切换到阅读页。阅读目录中，未处理章节显示为灰色、处理中显示为橙色、处理完成显示为绿色；完成后会自动刷新对应章节标注。
- 启动前会先计算预计 token。第一次点击用于查看预估，确认后再次点击才会真正启动。
- Token 上限留空表示不限制。用量接近上限的 80% 时会提示：填写新的上限会直接继续当前批次，不会重新开始，也不会清零已用量；填写 `0` 表示提供方额度已刷新，并从零重新计算本批次额度。
- 如果提供方没有返回 token 用量，设置了上限的批量任务会安全停止，避免把未知用量当作零继续消耗。
- “后台”指在当前浏览器会话中切换页面后仍继续处理。批量任务完成前不要刷新、关闭页面或退出应用，否则批量调度不会自动续接。

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

开启思考时请给足输出预算：39 个目标的窗口实测需要 13k–20k 输出 token，`max_tokens=16000` 正好卡在边界上，
JSON 会写到一半被切断（`finish_reason=length`）。程序现在会**自动处理这种情况**：识别到截断后带原因重发一次，
并把 `max_tokens` 翻倍（上限 128000）；错误信息里也会写清是"被截断"还是"结构不合法"。
开启思考的建议配置：`max_tokens ≥ 32000`、`timeout_seconds ≥ 300`。

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
- 再次导入本工具导出的 EPUB 时，会恢复导出时可见的颜色/人物标注以及已完整处理的章节；批量处理会自动跳过这些章节。初读模式中被隐藏、未处理或未知的对白不会写入标注恢复清单。
- 当前以本地单用户使用为目标，没有账号系统或云端同步。

## 许可证

本项目采用 [MIT 许可证](LICENSE)（SPDX: `MIT`），版权归 19077frb 所有。

许可证覆盖本仓库的源代码与仓库内自带的原创样例（`evaluation/examples/**`、`frontend/e2e/fixtures/**`）。
你自行导入的小说不属于本仓库内容；第三方依赖仍遵循各自的许可证。
