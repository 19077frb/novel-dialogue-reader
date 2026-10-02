# 评测对照方法

本页描述当前对照方法与复现边界。字段见[配置说明](configs/README.md)，历史结果见[报告说明](reports/README.md)。

## 当前对照

| 配置 | 比较用途 |
| --- | --- |
| B0 | 离线规则基线，不能证明模型效果 |
| B2 | 当前章节人物识别、自动确认、保守上下文对白流程 |
| B3 | 相对B2只切换context-2压缩上下文 |
| B4 | 相对B3增加1轮整窗口复核，覆盖全部对白 |

人物确认接受模型候选，优先采用模型POV候选，否则首位候选，与批量处理一致；不以人工正确名单替模型纠错。章内窗口按现有调度执行，不是对并发速度的评测。默认上下文仍为context-1，不因已有压缩实现就宣称它更好。

配置和报告版本已升级，旧B1伪场景开关与旧B4按条数复核配置不再提供；历史报告原样保留，不与新版直接比较。

## 离线复现

在项目根目录执行，不调用模型：

```powershell
uv run --project backend python -m ndr.evaluation validate --manifest evaluation/manifests/dev.json
uv run --project backend python -m ndr.evaluation run --manifest evaluation/manifests/dev.json --config evaluation/configs/b0.json --output data/validation/b0-current.json
uv run --project backend python -m ndr.evaluation loss --manifest evaluation/manifests/dev.json --context-policy context-2 --output data/validation/context-loss.json
```

## 真实比较（需要费用授权）

每个配置使用全新的隔离书库，在该库保存同一模型与参数。不要使用日常书库；已有同一原始书籍会拒绝评测，防止旧结果污染。以下路径和profile-id须换成该专用库的实际值，只有明确允许付费后才能运行：

```powershell
$env:NDR_DATA_DIR = 'D:\\evaluation-runs\\b2-new'
uv run --project backend python -m ndr.evaluation run --manifest evaluation/manifests/dev.json --config evaluation/configs/b2.json --profile-id '<专用库模型配置ID>' --allow-live --output data/validation/b2-current.json
Remove-Item Env:NDR_DATA_DIR
```

B2/B3/B4保持相同清单、模型参数、思考设置、程序版本、阅读模式和预算，仅改变被比较的配置。报告记录配置指纹及实际版本，新报告写独立路径，不覆盖历史证据。额度按每章任务生效，人物仅支持输入额度，不是全书费用硬上限；同时在服务商侧控制费用。

## 判定要求

- must_keep_violations必须为0，关键证据不能为省Token而删。
- 同时比较准确率、覆盖率、场景边界及难例，不能只报单一指标。
- 用量包括人物识别、首次对白、复核及失败重试；未知用量不能当零，LIVE_FAILED保留消耗但不评分。
- 样本少于 --min-sample（默认30）不能宣称达标；真实作品按作品划分独立测试。
- 假提供方仅验证代码链路，不算模型质量证据。仅真实、足量、可复现的结果可支持改变默认策略。

仓库最小样例的历史证据账没有触发长叙述压缩，旧B1～B4报告为NOT_RUN，不支持真实模型质量或压缩收益结论。
