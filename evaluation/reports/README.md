# 历史评测报告与复现

本目录保存可复现的公开评测证据，不是内部工作日志。历史JSON保留原始版本和时间，不随产品版本更新而改写。

| 文件 | 历史状态与证据边界 |
| --- | --- |
| `dev-b0-offline.json` | OFFLINE_BASELINE，原创最小样例上的规则指标，非真实模型效果 |
| `dev-b1-notrun.json`～`dev-b4-notrun.json` | NOT_RUN，没有该次真实模型结果 |
| `dev-context-loss.json` | 离线筛选证据账；该样例扫描2个Gap，未触发压缩，不能证明压缩收益 |

历史报告中 `quality_evidence=false`，不得据此宣称产品达到准确率/覆盖率目标。NOT_RUN的原因描述那次运行，不描述维护者现在是否有凭据。

## 复现命令

从项目根目录执行；新结果放独立运行目录，不覆盖历史文件：

```powershell
uv run --project backend python -m ndr.evaluation validate --manifest evaluation/manifests/dev.json
uv run --project backend python -m ndr.evaluation run --manifest evaluation/manifests/dev.json --config evaluation/configs/b0.json --output data/validation/b0-offline.json
uv run --project backend python -m ndr.evaluation loss --manifest evaluation/manifests/dev.json --context-policy context-2 --output data/validation/context-loss.json
```

真实模型评测需专用书库、有效模型配置、费用授权以及 `--profile-id --allow-live`。参数接入限制见 [配置说明](../configs/README.md)，比较方法见 [对照方法](../ablations.md)。

## 读取指标

- grouping.accepted_accuracy：已接受且可确定样本的归属准确率。
- coverage.coverage：覆盖率，拒答会降低覆盖。
- grouping.pairwise_f1：标签置换不变的同人分组指标。
- scenes.wrong_split / wrong_join：错误切断 / 连接。
- grouping.extra_groups / missing_groups：多建 / 漏建分组。
- degenerate：全拒答、全合并等退化提示。
- usage_total：各次调用的累计用量，不只看最后一次回答。
- targets_met：可确定样本不足或准确率/覆盖率不可计算时为null；当前工具门槛为准确率97%、覆盖率70%，这是判定条件，不是已经实现的结果。

实际字段和版本以报告内容为准。旧报告可能缺少后来新增的版本字段；新工具结果不保证逐字一致，不能把格式更新当作效果提升。
