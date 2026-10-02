# 评测配置格式与执行边界

本目录保存可复现的机器配置，不是页面设置，也不是实施计划。改变配置会改变指纹，旧报告须继续关联旧配置。

| 文件 | 实际使用的策略 |
| --- | --- |
| `b0.json` | rule_baseline，离线显式归属规则 |
| `b1.json` / `b2.json` | llm、context-1、不复核；当前执行路径相同 |
| `b3.json` | llm、context-2、不复核 |
| `b4.json` | llm、context-2、每窗口最多复核3条待定对白 |

## 字段

- `config_id`、`label`、`notes`：配置身份和说明。
- `strategy`：rule_baseline 或 llm。
- `context_policy`：context-1 / context-2，实际传给上下文策略。
- `reading_mode`：实际用于任务和预测读取。
- `budget.max_rechecks`：真实路径转换为策略的待定对白复核条数；不是全窗口轮次。
- `scene_state`、`prompt_version`、`model`：记录并参与指纹，但当前真实路径不据此切换场景能力、提示词或模型。模型由 `--profile-id` 指定，提示词使用实际程序实现。
- `budget.max_input_tokens` / `max_output_tokens`：被记录，但当前真实路径未接入任务硬额度。不能据此保证费用上限。

读取与指纹实现为 `backend/src/ndr/evaluation/configs.py`；实际调用由 `runner.py` 和 `live.py` 决定。配置名和历史备注不代表所有设想已经接入。

真实调用需明确 `--allow-live --profile-id`，会产生费用并写入书库。离线检查和对照门槛见 [评测方法](../ablations.md)。
