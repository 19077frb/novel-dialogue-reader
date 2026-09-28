# 导出样例

本目录是**原创**导出成品，用仓库内的最小样例 `../minimal-txt-001/text.txt` 生成：

- `minimal-txt-001-annotated.epub`：EPUB 3（`mimetype` 为第一项且不压缩，无外部引用）
- `minimal-txt-001-annotated.html`：单文件 HTML（CSS 内联、图片内联为 data URL，断网可打开）
- `manifest.json`：快照 ID/哈希、每个文件的大小与 sha256、内部检查结果与标准检查状态

生成方式（离线、确定性 FakeProvider，**没有访问任何真实模型**）：

```powershell
# 1) 导入 text.txt → 运行一次确定性离线任务 → POST /api/books/{id}/exports/preview 冻结快照
# 2) POST /api/books/{id}/exports（format=epub|html，style=color_and_label）→ 复制产物到本目录
# 3) 记录 manifest.json（含 snapshot_hash 与校验结果）
```

校验方式（内部检查永远运行；EPUBCheck 需要本地 jar）：

```powershell
uv run --project backend python backend/scripts/validate_exports.py `
  --epub evaluation/examples/exports/minimal-txt-001-annotated.epub `
  --html evaluation/examples/exports/minimal-txt-001-annotated.html `
  --expected-text-file evaluation/examples/minimal-txt-001/text.txt `
  --json data/test-exports/report.json
```

当前状态（如实记录）：

- 内部检查：**通过**（`mimetype_first`/`mimetype_stored`/`resource_closure`/`no_external_references`/
  `no_localhost`/`text_consistency` 等全部 ok）。
- EPUBCheck：**NOT_RUN** —— 本机没有 `tools/epubcheck/epubcheck.jar`，也没有联网安装；
  脚本会明确报 `NOT_RUN`，不会伪装成 PASS。安装 jar 后重新执行上面的命令即可升级为正式标准检查。