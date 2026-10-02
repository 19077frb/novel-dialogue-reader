# Windows 免安装版与发布

## 下载与使用

在仓库的 [Releases](https://github.com/19077frb/novel-dialogue-reader/releases) 下载 `NovelDialogueReader-x.y.z-windows-x64.zip`，**完整解压后**双击 `NovelDialogueReader.exe`。无需安装 Python、uv、Node.js 或 npm；启动窗口会自动打开浏览器，按 `Ctrl+C` 停止服务。不要只复制 EXE，配套 `_internal` 目录必须保留。

目前仅支持 Windows 10/11 x64，程序未做代码签名，可能出现 SmartScreen 提醒。请核对发布的 SHA256 文件并确认下载来源。模型识别仍需配置自己的模型 API。

## 在 GitHub 发布

1. 将要发布的源码提交并推送到仓库。版本号需在后端项目、运行版本、依赖锁、前端项目/锁、OpenAPI 和 README 中一致，现有版本一致性测试会检查。
2. 打开仓库 **Actions → Windows portable release → Run workflow**，选择发布分支，填写版本（本次为 `1.0.0`，不带 `v`）。
3. 保持 `Publish to Releases` 勾选即可发布；取消勾选则仅构建和验收，ZIP 从该次运行的 Artifacts 下载。
4. 成功后在 Releases 获得 `v版本号`、免安装 ZIP 和 `.sha256`。Actions 使用自带的 `GITHUB_TOKEN`，**不需要个人令牌或 GitHub 插件**。仓库需允许 Actions 运行，组织策略需允许发布任务申请 `contents: write`。

工作流固定构建点击运行时的提交，在 Windows 上安装锁定依赖，执行后端/前端回归、接口一致性检查，打包后再解压并实际启动 EXE。验收包含清空 PATH 后冷启动、中文/空格路径、迁移状态、静态页面/资源、原创短文导入、重启保留数据、重复启动与端口冲突，且不调用付费模型、不读取维护者的书库。

构建产物只作为 Actions Artifact 与 Release 附件上传，不进入 Git 提交。构建脚本显式只收集前端成品、Python 依赖、迁移文件和许可说明，不收集仓库 `data/`、密钥、日志或内部文档。

已有同名 Release 会拒绝覆盖；已有标签必须指向本次构建的提交，否则失败，请提升版本号。上传前先创建草稿，全部附件上传成功后才公开；如果上传失败会保留草稿以便检查，不会公开缺少附件的发行版。此时需要自行检查并清理本次草稿后重跑，脚本不会自动删除已有 Release 或标签。

## 用户数据与升级

免安装版数据默认位于 `%LOCALAPPDATA%\NovelDialogueReader\data`，与程序解压目录分开。更新 ZIP 不会覆盖书库，但升级前仍应停止旧服务并备份整个数据目录。数据库版本变化时会先生成 SQLite 在线备份到 `backups/`，这**不包含书籍资源文件**，不能代替整个目录备份。旧程序无法打开更高版本的数据库时会拒绝降级。

源码版继续使用仓库 `data/`，不会自动搬到免安装版。迁移现有书库时，停止两个版本的服务，将原 `data/` **全部内容**复制到免安装版空数据目录，不要与另一本书库混合覆盖。系统保存的模型密钥仍绑定 Windows 用户；仅会话保存的密钥重启后需重新输入。浏览器偏好与域名/端口有关，保持默认 `127.0.0.1:8765` 可继续沿用同来源的设置。

可选启动参数：

```powershell
.\NovelDialogueReader.exe --port 8800
.\NovelDialogueReader.exe --data-dir "D:\MyBooks\data"
```

只关闭浏览器不会停止服务。退出程序不会自动恢复所有调度任务，请在任务界面检查实际状态再继续。免安装版同一书库不允许启动多个实例；不同程序版本也不要同时操作同一数据目录。源码版不使用免安装版实例锁，必须手动停止原服务后再使用免安装版。

免安装版不读取 `.env`，可在“通用设置 → 应用配置”中修改并保存全部应用配置，各项有中文说明。保存后下次启动生效，也可在没有排队或运行任务时点击“保存并重启”。启动器会先优雅关闭服务、释放书库锁与端口，再读取新配置启动；不会强制结束推理。固定的本机监听、内置网页目录和数据库升级策略会说明锁定原因。配置文件固定在默认用户数据目录的 `application-settings.json`，更改书库目录不搬迁数据、不改变配置位置。使用 `--port` 或 `--data-dir` 时对应项目由参数覆盖；取消参数后才能在界面修改。重启失败请查看启动窗口并检查新端口或目录。

## 维护者本地验收（可选）

日常发版只需 Actions，无需本机构建。如需排查打包问题，已安装开发环境的 Windows x64 维护者可运行：

```powershell
pwsh -File scripts/build-portable.ps1
pwsh -File scripts/test-portable.ps1 -ZipPath "构建输出的ZIP路径"
```

输出位于被忽略的 `dist/portable-随机标识/`，每次独立构建，不覆盖旧输出。第三方许可与 Python 许可随包保存；有新依赖时需检查其许可和打包钩子。

## 应用图标

EXE 使用 `assets/icons/app.ico` 中的蓝色书籍/对话气泡图标，含 16、24、32、48、64、128、256 像素的透明图层。源图 `assets/icons/app.png` 由图像生成工具生成，经过用户选定后保存在仓库；它们是设计资产，不是 EXE/ZIP 构建成品。构建会嵌入该 ICO，验收会逐帧核对 EXE 图标资源；缺少图标时构建失败，不回退到默认图标。

替换源 PNG 后，可用独立工具环境重新转换（Pillow 仅用于转换，不是应用运行依赖）：

```powershell
uv run --no-project --with pillow==12.3.0 python backend/scripts/build_icon.py
```

转换保留透明度，要求方形源图至少 256 像素。浏览器标签页图标未在此流程中修改。Windows 可能缓存旧图标，更新后若仍显示旧图标，可重新解压至新目录并重新创建快捷方式。
