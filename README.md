# 轻小说对话辅助阅读器（Novel Dialogue Reader）

为中文轻小说译本的对白添加颜色/编号，帮助读者辨认说话人；原文不可变，识别结果单独保存，
不确定的对白交给用户确认。产品目标见 [PLAN.md](PLAN.md)，实现规格见 [DEVELOPMENT.md](DEVELOPMENT.md)。

> **当前状态（2026-09-28）**：已完成 **T00 工程骨架**、**T01 领域模型/数据库迁移/公共契约**、
> **T02 TXT 导入**、**T03 EPUB 导入与资源**、**T04 书架与无模型阅读器**、**T05 候选引语/Gap 与金标准工具**、**T06 模型配置与凭据**、**T07 适配器与连接测试**（识别预览、待确认与导出 **尚未实现**）。
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

## 界面（T04）

打开 http://127.0.0.1:5173 ：

- **书架**（`/library`）：拖放或选择本机的 TXT/EPUB 导入，可选编码与书名；导入失败会给出可用的编码候选与
  （标注为有损的）预演，并可一键换编码重试。书架卡片显示格式、正文长度与导入警告。
- **阅读**（`/books/:id/read`）：左侧目录（EPUB 为 spine 顺序）、正文按节点渲染，
  ruby 注音用 `<ruby>/<rt>` 显示、插图通过受控资源端点加载；切换章节或滚动会保存阅读位置，
  重新打开会回到书签所在章节。**不需要填写任何 API 配置**。

界面现在**不会**显示任何识别结果（颜色/编号/人物名）——那是 T05 起接入的标注层，
`AnnotationLayer` 目前只是带节点定位属性的占位容器。

## 上下文与预算（T08）

模型一次只看一个**处理窗口**（调用边界，不等于场景边界）：

- 正文预算默认 2500 token，另计提示预留 900 与输出预留 800（PLAN 7.3 的初始实验配置）；
  相邻窗口带约 200 token 重叠并携带上一窗口的接力点，长场景因此可以跨窗口延续。
- 证据保留顺序：目标对白 → 目标之间的叙述 Gap（必留） → 场景状态/已确认结果 → 两端重叠 → 外层 Gap；
  预算不足时先丢可选片段，**目标对白绝不截断**。
- 单条对白本身超预算时，它会独立成窗口并标为 `oversized_quote`（保留待定，不会截断后猜测）。
- 初读（`initial`）只用读到的位置以内的原文；重读（`reread`）才能用后文证据。
- token 用启发式估算（CJK 约 1 token/字），置信度标为低；接入真实分词器前不作为计费依据。

## 任务与用量（T10）

正式处理是一个**任务**（`POST /api/jobs`）：按窗口顺序执行，每个窗口完成即写检查点，可暂停/恢复；
再次运行会跳过已完成窗口。

- 先“预览”再“处理”同一范围会**命中缓存**，不会重复调用模型（发送次数可核对）。
- 预算按窗口预留、按提供方 usage 结算；到顶即停并标为 `BUDGET_EXHAUSTED`。
- 提供方没返回 usage 时记为**未知**（不按 0 计），`GET /api/books/{id}/usage` 单独列出。
- 请求发出后进程中断/超时：该次尝试记为未知结果，任务进入 `NEEDS_RECONCILIATION`，
  需要显式选择重试或保留未知——**不会自动重发**、不会盲目重复计费。
- `POST /api/books/{id}/estimates` 只做本地估算（窗口数/token），不发任何请求。

## 预览与按章处理（T11）

在阅读页顶部点“预览与处理”进入 `/books/:bookId/preview`：

- **范围**：按章选择或直接填码点范围；估算只走本接口，不发任何模型请求。
- **试运行**：用 `mode=preview` 创建任务，结果写进与正式阅读**相同**的标注投影（没有另一套临时识别存储）。
- **正式处理**：同范围再次处理会**命中缓存**，任务面板里的真实调用次数保持 0、缓存命中窗口数等于窗口总数。
- **原文 / 标注切换**与图例点击只是前端显示，**不会调用模型**；编号是真实文本节点 `〔S1〕`，不是 CSS 伪元素。
- 初读模式下，可见时点晚于当前范围的证据**不下发颜色与编号**（切到“重读”可显示全部有效投影）。
- 未知（UNKNOWN）不显示颜色也不显示编号：未知对白不等于新人。
- 阅读页（`/books/:bookId/read`）用同一套投影着色，可一键关闭标注；候选虚线覆盖与标注是两层不同的信息。

## 人工更正与待确认队列（T12）

不需要等模型自己改对：普通对白和待确认项都能人工修正，**这些操作不调用模型**（不产生费用）。

- 更正动作：指定已有说话人（`assign_existing`）、新建说话人（`create_speaker`）、改类型
  （`set_kind`）、锁定为未知（`mark_unknown`）；Gap 可确认 `CONTINUE/UPDATE/BREAK/UNCERTAIN`；
  同一场景内还能把两个分组 `MERGE` 或把一个分组 `SPLIT`。
