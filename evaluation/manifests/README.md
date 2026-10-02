# 评测清单格式

清单负责声明作品、数据划分、正文、金标准和授权，不负责选择模型参数；模型参数见 [配置说明](../configs/README.md)。

根字段为 `manifest_version`、`description`、`works` 和可选 `notes`。每个作品记录 `work_id`、`title`、`split`、`license`，并在 `books` 列出 `book_id`、`text`、`gold`、`format` 和可选难例/备注。可参考本目录 `dev.json`。

## 数据要求

- split 为 dev / calibration / test；按整部作品划分，同一作品不能跨集合。
- text 指向TXT或EPUB，gold符合 [金标准schema](../schemas/gold-standard.schema.json)。
- 路径先相对清单目录解析，找不到再相对仓库根目录解析。
- hard_cases 支持 no_explicit_attribution、scene_boundary、nested_quote、thought、group_voice、long_gap、repeated_quote、astral_text；未知类别给出警告。
- 公开清单和正文必须有合法来源，不得上传个人书库、凭据或未授权小说。

## 校验

```powershell
uv run --project backend python -m ndr.evaluation validate --manifest evaluation/manifests/dev.json
```

仓库 `dev.json` 仅包含原创最小样例，不足以宣布真实作品质量达标。新增评测使用独立清单，最终测试集冻结后不得用来调整提示词。
