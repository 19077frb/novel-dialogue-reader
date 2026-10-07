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

推理尝试的内部证据由`ndr.storage.run_archive`压缩保存，适配器通过`ProviderResult.receipt`/`ProviderError.receipt`私有属性传递实际请求和已接收响应，不添加模型JSON字段或公开错误正文。归档包含已发送小说文本和提供方响应，应与书库同等保护，禁止上传仓库。迁移0022仅新增可空、按需加载的列，不回填历史证据、不自动压缩整库；8MiB上限、不完整标记和使用边界见[契约](docs/CONTRACTS.md#模型配置任务和恢复)。当前归档覆盖调度器对白及章节人物分析，读取成功不等于结果通过校验，也不等于已实现自动重放恢复。

`ndr.jobs.expression_pipeline`为显式短表达任务提供正式多阶段复核。`ReviewedExpression`将完整短提案、任务绑定和接受上限一起传给领域编译，不能用普通模型JSON伪造该类型。缓存使用独立版本并恢复上限；检查点只存调用引用，原始输出读内部压缩归档。阶段恢复不增加用量、不推断未收到的响应；实际产品选择及模型质量另验收。离线回归可运行`uv run --project backend python -m pytest backend/tests/integration/test_expression_pipeline.py`，仅用原创文本和隔离库。

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

分组脚本可用 `--junitxml` 指定本地报告位置，用 `--basetemp` 指定隔离测试目录；pytest会清理指定的临时目录，务必使用专用空目录，不能指向书库、仓库或已有资料目录。

本地隔离测试副本可放在 `.tmp/<测试名>` 下，该目录不纳入 Git。测试结束后可清理已确认无用的副本和测试缓存；先确认没有测试仍在使用，保留需调查的失败现场，不删除实际书库、内部报告、依赖环境或正在使用的构建产物。

## 8. 配置与离线维护

用户页面操作见 [用户使用指南](docs/USER_GUIDE.md)。下面的命令面向源码部署和维护，不是日常阅读步骤。

所有命令默认在项目根目录执行，特别注明的除外。真实库操作前停止服务、备份并核对目标；离线测试使用独立数据目录，不调用真实模型。

### 环境配置

源码版可复制 `.env.example` 为 `.env`；免安装版不读取 `.env`。配置优先级、保存和重启契约见 [接口与安全约定](docs/CONTRACTS.md)，可配置项以 [`.env.example`](.env.example) 为准。

容量使用 `NDR_MAX_IMPORT_MB`、`NDR_MAX_EPUB_TOTAL_UNCOMPRESSED_MB`、`NDR_MAX_EPUB_ENTRY_MB`，支持小数，1 MB = 1024 × 1024 字节。旧 `*_BYTES` 仍兼容，同一来源 MB 优先。应用配置 JSON 使用小写 `*_mb`，仅在显式保存时转换旧容量键；内部 Settings 和 API 继续使用整数字节。

源码版配置文件位于 `<仓库>/data/application-settings.json`，免安装版位于 `%LOCALAPPDATA%\NovelDialogueReader\data\application-settings.json`。切换书库不改变配置文件位置，也不搬迁数据。模型密钥不能写入 `.env` 或 JSON。

### 数据库空间维护

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

免安装版没有维护按钮；维护其书库前，须准备与当前迁移版本匹配的 EXE，旧 EXE 可能无法打开升级后的书库。恢复备份时先停服，解压为独立文件并检查后再替换，不能在服务运行中覆盖数据库或手改迁移版本。

### 历史待确认记录检查

对旧版人物引用或队列同步问题，可在 `backend` 目录进行预检查：

```powershell
.venv\Scripts\python.exe -m ndr.scenes.repair_reviews --book-id 书籍ID
```

加 `--apply` 才会应用，并先备份数据库；有运行或排队任务时拒绝应用。不调用模型，仅修复符合证据约束的旧自动记录，保留人工锁定结果，不能替代正常人物推理或人工复核。备份回退必须停服执行。
