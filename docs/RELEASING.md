# Windows 免安装版与发布指南

本页负责下载、升级、迁移书库及维护者构建发布。应用页面操作见 [用户指南](USER_GUIDE.md)，源码开发与离线维护见 [开发说明](../DEVELOPMENT.md)。

## 下载与启动

从 [GitHub Releases](https://github.com/19077frb/novel-dialogue-reader/releases) 下载 `NovelDialogueReader-x.y.z-windows-x64.zip`，完整解压后双击 `NovelDialogueReader.exe`。无需 Python、uv、Node.js 或 npm。保留所有配套文件，尤其是 `_internal`。

支持 Windows 10/11 x64。目前未做代码签名，可能出现 SmartScreen 提醒；请确认来源并核对附件 `.sha256`。服务就绪后会打开浏览器，按启动窗口中的 `Ctrl+C` 停止。只关闭浏览器不会停止服务。

包内附带 [PORTABLE_README.txt](PORTABLE_README.txt)，可用Windows记事本直接打开。

识别人物和对白仍需要用户自己的模型配置与额度。

## 书库、升级和迁移

- 默认数据目录：`%LOCALAPPDATA%\NovelDialogueReader\data`，不在程序解压目录。
- 升级前停止旧服务并备份整个书库，再解压新版本启动。更新程序不会直接覆盖书库。
- 数据库升级前生成的 `backups/` 备份仅包含数据库，不包含原书与图片，不能替代整目录备份。
- 旧程序无法读取更高版本的数据库时会拒绝降级，不要手改数据库版本。
- 源码版的仓库 `data/` 不会自动迁移到免安装版。迁移时停止所有服务，将整份旧书库复制到空目标目录；不要混合覆盖两份书库。
- 系统凭据绑定当前Windows用户，会话密钥在服务退出后失效；复制书库不能保证密钥跟随迁移。
- 浏览器偏好和未派发队列与浏览器及服务地址有关，更换端口或浏览器后不保证恢复。

同一书库不要由多个版本同时使用。免安装版有实例保护，源码版仍需手动停止。刷新浏览器可接回已保存队列；服务重启中断的未知模型请求不会自动重发，按任务面板检查。

## 配置和启动参数

推荐使用“通用设置 → 应用配置”，保存后下次启动生效。无未结束任务且启动方式支持时，可“保存并重启”。免安装版不读取 `.env`。

```powershell
.\NovelDialogueReader.exe --port 8800
.\NovelDialogueReader.exe --data-dir "D:\MyBooks\data"
```

参数覆盖的项目在界面锁定，取消参数后重启才能修改。更换目录不搬迁数据；更换端口需打开新地址。启动失败的具体错误在启动窗口显示。

## GitHub Actions 发布

维护者使用 [release-portable.yml](../.github/workflows/release-portable.yml)，不需要 GitHub 插件或个人令牌。

1. 更新并提交版本元数据，推送要发布的源码。后端项目/运行版本/锁文件、前端项目/锁文件、OpenAPI 与 README 的版本须一致。
2. 打开 Actions → **Windows portable release** → **Run workflow**，选择分支，填写源码已有的 `x.y.z` 版本，不带 `v`。
3. 勾选发布则上传到 Releases；取消则只构建验收，从该次运行的 Artifacts 下载。
4. 发布成功后得到 `v版本号`、ZIP 和 SHA256 附件。

工作流构建触发时的提交，先做回归与接口一致性检查，再构建并实际启动 EXE 验收。验收使用独立书库、中文/空格路径及空 PATH，不调用真实模型。

仓库和组织策略须允许 Actions 发布任务申请 `contents: write`。同名 Release 拒绝覆盖，已有标签须指向构建提交；不强推或删除已有标签。发布先创建草稿，附件上传成功后公开；失败草稿须维护者检查处理，脚本不自动删除。

构建产物仅进入 Artifact / Release，不提交到 Git；原书、配置、密钥、日志及内部文档不得打包。

## 本地构建与图标

已安装开发环境的 Windows x64 维护者可在项目根目录运行：

```powershell
pwsh -File scripts/build-portable.ps1
pwsh -File scripts/test-portable.ps1 -ZipPath "构建输出的ZIP路径"
```

每次输出到独立的 `dist/portable-随机标识/`，不覆盖旧产物。验收覆盖图标、启动、导入、重启持久化、重复实例与端口冲突；打包包含第三方及 Python 许可。新依赖需检查许可和打包钩子。

EXE 图标来自 `assets/icons/app.ico`，包含七种尺寸；源图为 `assets/icons/app.png`。替换方形源 PNG 后可重新转换：

```powershell
uv run --no-project --with pillow==12.3.0 python backend/scripts/build_icon.py
```

构建验收检查嵌入图标；此流程不修改浏览器图标。Windows 可能缓存旧图标，可重新解压到新目录并重建快捷方式。