- 人工确认的结果会**锁定**（`user_locked`）：之后的模型结果与自动合并都不会覆盖它。
- 锁定未知 ≠ 跳过：未知只是「这句还不知道是谁」，不会因此新建人物。
- 撤销走 `POST /api/corrections/{id}/undo`：目标仍停在这次更正的版本上才允许，
  否则返回 409（另一处已更新），**不会**回滚掉较新的修改；历史只追加，不做硬删除。
- 更正会影响同一推理窗口的其它对白：它们被标为 `stale` 并进入 `STALE_DEPENDENCY` 待确认项，
  等用户重新确认（界面在 T13 接入）。
- 队列接口：`GET /api/books/{id}/review-items`（按章节/场景/原因/状态过滤）、
  `GET /api/review-items/{id}`、`POST /api/quotes/{id}/review-items`（主动标记，幂等）、
  `POST /api/review-items/{id}/defer`（只延后）。

## 暂停、预算与故障恢复（T14）

任务面板（预览页与阅读页抽屉里的局部复核）现在把**非完成状态**翻译成可执行动作：

- **暂停**：`RUNNING` 时只请求暂停，当前窗口返回后进入「已暂停」；界面会说明「不承诺远程请求已停止计费」。
- **继续**：已完成窗口直接复用，只有未完成窗口会真正调用模型（面板显示真实调用次数与缓存命中窗口数）。
- **预算到顶**：到顶后不再发任何调用。预算属于创建任务时的快照，不会偷偷放宽——
  用更大的预算重新点「用当前预算重新处理此范围」即可（已完成窗口命中缓存）。
- **提供方超时 / 进程中断**：结果未知的尝试记为「未知结果」，任务进入「待人工对账」；
  系统**不会自动重发**，你可以在面板里选「保留未知结果」（免费）或「确认重发这些窗口」（可能计费）。
- **限流 / 暂时不可用**：只有「确定没有被处理」的错误会按有上限的指数退避自动重试（默认最多 2 次，
  上限 30 秒），每次尝试都留记录；超时不在此列。
- **缺 Key**：任务会明确失败并给出「去补充模型凭据」入口，而不是卡在「执行中」；原文始终可读。
- **进程重启**：启动时自动扫描——超租约的请求标为未知结果、暂停中的任务转为已暂停、
  中断的任务转为部分完成（已完成窗口保持有效），不会把 RUNNING 直接重发。

## 待确认队列与确认抽屉（T13）

阅读页顶部会显示待确认数量，点进去就是 `/books/:id/review`：

- **任意对白都能确认**：点阅读页里着色的对白或未处理的候选虚线，都会打开同一个确认抽屉
  （查看原文上下文、当前编号/状态、已有说话人、主动标记、跳过）。
- **四种更正**：指定已有说话人、新建说话人、改类型、锁定为未知；更正后阅读页颜色、图例与数量立刻更新。
- **跳过（延后）不是确认**：它只把项目移到 `DEFERRED`，在队列里把状态切到「已跳过」就能找回。
- **场景边界走 Gap 更正**：队列里的 Gap 项就地选择继续/推进/断开/待定，不会误用说话人确认。
- **队列清空 ≠ 全部识别正确**：队列只列出已知问题项；请结合统计里的未知/暂定/过期数量一起判断。
- **展开原文与模型复核是两件事**：「展开更多原文」只读本地原文；
  「局部复核」需要先选模型配置与 token 上限，会创建真实 `RECHECK` 任务并显示任务面板（可能产生费用）。
- 提交旧版本会得到 409：界面提示「已被其它操作更新」并刷新为最新状态，不会静默覆盖别人的修改。

## 场景与匿名分组（T09）

模型结果不会直接变成「谁在说话」，而是先过一遍保守的接受策略：

- 直接证据的归属才会被接受；指代/承接/风格类依据先标为**暂定**并进入待确认队列。
- 证据不足或多条短对白无法判断时标为**未知**——未知不会新建人物，也不会把几句未知并成同一个人。
- 编号 S1、S2… 只在所属场景内有意义：场景延续时沿用同一编号，只有明确的场景切换（BREAK）才重新编号。
- 后文才揭示的身份会记录可见时点，因此**初读时不会提前把两个声音合成一个颜色**。
- 用户确认过的对白（锁定）永远不会被后来的自动结果覆盖。

（颜色/编号自 T11 起在阅读页与预览页显示；待确认队列在 T13 接入。）

## 模型配置（T06）

打开 http://127.0.0.1:5173/settings/models ，填写名称、协议、Base URL（API 根路径）、模型名与密钥即可，
不需要改源码：

- 凭据模式：**仅本会话**（进程内存，退出即失效）或**系统凭据库**（keyring / Windows 凭据管理器）。
  系统凭据库不可用时会自动降级为会话密钥并在页面给出警告，**不会明文写入文件**。
