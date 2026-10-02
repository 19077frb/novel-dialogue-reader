# 评测对照方法

本页负责对照设计、复现条件和判定门槛，不记录个人工作进度。配置字段见 [配置说明](configs/README.md)，历史结果见 [报告说明](reports/README.md)。

## 对照范围

| 配置 | 当前实际路径 | 可用于比较什么 |
| --- | --- | --- |
| B0 | 不调用模型的显式归属规则 | 规则基线，不能证明模型质量 |
| B1 / B2 | 同一真实任务路径，context-1、无额外复核 | 目前不能用两者证明场景状态开关收益 |
| B3 | context-2 长叙述保守筛选，无额外复核 | 与 B2 比较上下文筛选 |
| B4 | context-2，最多复核每窗口3条待定对白 | 与 B3 比较旧条数式有限复核 |

配置中的 `scene_state` 被记录但没有传给当前真实运行路径；B1/B2不是已实现的场景开关消融。B4沿用评测的条数式复核，不是应用页面的全窗口复核轮次。强模型路由没有对应评测配置入口。

默认上下文策略为 context-1；不因已有 context-2 实现就宣称它更好。

## 离线复现

在项目根目录运行，不调用模型：

```powershell
uv run --project backend python -m ndr.evaluation validate --manifest evaluation/manifests/dev.json
uv run --project backend python -m ndr.evaluation loss --manifest evaluation/manifests/dev.json --context-policy context-2 --output data/validation/context-loss.json
```

真实比较需要已保存模型配置、可用凭据及明确费用授权。使用相同清单、模型、程序版本和阅读模式，只更换 B2/B3/B4 配置；在 `run` 命令中显式提供 `--profile-id` 和 `--allow-live`。新报告写入独立路径，不覆盖历史报告。

注意：评测配置的输入/输出额度目前未传给真实任务，不能把配置JSON中的额度当作硬上限。真实评测会导入书籍和写入配置指定的书库，须准备专用评测书库及模型配置，并在服务商侧控制费用。

## 判定要求

- `must_keep_violations` 必须为0；关键证据被删掉时不能只讨论省Token。
- 同时比较已接受准确率、覆盖率、错误场景边界和难例，不以单一指标宣称成功。
- 用量包含全部调用及复核/重试，不能只算最后一次回答。
- 可确定样本少于 `--min-sample`（默认30）时不能宣布达标；真实作品还须按作品划分独立测试。
- 只有真实、足量且可复现的证据才能支持修改默认策略。

仓库最小样例的历史证据账没有触发长叙述压缩，B1～B4历史报告为 NOT_RUN。它们不提供真实模型质量或筛选收益结论。
