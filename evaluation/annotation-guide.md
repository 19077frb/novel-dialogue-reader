# 标注指南（金标准）

适用范围：为“轻小说对话辅助阅读器”制作人工金标准，用于评价对白提取、场景边界与场景内匿名分组。
本文件不描述产品功能是否已实现；实现状态见 [docs/IMPLEMENTATION_STATUS.md](../docs/IMPLEMENTATION_STATUS.md)。

## 1. 基本约定

- 坐标一律是**该书籍版本规范化全文中的 Unicode 码点区间 `[start_cp, end_cp)`**（见决策 0002）。
  规范化规则：换行统一为 `\n`，保留原始段落划分，不改写字词与标点。
- 标注对象是“候选引语”的位置，不要求先知道真实姓名。
- 分组编号是**场景内**匿名编号（S1、S2…），跨场景不继承；比较时在场景内做最优匹配，
  编号整体互换不算错。
- 每条不确定的判定必须显式写 `resolvable: false`，而不是随便归入某个分组。

## 2. 必须记录的字段

结构由 [schemas/gold-standard.schema.json](schemas/gold-standard.schema.json) 校验；
T05/T16 的工具还会额外校验引用完整性（`scene_id`、`quote_id`、`gap_id` 必须存在，
`group_id` 必须属于同一场景）。

| 内容 | 字段 | 要求 |
| --- | --- | --- |
| 作品与版本 | `book` | 记录 `work_id`、`canonical_sha256`、长度；不同作品不得混入同一样本 |
| 场景范围 | `scenes[].start_cp/end_cp` | 覆盖包含相关叙述的连续区间；边界不确定时写 `boundary_uncertain` |
| 对白范围与类型 | `quotes[].start_cp/end_cp`、`kind` | 范围精确到引号及必要标点；心声、引用、集体声音分别标注 |
| 匿名分组 | `quotes[].group_id` | 场景内同一人使用同一字符串；`resolvable=false` 时为 `null` |
| 被叙述打断的发言 | `quotes[].fragments` | 同一发言的多个片段分别给出范围 |
| 身份证据 | `quotes[].evidence_refs[].visible_from_cp` | 记录证据位置与“从这里起才可展示该身份” |
| Gap | `gaps[]` | 左右对白、四状态决策，以及必须保留的证据片段 `must_keep` |

## 3. 判定规则

1. **不强行确定**：证据不足（只有语气、只有一句短对白、只有“另一个人说”而前文未出现）时标
   `resolvable: false`。多条无法判定的对白不能合并成一个虚构人物。
2. **不按轮流分配**：不因为“A 说完该 B 说”就确定归属；同一人可以连续发言。
3. **保持场景连续**：心理描写、环境描写、固定字数、新人物出现都不自动结束场景；
   只有出现明确的交谈对象/时间/地点切换证据时才标 `BREAK`。
4. **人未到话先到**：先出现的声音先建立一个分组；后文揭示身份时用 `evidence_refs` 记录
   揭示位置与 `visible_from_cp`，不要回填到更早的位置。
5. **不可判定要单列**：`resolvable: false` 的对白计入覆盖率分母，不计入“已接受准确率”分子。
6. **区分操作语义**：`UNKNOWN`（确认为无法判断）与“暂时跳过”不同，后者只影响处理队列。

## 4. 争议与复核

- 保留原始判断，再把争议处理记录在 `review.disagreements`，不覆盖第一版判断。
- 关键难例（长 Gap、三人插话、声音先出现、后文揭示）尽量复查；条件允许时用第二人独立标注。
- 作品级划分：调参/校准/测试按整部作品分开，同一场景与重叠窗口不得跨集合泄漏；
  测试集冻结后不用于调提示词。

## 5. 数据准备与规模

- 初步验证：3～5 部作品、约 300～500 段对白及完整上下文。
- 正式验收：约 1,000～2,000 段对白，分布在多个完整场景。
- `examples/` 中的样例是**原创最小样例**，只用于校验工具格式与边界，不能替代真实作品评测。

## 6. 结果报告口径

报告对白提取精确率/召回率、场景错误切断/连接率、分组准确率、同人 pairwise F1、已接受准确率与
覆盖率、无证据强标率、新人物误建/漏建、难例分项指标、每万字 token/费用/耗时。
样本不足时必须给出样本量与不确定性，不得宣布达标。

## 7. 工具用法（T05 起）

用扫描器候选生成可填写的模板，再人工补全判定：

```powershell
# 1) 生成模板（候选的 resolvable 默认为 false、group_id 为 null，kind 为 unknown）
uv run --project backend python backend/scripts/gold_standard.py template --text .\作品.txt --out evaluation\manifests\作品.json

# 2) 人工填写 group_id / resolvable / kind / evidence_refs（模板不是金标准）

# 3) 校验结构与引用（可同时给出正文做范围与引号检查）
uv run --project backend python backend/scripts/gold_standard.py validate `
  --gold evaluation\manifests\作品.json --text .\作品.txt
```

- 校验会检查：JSON Schema、scene/quote/gap 引用存在、片段与证据范围合法（fragment ⊆ quote、must_keep ⊆ gap）、
  `resolvable=false` 必须 `group_id=null`、`group_id` 属于同场景参与者、证据 `visible_from_cp` 不早于证据本身，
  以及金标准对白是否被候选扫描器覆盖（覆盖率只说明“提取到了”，不代表归属正确）。
- 离线查看候选扫描结果（不调用模型）：`... scan --text .\作品.txt`。