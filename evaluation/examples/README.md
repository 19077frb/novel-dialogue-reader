# 原创样例

这些样例是**自行编写的最小片段**，用于校验金标准文件结构与坐标约定，不能替代真实作品评测。
真实作品必须另建数据，并按 [annotation-guide.md](../annotation-guide.md) 标注。

## `minimal-txt-001/`

| 文件 | 说明 |
| --- | --- |
| `text.txt` | 原始 TXT 片段（原创，7 段，含一个连续场景） |
| `gold.json` | 对应金标准，符合 `../schemas/gold-standard.schema.json` |

样例覆盖：显式归属（「她低声说」「少年忽然说」）、**不可确定**的短对白（`resolvable: false`）、
以及必须保留叙述证据的两个 Gap。

坐标约定：`gold.json` 的 canonical text 就是 `text.txt` 的 UTF-8 解码结果（LF 换行，未做其他
规范化），`canonical_sha256` 是 `text.txt` 的文件字节摘要；因此所有 `start_cp/end_cp` 可直接复算。

## 尚未建立

- EPUB 原创夹具（封面、ruby、图片、跨节点对白）已随 EPUB 导入一并提供。
- 范围检查工具与金标准校验命令见 `backend/scripts/gold_standard.py`。
