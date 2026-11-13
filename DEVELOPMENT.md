# 开发说明

本页面向贡献者和部署维护者，负责开发环境、架构边界、检查、配置及离线维护。安装入口见 [README](README.md)，页面操作见 [用户指南](docs/USER_GUIDE.md)，接口语义见 [契约](docs/CONTRACTS.md)。文档分工见 [职责索引](docs/README.md)。

Windows 免安装包由 GitHub Actions 手动构建和发布，流程与本地验收见 [发布指南](docs/RELEASING.md)。二进制仅发布为 Release 附件，不提交构建成品。

## 1. 开发原则

- 原文不可变；模型结果、人工更正和导出快照单独保存。
- 后续模型任务不能覆盖 `user_locked` 的对白归属；人工人物姓名/说明的后台更新由显式设置控制，不能混同两种保护。
- 无法判断归属时保留未知；有证据的匿名人物可以使用简短称呼，不能为了覆盖率虚构身份。
- 章节、场景和单次模型窗口是不同边界。
- 模型只返回结构化判断，不负责改写原文或直接操作数据库。
- 模型调用必须有明确预算，并记录实际或未知的 token 用量。
- 修改公共 API 时同步更新后端 schema、`docs/openapi.json`、前端生成类型和测试。
- 页面内导航、设置和任务查看入口保留链接语义，使用 `ndr-button` 复用统一按钮外观；本书导航、顶栏页签和跳至正文入口沿用各自的统一样式。新增入口需通过前端链接样式审计，不能以全局覆盖所有链接代替。

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
- 人物通过稳定ID关联，不能仅因名称相似或编号冲突强行合并；姓名更新、人工资料保护与初读可见历史按契约处理。
- 初读模式不能使用阅读位置之后的证据；重读模式可以使用后文。
- 导出读取冻结快照，不调用模型，也不覆盖原始书籍。

## 5. API 和模块边界

