轻小说对话辅助阅读器 — Windows x64 免安装版

请先完整解压 ZIP，再双击 NovelDialogueReader.exe。
不用安装 Python、Node.js、npm 或 uv。浏览器将在服务就绪后打开。
请保持启动窗口运行；按 Ctrl+C 停止。只关闭浏览器不会停止服务。

数据默认保存于 %LOCALAPPDATA%\NovelDialogueReader\data，更新程序不会覆盖书籍。
升级前请停止旧服务并备份整个数据目录。不要同时运行源码版和免安装版来操作同一书库。
源码版原有 data 不会自动搬迁：首次打开免安装版是空书架。
若要迁移，请停止所有服务，将旧 data 的全部内容复制到上述目录（目标应是空书库）。
数据库升级前会自动备份数据库，位于数据目录 backups；这不替代整份书库备份。
密钥仍由 Windows 凭据管理器或会话内存保存，不包含在本发行包里。

端口冲突时可在终端运行：NovelDialogueReader.exe --port 8800
指定其他书库：NovelDialogueReader.exe --data-dir "D:\MyBooks\data"
更换端口会改变浏览器的本地设置来源，部分阅读/处理偏好可能需要重新设置。
运行时遇到错误会在启动窗口显示；请保留错误内容供排查。
不要只移动 EXE：必须保留 _internal 等所有配套文件。

当前发行包未做商业代码签名，Windows 可能显示 SmartScreen 提醒。
请仅从项目 GitHub Releases 下载，并核对随包发布的 SHA256 校验文件。
人物识别仍需要配置模型 API，调用会产生提供方费用并发送相关正文。
退出程序不会保证自动续跑未完成的任务，应回到任务界面检查后再继续。

项目源码许可证见 LICENSE；第三方许可证见 THIRD_PARTY_LICENSES。
使用说明：https://github.com/19077frb/novel-dialogue-reader
