# 评测清单（T16）

一份清单描述「用哪些作品、哪些书、哪些金标准」跑评测：

```json
{
  "manifest_version": "1.0",
  "description": "评测用途说明",
  "works": [
    {
      "work_id": "work-a",
      "title": "作品 A",
      "split": "dev",
      "license": "授权说明或来源",
      "notes": "备注",
      "books": [
        {
          "book_id": "book-a-1",
          "text": "evaluation/examples/a/text.txt",
          "gold": "evaluation/examples/a/gold.json",
          "format": "TXT",
          "hard_cases": ["no_explicit_attribution", "scene_boundary"],
          "notes": "备注"
        }
      ]
    }
  ],
  "notes": "整体说明"
}
```

规则：

- `split` 取值 `dev` / `calibration` / `test`，按**整部作品**划分；
  **同一部作品只能出现在一个 split**（调参/校准与最终测试必须用不同作品，否则数字不可信）。
- `text` 可以是 TXT 或 EPUB；`gold` 必须符合 `evaluation/schemas/gold-standard.schema.json`。
- 路径先按**清单文件所在目录**解析，找不到再按**仓库根目录**解析。
- `hard_cases` 目前认识：`no_explicit_attribution`、`scene_boundary`、`nested_quote`、`thought`、
  `group_voice`、`long_gap`、`repeated_quote`、`astral_text`（未知类别只给 warning）。

校验：

```powershell
uv run --project backend python -m ndr.evaluation validate --manifest evaluation/manifests/dev.json
```

当前仓库只有 `dev.json`（原创最小样例，样本量远不到宣布达标的要求）；
**真实作品请另建清单**，并把最终评测放在 `test` 划分里。