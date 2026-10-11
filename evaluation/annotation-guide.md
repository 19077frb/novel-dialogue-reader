# 人工金标准标注指南

本页面向评测标注者，负责坐标、归属、场景、证据和歧义规则，不是产品页面教程。结构以 [gold-standard.schema.json](schemas/gold-standard.schema.json) 为准。

## 坐标与身份

使用该版本规范化全文的 Unicode码点区间 `[start_cp, end_cp)`，开始含、结束不含。先确定规范化正文，再标位置，不用Word页码或浏览器UTF-16偏移。

group_id是场景内匿名分组，可用S1、S2；跨场景不继承，编号整体置换不算错。这与产品展示具体姓名或称呼是不同用途。

无法确定时写 `resolvable: false` 和 `group_id: null`，不要把多条未知对白归为一个虚构人物。

## 必填内容

| 内容 | 字段与要求 |
| --- | --- |
| 作品/版本 | book：work_id、canonical_sha256、规范化长度 |
| 场景 | scenes：连续范围、参与者、边界是否不确定 |
| 引语 | quotes：范围、kind、scene_id、分组及resolvable |
| 分片 | fragments：同一发言被叙述打断时分别标范围 |
| 证据 | evidence_refs：证据范围及visible_from_cp，不能早于证据 |
| 对白间叙述 | gaps：左右引语、四状态边界决策和must_keep证据 |

quote/scene/gap引用必须存在；group属于同场景，fragment在quote内，must_keep在gap内。心声、引用、集体声音应分别判定，不默认是普通对白。

## 判定与复核

术语、强调词、修辞说明、泛指社会观念及没有具体表达者的假想台词模板标为 `other`，不要求单个人物。真实或特定人物的引用仍标为 `quotation` 并判断来源。不能仅因短句、省略号、括号或证据不足就标为 `other`；明确表示沉默且不承载人物表达时才无需归属。人物归属指标可将 speech/thought/quotation 中同一人物视为一致，表达类型准确率需单独报告，不能据此掩盖类型错误。

- 不按轮流说话、语气或固定字数强行归属；同一人可连续发言。
- 普通环境或心理描写、新人物出现不自动结束场景。明确时间、地点或交谈对象变化才支持BREAK。
- 声音先出现、身份后揭示时，记录揭示证据与可见位置，不提前回填。
- UNKNOWN是无法判断，不等于操作队列中的“暂时跳过”。
- 保留初始判断，把争议记录在review.disagreements；关键难例尽量由第二人独立复查。
- 调参、校准和测试按作品隔离，重叠窗口不能跨集合泄漏。

可从多个作品约300～500条对白做初步验证，正式评测应扩大到约1000～2000条及完整场景；这些是数据准备建议，不是现有样本规模。仓库原创最小样例只验证格式与流程。

## 工具

从项目根目录运行：

```powershell
uv run --project backend python backend/scripts/gold_standard.py template --text "作品.txt" --out data/validation/gold-template.json
uv run --project backend python backend/scripts/gold_standard.py validate --gold data/validation/gold-template.json --text "作品.txt"
```

模板中的默认未知判定需要人工填写，模板本身不是金标准。`scan --text "作品.txt"` 可离线查看候选，不调用模型。

校验覆盖schema、引用、范围和扫描器候选匹配；提取覆盖不代表归属正确。报告应同时给出准确率、覆盖率、分组/场景难例、用量和样本不足说明。指标解释见 [报告指南](reports/README.md)。
