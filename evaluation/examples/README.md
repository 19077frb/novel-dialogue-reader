# 原创评测样例

本目录提供原创最小测试输入，用于校验格式、引用、坐标和工具流程，不能替代真实作品质量评测。标注规范见 [金标准指南](../annotation-guide.md)。

## 文本样例

`minimal-txt-001/text.txt` 是原创短文，`gold.json` 是对应金标准。它包含明确归属、无法确定的短对白，以及需保留的叙述证据。

该样例使用 UTF-8 和 LF换行；其规范化文字与原文件一致，可以重算 `canonical_sha256` 和字符区间。实际其他输入应以规范化全文而不是任意文件字节定位。

```powershell
uv run --project backend python backend/scripts/gold_standard.py validate --gold evaluation/examples/minimal-txt-001/gold.json --text evaluation/examples/minimal-txt-001/text.txt
```

## 其他夹具

[exports/](exports/README.md) 保存离线生成的固定导出夹具。EPUB程序化夹具在 `backend/tests/fixtures/epub_factory.py` 中，包括注音、图片和跨节点内容，不是本目录中的真实小说样本。

新增真实作品前确认授权，按整部作品划分集合，避免把私有小说上传仓库。不得因整理说明而修改既有金标准或测试小说。