- 保存后接口只返回 `has_key`；页面与 API 都不回显密钥。编辑时可选择“保持不变 / 替换密钥 / 清除密钥”。
- Base URL 必须是 API 根路径（例如 `https://api.example.com/v1`）；填成完整端点会被拒绝并提示。
- **连接测试**：保存配置后点“测试连接”（或先用“测试当前填写内容”测草稿）。它发送的是**微型结构化请求**，
  只检查鉴权与 JSON 输出可解析，并显示真实用量（提供方未返回 usage 时显示“未知”，不按 0 计）。
  **连接成功不代表小说标注效果**；效果评测需要真实作品与人工标注（T16）。
- 测试用适配器（`fake-provider`）只有设置 `NDR_ALLOW_FAKE_PROVIDER=1` 才可用，界面上会明确标注
  “没有访问任何真实服务”。真实提供方失败时不会回退到它。

E2E/自动化若不想触碰真实的系统凭据库，可设置 `NDR_CREDENTIAL_BACKEND=session`。

## 候选引语与金标准（T05）

导入后会自动扫描**候选引语**与它们之间的 **Gap**（不调用模型、不做说话人判断）：

```powershell
$bookId = (Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/books").data.items[0].id
Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/books/$bookId/quotes"            | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/books/$bookId/gaps"              | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/books/$bookId/locate?start_cp=0&end_cp=20"
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/api/books/$bookId/quotes/scan"
```

- 阅读页会把候选画成**虚线**（可一键关闭），并明确标注“尚未判定说话人”；颜色/编号自 T11 起由后端投影下发（见“预览与按章处理”）。
- 候选 ID 由（原文版本 + 位置 + 扫描器版本）稳定派生；重新扫描幂等，但**已有用户标注时返回 409**，
  不会覆盖人工结果。
- 异常引号不会吞章：单条候选超过长度/跨段/嵌套上限会被丢弃并在扫描警告里说明。

金标准（评测用）工具有三个子命令，全部离线、不需要模型：

```powershell
# 校验（结构 + 引用 + 范围 + 扫描器覆盖率）
uv run --project backend python backend/scripts/gold_standard.py validate `
  --gold evaluation/examples/minimal-txt-001/gold.json `
  --text evaluation/examples/minimal-txt-001/text.txt

# 生成可填写的标注模板（模板不是金标准）
uv run --project backend python backend/scripts/gold_standard.py template --text .\我的小说.txt --out .\gold-template.json

# 只看候选扫描结果（离线 sanity check）
uv run --project backend python backend/scripts/gold_standard.py scan --text .\我的小说.txt
```

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

## 导入与阅读（T02/T03）

无模型也能跑通导入与整本阅读（`GET /api/books/{id}/content` 返回结构化节点，前端在 T04 接入）：

```powershell
# 导入 TXT 或 EPUB（TXT 的 encoding 可省略：按 utf-8 → gb18030 → big5 严格检测）
$r = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/books/import -Form @{ file = Get-Item .\我的小说.txt }
$bookId = $r.data.book_id

Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/books/$bookId"                       | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/books/$bookId/chapters"              | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/books/$bookId/content?limit=200"     | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/jobs/$($r.data.job_id)"              | ConvertTo-Json -Depth 6
```

- 选错编码不会被静默接受：返回 422，`details.candidates` 列出可用编码，`details.preview` 是**有损**预演
  （`preview_is_lossy: true`），并带上失败的 `job_id`；正文里绝不会出现替换符 U+FFFD。
- 重复导入同一份文件复用同一本书与同一版本；换编码生成新版本并切换活动版本。
- 落盘位置（数据目录内，API 不暴露路径）：`books/<book_id>/source.*`（原始字节，不可变）与
  `books/<book_id>/versions/<version_id>/canonical.txt`（规范化全文，LF）。
- EPUB：按 spine 顺序阅读，标题取自 nav/NCX 目录；ruby 的注音（rt）不进入正文，
  存在节点 `payload.ruby` 里；插图是零长度 `image` 节点，图片通过
  `GET /api/books/{id}/resources/{resource_id}` 读取（只读包内已登记资源，不下载外链）。
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
| `NDR_ALLOW_FAKE_PROVIDER` | `false` | 允许测试用 `fake-provider` 适配器（绝不访问网络；界面上会明确标注） |
| `NDR_FAKE_PROVIDER_LABELS` | `unknown` | 仅测试：`unknown` 全部标为未知；`deterministic` 确定性建分组，用于离线验证着色链路 |

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

## E2E 样例文件

`frontend/e2e/fixtures/` 里的样例由脚本生成（原创内容，含 GB18030 文本与真实 1×1 PNG）：

```powershell
uv run --project backend python backend/scripts/make_e2e_fixtures.py
```

E2E 每次运行使用独立数据目录 `frontend/.e2e/data-<runId>`（`e2e/global-setup.ts` 尽力清理旧目录），
后端以 `NDR_AUTO_MIGRATE=1` 在测试端口启动，绝不接触 `data/` 里的用户书库。