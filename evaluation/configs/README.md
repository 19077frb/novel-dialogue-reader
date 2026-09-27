# 评测配置（T16）

| 配置 | 含义（PLAN） | 是否调用模型 |
| --- | --- | --- |
| `b0.json` | B0：仅明确归属规则，无 LLM 的最低成本基线 | 否（纯规则） |
| `b1.json` | B1：固定窗口 + 完整上下文 + LLM 批量标签 | 是（需 `--profile-id --allow-live`） |
| `b2.json` | B2：B1 + 持续场景状态与边界判断（当前引擎默认行为） | 是（需 `--profile-id --allow-live`） |

字段：

```json
{
  "config_id": "b2-scene-state",
  "label": "……",
  "strategy": "llm",              // rule_baseline | llm
  "scene_state": true,            // 是否使用持续场景状态
  "prompt_version": "labeling-1",
  "context_policy": "context-1",
  "reading_mode": "reread",
  "budget": {"max_input_tokens": 200000, "max_output_tokens": 20000, "max_rechecks": 0},
  "model": null,                  // null 表示用 --profile-id 指定的配置
  "notes": "……"
}
```

每个配置都会算出一个**配置指纹**（`fingerprint`），报告里记录它，便于证明“这份数字是这组参数跑出来的”。