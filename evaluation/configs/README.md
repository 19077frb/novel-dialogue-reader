# 评测配置（T16）

| 配置 | 含义（PLAN） | 是否调用模型 |
| --- | --- | --- |
| `b0.json` | B0：仅明确归属规则，无 LLM 的最低成本基线 | 否（纯规则） |
| `b1.json` | B1：固定窗口 + 完整上下文 + LLM 批量标签 | 是（需 `--profile-id --allow-live`） |
| `b2.json` | B2：B1 + 持续场景状态与边界判断（当前引擎默认行为） | 是（需 `--profile-id --allow-live`） |
| `b3.json` | B3：B2 + 长 Gap 保守筛选（`context_policy=context-2`，`max_rechecks=0`） | 是（需 `--profile-id --allow-live`） |
| `b4.json` | B4：B3 + 有限局部复核（`max_rechecks=3`，复核回到保守策略补回证据） | 是（需 `--profile-id --allow-live`） |

字段：

```json
{
  "config_id": "b2-scene-state",
  "label": "……",
  "strategy": "llm",              // rule_baseline | llm
  "scene_state": true,            // 是否使用持续场景状态
  "prompt_version": "labeling-2",
  "context_policy": "context-1",     // T17：context-1 保守 / context-2 长 Gap 保守筛选
  "reading_mode": "reread",
  "budget": {"max_input_tokens": 200000, "max_output_tokens": 20000, "max_rechecks": 0},   // T17：max_rechecks → recheck_max_targets
  "model": null,                  // null 表示用 --profile-id 指定的配置
  "notes": "……"
}
```

每个配置都会算出一个**配置指纹**（`fingerprint`），报告里记录它，便于证明“这份数字是这组参数跑出来的”。
T17 的消融（B2/B3/B4）口径与判定门槛见 `evaluation/ablations.md`；压缩丢掉的行文可以用
`python -m ndr.evaluation loss --manifest … --context-policy context-2` 离线复核。
