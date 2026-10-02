轻小说对话辅助阅读器 — Windows x64 免安装版

【开始使用】
完整解压 ZIP，再双击 NovelDialogueReader.exe。
无需安装 Python、Node.js、npm 或 uv；请保留 _internal 等配套文件。
浏览器在服务就绪后打开。保持启动窗口运行，按 Ctrl+C 停止。
只关闭浏览器不会停止服务。

在书架导入 TXT / EPUB 即可阅读；模型识别需要先添加自己的模型配置。
调用模型会把所需正文或人物资料发送给服务商，可能产生费用。

【设置】
通用设置中的修改需要点击保存。
阅读与处理设置保存即生效；应用配置保存后下次启动生效。
没有未结束任务时可使用“保存并重启”。免安装版不读取 .env。
导入和 EPUB 容量限制使用 MB，可填写小数。

【数据与升级】
默认书库：%LOCALAPPDATA%\NovelDialogueReader\data
更新程序不会直接覆盖书库；升级前停止服务并备份整个数据目录。
自动数据库备份在 backups 中，不包含原书和图片，不能代替整份书库备份。
源码版 data 不会自动搬迁。迁移须停止所有服务，复制整份书库到空目标目录。
不要同时运行不同版本操作同一书库。

刷新或重新打开同一浏览器、同一地址可以找回保存的任务。
关闭所有页面时，尚未派发的任务暂不继续。
重启服务后若请求结果未知，请按任务界面提示检查，不要重复提交。

【可选启动参数】
NovelDialogueReader.exe --port 8800
NovelDialogueReader.exe --data-dir "D:\MyBooks\data"
参数覆盖的配置在界面锁定；更换目录不搬迁书籍，更换端口可能影响浏览器偏好。
错误会显示在启动窗口，请保留具体错误供排查。

【安全与许可】
当前程序未做代码签名，Windows 可能显示 SmartScreen 提醒。
只从项目可信来源获取发行包，并核对发布的 SHA256。
源码许可见 LICENSE；第三方许可见 THIRD_PARTY_LICENSES。
页面操作指南：
https://github.com/19077frb/novel-dialogue-reader/blob/main/docs/USER_GUIDE.md
