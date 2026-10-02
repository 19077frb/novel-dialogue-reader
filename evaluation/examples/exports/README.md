# 固定导出夹具与校验

本目录保存由 [原创短文](../minimal-txt-001/text.txt) 离线生成的历史夹具，不是当前版本的可执行发行包。

- `minimal-txt-001-annotated.epub`：带标注EPUB。
- `minimal-txt-001-annotated.html`：可离线打开的单文件HTML。
- `manifest.json`：生成来源、快照、大小、SHA256和当时校验状态。

这些产物使用确定性离线测试适配器，不能证明真实模型效果。历史 manifest 记录内部检查通过，EPUBCheck 为 NOT_RUN；这不代表当前环境没有工具，也不等于标准检查通过。

## 重新检查

在项目根目录执行，新报告写到运行数据目录，不改历史 manifest：

```powershell
uv run --project backend python backend/scripts/validate_exports.py --epub evaluation/examples/exports/minimal-txt-001-annotated.epub --html evaluation/examples/exports/minimal-txt-001-annotated.html --expected-text-file evaluation/examples/minimal-txt-001/text.txt --json data/validation/export-fixtures.json
```

需要正式EPUB标准检查时，提供 `--epubcheck-jar "本地jar路径"` 并确保Java可用。缺少工具时状态为 NOT_RUN，不能解释为PASS；HTML不适用EPUBCheck。

当前导出功能回归还应查看后端导出/回导测试，固定旧夹具不能覆盖所有新增行为。重新生成夹具须同时核对文字、哈希和manifest；不能仅为消除文档过时而覆盖旧证据。