推理尝试的内部证据由`ndr.storage.run_archive`压缩保存，适配器通过`ProviderResult.receipt`/`ProviderError.receipt`私有属性传递实际请求和已接收响应，不添加模型JSON字段或公开错误正文。归档包含已发送小说文本和提供方响应，应与书库同等保护，禁止上传仓库。归档覆盖调度器对白、章节人物分析和自动人物合并；读取成功不等于结果通过校验，恢复仍须经过流程绑定校验。8MiB上限、不完整标记、完成后留存与恢复边界见[契约](docs/CONTRACTS.md#模型配置任务和恢复)。

迁移0023新增任务请求键登记表，回填原请求键，并保存活动任务复用时接受的其他请求键及各自摘要；旧任务内容不变。登记与任务创建处于同一事务，SQLite触发器防止原键夺用已登记的键。降级至0022会删除新登记的别名关联，原任务及原键保留；重新升级只能回填原键，不能找回降级丢失的别名。因此需要保留这些回执时应恢复完整备份，不应以降级再升级代替回滚。升级前备份、维护及数据目录说明见下文。

`ndr.jobs.expression_pipeline`为显式短表达任务提供正式多阶段复核。`ReviewedExpression`将完整短提案、任务绑定和接受上限一起传给领域编译，不能用普通模型JSON伪造该类型。缓存使用独立版本并恢复上限；恢复阶段的检查点保存调用引用，原始输出读取内部压缩归档；任务完成后按调用留存规则精简成功全文。阶段恢复不增加用量、不推断未收到的响应；实际产品选择及模型质量另验收。离线回归可运行`uv run --project backend python -m pytest backend/tests/integration/test_expression_pipeline.py`，仅用原创文本和隔离库。

模型输出默认仍为1.0。显式1.1领域契约允许心声和引用保留人物，必须由调用方在解析及应用时指定版本；结构生成使用`expression_output_json_schema()`，不得直接把评测模块的内部speech视图提交书库。版本和缓存边界见[契约](docs/CONTRACTS.md#模型配置任务和恢复)。正式Jobs API可显式选择`range.output_protocol=expression-production-1`，由短协议编译为1.1；页面提供完整对白策略试验选项，默认未切换。人物反馈试验选择、前置条件及任务展示见[用户指南](docs/USER_GUIDE.md)。相关回归可运行`uv run --project backend python -m pytest backend/tests/unit/test_expression_contract.py backend/tests/integration/test_attribution_engine.py`，仅使用原创文本和隔离数据库，不调用模型。

内部`run_window(expression_task=...)`可显式使用短表达协议：任务正文、目标、片段类型和边界必须与实际窗口一致，候选人物须来自当前状态；初读候选字段必须匹配范围内的身份事实。`compile_expression_output()`返回完整1.1输出与独立的接受上限，二者须一起应用；仅取`output`会丢失复核限制。调用方完整的`owner_approvals`表只能降低接受性，不能批准缺乏依据的归属。该路径保留每次原始响应及用量；用量未知时不自动重复格式失败请求，鉴权/限流/超时也不在此重发。内部入口不代表Job调度器、缓存、预算和刷新恢复已经接入。编译测试另见`backend/tests/unit/test_expression_compiler.py`。

`ndr.characters.facts`提供绑定不可变原文的逐事实持久化与按位置读取。`OriginalIdentitySnapshot`先核对整份原文哈希，可在一次事务内复用；更新由调用方负责锁定与事务，返回内容指纹供未来任务快照与缓存使用。旧资料没有事实时返回空，不回填首见位置。`prepare_merged_identity_facts()`及`prepare_profile_updates()`不修改行，目录接口在既有事务和版本检查内提交完整准备结果；原文揭示与身份合并的关联时点独立保存。`read_identity_facts()`仅返回原文事实，`read_identity_records()`包含资料修订，写入/合并/缓存必须保留全账本；模型输入使用可见profile，不能把被用户删除的原始事实直接变回候选别名。启用实际模型事实写入前仍须接通任务、初读投影及导出回导，不能仅添加存储列就宣称完成防剧透。离线回归：`uv run --project backend python -m pytest backend/tests/unit/test_character_facts.py backend/tests/unit/test_identity_profile_updates.py backend/tests/integration/test_character_facts_storage.py backend/tests/integration/test_character_directory.py backend/tests/integration/test_schema.py backend/tests/integration/test_index_policy.py`。

`ndr.llm.roster_repair`提供人物提案的定向修复计划和全名单编译，使用`prepare_roster_repair()`冻结有效人物及失败依赖组，`compile_roster_repair()`仅替换失败组并保留分别的调用来源。内部生成的人物任务可显式指定range.roster_repair_protocol=roster-repair-1，由ndr.jobs.roster_pipeline实现有界调用、计量、停止及恢复；须同时使用sourced-roster-2。页面默认尚未启用，模块编译器本身不调用模型。离线验证：`uv run --project backend python -m pytest backend/tests/unit/test_roster_repair.py backend/tests/unit/test_sourced_roster.py backend/tests/integration/test_sourced_roster_jobs.py backend/tests/integration/test_roster_pipeline.py`。

`ndr.llm.isolated_roster_repair`是新任务冻结的identity-blocks-2编译策略；旧严格编译器保留。它在完整组覆盖及全名单校验下隔离辅助信息、保留独立成功组，再生成仅含剩余组的计划。保留块含原索引及实际来源，不能把多次修复来源统一重标；策略/计划绑定进入阶段指纹。同一幂等请求复用旧任务而非升级。离线边界用例为`backend/tests/unit/test_isolated_roster_repair.py`，正式暂停/恢复与来源用例仍在`test_roster_pipeline.py`；契约细节见[人物身份与颜色](docs/CONTRACTS.md#人物身份与颜色)。

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

完整机器可读接口见 [docs/openapi.json](docs/openapi.json)。手工说明不复制字段全集，接口修改必须同步生成定义和前端类型。

## 6. 模型、安全与成本

- 正式适配器支持 OpenAI Chat Completions 兼容协议及 Anthropic Messages 协议；均通过共享工厂供人物、对白、复核及合并任务使用。
- Base URL 是 API 根地址。OpenAI 适配器追加 `/chat/completions`；Anthropic 根地址以 `/v1` 结尾时追加 `/messages`，否则追加 `/v1/messages`。兼容网关填写其带版本的根地址，不填完整业务端点。
- Anthropic 使用 `x-api-key` 与 `anthropic-version: 2023-06-01`，将 system 消息转换为顶层字段，仅读取最终文本块，使用非流式纯文本 JSON 任务。不强制 OpenAI 的 `response_format`，仍按同一业务 schema 校验。接入测试使用隔离 MockTransport，不代表真实服务质量已验证；请求与思考参数依据 [Messages 文档](https://platform.claude.com/docs/en/api/messages/create)。
- API Key 只存入系统凭据库或会话内存，不进入数据库、日志、缓存或响应。
- 自动测试默认不能访问真实付费模型。
- 重试必须有次数上限；请求结果未知时不能静默自动重发。
- 日志和错误响应必须对密钥、授权头和提供方响应做脱敏。
- 运行期诊断写入数据目录的 `logs/tasks.log`，采用有界结构化事件，记录安全错误分类及任务关联，不记录正文、提示词、模型返回、SQL/参数或凭据。查阅、容量和观察边界见[日志排查与容量](#日志排查与容量)。
- `writer_released` 分别记录获取写锁等待 `wait_ms`、取得写锁至首次提交/退出的 `initial_hold_ms`、写上下文 `write_context_ms`、其中业务处理 `work_ms` 和提交 `commit_ms`。总计时包含等锁；上下文中显式提交可能提前释放锁，因此上下文时长不是精确锁占用，首次持锁时长也不包含后续隐式事务。复核写入在读取前获取写锁，模型调用始终在事务外。
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

仓库修改先创建或复用中文Issue，规范描述目标与验收条件；在独立分支提出关联PR，最新GitHub检查及前端生产构建成功后再合并主分支，不直接推送main。完整协作与提交约束见 [AGENTS.md](AGENTS.md)。这是贡献流程，不代表远端已启用服务器端分支保护。

### 测试隔离与CI耗时

普通数据库测试从每个pytest进程中真实迁移得到的空库模板生成独立副本，不共享可写数据库或应用对象。已有测试库不会被空模板覆盖；空库初始化、历史升级、迁移回退及离线维护测试仍执行真实迁移。模板仅在临时目录中存在，不存入仓库、书库或Actions缓存。

数据库回归Actions保留全部后端测试，分为 `unit`、`database`、`integration-1`、`integration-2` 四组，最多四组并行。分组脚本和测试保证文件不遗漏、不重复；其中一组失败不会取消其他组，原 `database` 汇总检查必须在静态检查和全部测试组通过后才能通过。同一分支或PR的新提交会取消该回归工作流的旧运行，不取消独立的发布工作流；行为遵循 [GitHub并发规则](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)。实际并行数仍受账号可用runner限制。

所有PR（包括仅修改文档的PR）都会执行该验证，不按修改路径跳过；主分支推送前端、后端或验证依赖也会触发检查。任务分支不另开push检查，避免同一修改因push和PR重复执行。前端在独立并行任务中执行类型、API一致性、单元测试及生产构建，`database` 总检查也必须等待前端成功。PR新增提交后需重新等待最新结果，不能使用旧提交或本地结果替代。这里的生产构建生成前端资源，不自动打包EXE或发布Release；免安装发布仍按 [发布指南](docs/RELEASING.md) 显式执行。

本地默认 `verify.ps1` 仍执行全量检查，并输出最慢的20项测试阶段（包括准备、执行和清理）。从项目根目录复现某组：

```powershell
uv run --project backend python backend/scripts/run_test_group.py --group database
uv run --project backend python backend/scripts/run_test_group.py --group integration-1 --collect-only
```

每个CI组也输出耗时，并提供保留7天的JUnit附件，便于定位慢测试和失败，不代表真实模型质量。选择分组不依据本次修改文件；发布工作流继续执行全量验收，不绕过迁移或索引检查。

pytest 默认在系统临时目录下统一管理编号会话。成功用例的 `tmp_path` 在用例结束时清理，整轮成功时也清理会话目录及空库模板；失败后的编号会话目录最多保留三轮，便于排查。仍在运行的会话有锁保护，崩溃、文件占用或权限问题可能延迟清理。

分组脚本可用 `--junitxml` 指定本地报告位置。通常不要指定 `--basetemp`，也不要每轮创建新的 `PYTEST_DEBUG_TEMPROOT`，否则会绕过统一的编号留存范围。受限环境需要项目内临时目录时，可在一个会话中设置固定的绝对 `PYTEST_DEBUG_TEMPROOT`，先创建该专用目录，再连续运行测试。`--basetemp` 仅供显式隔离诊断使用，pytest会在启动时清空指定目录，且不按上述三轮策略清理其会话目录；务必使用专用测试目录，不能指向书库、仓库或已有资料目录。

项目内受管理的测试根可固定为 `.tmp/pytest-managed`，该目录不纳入 Git。显式诊断副本由创建者在结束后核对归属并清理，不放在实际书库的 `data/validation` 或内部报告目录长期保留。先确认没有测试仍在使用，保留需调查的失败现场和不可再生的评测证据，不删除实际书库、内部报告、依赖环境或正在使用的构建产物。

### 依赖下载缓存

uv 和 npm 使用各自默认的用户级下载缓存，同一用户从仓库根目录或包目录调用会复用对应缓存。已有的 `backend/.venv`、`frontend/node_modules` 和历史下载缓存继续保留，配置调整不会迁移或删除它们。缓存与安装环境都占用磁盘；缓存用于复用下载内容，安装环境供开发、测试和构建使用。

需要复用历史缓存，或受限环境只能写项目目录时，可在当前 PowerShell 会话显式设置绝对路径，供后续命令及其子进程使用。例如在仓库根目录运行下列命令，选择已确认的缓存目录。

```powershell
$env:UV_CACHE_DIR = [IO.Path]::GetFullPath('.uv-cache')
$env:npm_config_cache = [IO.Path]::GetFullPath('.npm-cache')
uv cache dir
npm config get cache
```

这些设置在当前会话持续有效，关闭该 PowerShell 后结束；需要恢复默认时移除对应环境变量。首次改用缺少依赖包的缓存可能需要下载，离线前应确认所需依赖已经安装或下载内容可用。日常启动和验证保留缓存，显式维护时可对选定目录运行 `uv cache prune` 或 `npm cache verify`，清理工具判定无用的内容并检查缓存。`uv cache prune --ci` 会移除预构建 wheel，后续安装可能重新下载，应按 CI 的缓存策略选择。

## 8. 配置与离线维护

用户页面操作见 [用户使用指南](docs/USER_GUIDE.md)。下面的命令面向源码部署和维护，不是日常阅读步骤。

所有命令默认在项目根目录执行，特别注明的除外。真实库操作前停止服务、备份并核对目标；离线测试使用独立数据目录，不调用真实模型。

### 环境配置

源码版可复制 `.env.example` 为 `.env`；免安装版不读取 `.env`。配置优先级、保存和重启契约见 [接口与安全约定](docs/CONTRACTS.md)，可配置项以 [`.env.example`](.env.example) 为准。

容量使用 `NDR_MAX_IMPORT_MB`、`NDR_MAX_EPUB_TOTAL_UNCOMPRESSED_MB`、`NDR_MAX_EPUB_ENTRY_MB`，支持小数，1 MB = 1024 × 1024 字节。旧 `*_BYTES` 仍兼容，同一来源 MB 优先。应用配置 JSON 使用小写 `*_mb`，仅在显式保存时转换旧容量键；内部 Settings 和 API 继续使用整数字节。

源码版配置文件位于 `<仓库>/data/application-settings.json`，免安装版位于 `%LOCALAPPDATA%\NovelDialogueReader\data\application-settings.json`。切换书库不改变配置文件位置，也不搬迁数据。模型密钥不能写入 `.env` 或 JSON。

### 日志排查与容量

日志位于当前书库的 `logs/tasks.log`；轮转文件为 `tasks.log.1` 至 `.3`。每份最多 5 MiB，总量约 20 MiB，不按任务另建文件。新记录每行一个 JSON 对象，含 UTC `time`、`level`、`event` 和 `schema_version`；单条最多 8 KiB，超限保留关联标识与错误分类并标记截断。已有普通文本日志不重写，轮转期间可能与新格式共存。

排查时先从页面错误编号取得 `request_id`，或从任务详情、批次本地记录取得 `job_id`，在当前与轮转日志中一起检索。在源码版默认书库下可运行：

```powershell
Select-String -Path .\data\logs\tasks.log* -SimpleMatch '待查的任务ID或错误编号'
```

使用自定义书库或免安装版时，替换为实际书库路径。按以下顺序关联事件：

1. `job_admitted` 记录准入后的任务 ID、状态、是否新建和是否派发；`job_control` 记录暂停、恢复或核对操作。重复请求复用任务，不代表又调用了一次模型。
2. `job_worker_start` / `job_worker_finished` 记录本次执行起止及结果；调用事件带 `run_id`、`window_id`、`stage`、尝试次数和耗时。`*_call_restored` 表示复用已有回执，不能算成新派发。
3. 遇到失败，结合 `reason_code` 和 `error.chain` 查看提供方错误码、HTTP 状态、超时类别、SQLite 错误码、系统错误码、JSON 行列或结构校验问题类型。因果链最多四层，每层保留最接近错误位置的八个源码帧，只含文件名、函数名和行号。日志不保留任意异常原文或完整上游响应。
4. `settlement_deferred` 表示模型执行后仍等待本地保存；查看相邻写入等待事件及 `settlement_recovered`，不要因此重新提交模型任务。重复后台故障先记录完整首条，随后最多每 60 秒汇总次数、时间和错误指纹，不重复整份栈；恢复或正常关闭也会输出汇总。去重缓存最多 256 项，不合并不同任务、调用或错误类别。

正常进度、健康检查和任务状态轮询不逐次写文件日志；独立调用、实际重试和终态保留事件。`returned` 只代表调用返回，仍须检查错误、`usage_unknown` 和最终任务状态。日志中的调用计数用于本次执行诊断，未知用量不等于零费用；实际用量与恢复依据仍以任务记录和调用回执为准。

启动迁移、恢复、后台收尾及 API、人物、导入、导出失败使用同一安全诊断入口。日志按书库隔离，重复启动不重复安装同一个文件处理器。创建、写入或轮转失败会输出固定诊断不可用提示，不覆盖原始业务错误、不更改重试和任务结果；此时不能保证日志完整。标准源码和免安装启动入口关闭访问轮询输出，并安全格式化服务器异常；自行使用外部 ASGI 宿主时，其控制台日志由宿主管理。不要为排错另加无限增长的控制台重定向文件。

前端尚未提交的排队等待只存在于浏览器的批次记录，后端日志无法观察；提交后可用其中的任务 ID 关联。旧版本漏记的错误、强制结束进程前尚未落盘的事件和已轮转淘汰的记录不能补录。需要保留某次故障时，及时复制相关轮转文件到明确的诊断归档，完成排查后按需清理；日志不替代数据库备份或模型响应归档。

### 数据库空间维护

在线写入通过 `create_session_factory` 的排队会话执行；新增写入口复用该工厂及显式写事务，不以直接引擎连接绕过队列。只读查询保持普通会话；网络调用必须在写事务及队列资格释放后执行。队列按数据库文件、当前进程协调，不提供多进程调度或持久任务存储，具体边界见 [接口与安全约定](docs/CONTRACTS.md)。离线维护仍先停止服务、独占数据库，不依赖在线队列。

删除或替换数据库查询时，也须重新审查其索引用途：去掉不再使用的索引列，但保留实际查询和外键维护所需的覆盖。索引变更通过新的可逆迁移同步旧库与模型，不能仅移除测试断言或改写已发布迁移。`backend/tests/integration/test_index_policy.py` 检查模型与迁移的索引一致性、用途、查询计划及冗余；外键覆盖由结构测试检查。

普通启动不自动压缩数据库；删除数据或精简索引后，空闲页可复用，但文件未必立即缩小。检查空间：

```powershell
uv run --project backend python -m ndr.storage.maintenance
```

执行压缩前，停止任务、等待收尾并停止服务，预留数据库大小三倍的空闲空间：

```powershell
uv run --project backend python -m ndr.storage.maintenance --apply
```

可用 `--data-dir "实际书库目录"` 指定其他书库。维护会先生成校验过的 `backups/before-compact-*.sqlite3.gz` 备份，再升级数据库并压缩。升级事务提交前核对原有全部业务表与字段，缺失或内容变化则拒绝并回滚；压缩前后另核对升级后的完整业务数据，包括新增字段，避免将合法新增列误报为数据变化。数据库被占用、存在未结束任务或空间不足时拒绝执行。不会清理旧备份、回收文件或模型缓存，完成前不要启动服务。

已完成任务的留存整理是独立操作，先预览：

```powershell
uv run --project backend python -m ndr.storage.retention
```

确认使用当前数据库版本、停止任务并停服后，执行：

```powershell
uv run --project backend python -m ndr.storage.retention --apply
```

同样支持 `--data-dir`。命令要求书库锁、SQLite 独占访问和足够备份空间，先创建并校验 `backups/before-retention-*.sqlite3.gz`，再在单个事务中精简已完成执行材料、共享旧人物展示历史并移除无引用共享基线。正文、标注、人物事实、撤销记录、书签、阅读位置、任务状态和用量均核对不变，异常回滚。未知归属缓存保留，未完成任务的恢复材料保留；具体留存条件见[契约](docs/CONTRACTS.md#模型配置任务和恢复)。此命令不自动升级或压缩数据库，报告中的逻辑字节变化不等于文件立即缩小，腾出的页可复用。需要缩小文件时另运行上面的离线压缩命令。

排查需要成功调用全文时，在复现前将应用配置“保留已完成调用详情”打开并保存、重启，或设置 `NDR_RETAIN_COMPLETED_CALL_DETAILS=1`。默认关闭；离线整理也遵守该设置。已经精简的调用全文只能从整理前备份找回，开启选项不会重建历史响应。

免安装版没有维护按钮；维护其书库前，须准备与当前迁移版本匹配的 EXE，旧 EXE 可能无法打开升级后的书库。恢复备份时先停服，解压为独立文件并检查后再替换，不能在服务运行中覆盖数据库或手改迁移版本。

### 历史待确认记录检查

对旧版人物引用或队列同步问题，可在 `backend` 目录进行预检查：

```powershell
.venv\Scripts\python.exe -m ndr.scenes.repair_reviews --book-id 书籍ID
```

加 `--apply` 才会应用，并先备份数据库；有运行或排队任务时拒绝应用。不调用模型，仅修复符合证据约束的旧自动记录，保留人工锁定结果，不能替代正常人物推理或人工复核。备份回退必须停服执行。
