# 评测配置

配置版本为2.0；用于开发评测，不是阅读页面设置。执行复用当前“整章识别人物 → 自动确认 → 对白窗口处理”流程，人物按章节顺序沿用。模型与生成参数由已保存的 --profile-id 指定，提示词采用当前实现，报告记录程序版本。

| 文件 | 策略 |
| --- | --- |
| b0.json | 离线显式归属规则，不调用模型 |
| b2.json | 当前章节流程，context-1，不额外复核 |
| b3.json | 当前章节流程，context-2，不额外复核 |
| b4.json | context-2，每窗口额外复核1轮，每轮覆盖全部对白 |

B1没有真正实现独立的场景开关，已删除其配置；历史报告不改写。

## 字段与额度

- config_version：必须为“2.0”。config_id、label、notes：配置身份与说明。
- strategy：rule_baseline / llm；context_policy：context-1 / context-2。
- reading_mode：initial / reread，传给任务及结果读取。
- budget.max_recheck_rounds：每窗口额外复核轮次，0关闭。不是条数；保留人工锁定，使用正常调度器的容量拆分及恢复检查点。
- budget.max_format_retries：对白输出校验失败的重试次数，0～5，默认1，不含首次调用；不是复核轮次。
- budget.max_input_tokens / max_output_tokens：每章对白任务的输入/输出额度；省略或null为不限。人物识别任务仅支持输入额度，沿用max_input_tokens。**不是全书总费用上限，也不能限制人物输出消耗。**
- inference_options.thinking_mode：default / disabled / enabled / adaptive；reasoning_effort：default / low / medium / high。人物与对白使用同一设置，default沿用模型配置。

不接受旧max_rechecks、scene_state、prompt_version、model字段或拼错的字段，避免记录但不生效。旧自定义配置需人工迁移为2.0：重新决定轮次数，不能把旧条数直接当轮次。

真实运行必须明确 --allow-live --profile-id，会产生费用并写入书库。每次使用新的专用书库及该库的模型配置；同一原始书籍已存在会拒绝运行，避免旧标注、人工资料和缓存污染对照。不能指向日常书库。失败显示LIVE_FAILED并保留已知费用和未知用量提示，不参与质量评分；未授权/未指定模型为NOT_RUN。没有文字的章节沿用应用跳过逻辑。

配置预算不能替代服务商费用限制。方法与隔离运行示例见[评测方法](../ablations.md)，历史证据见[报告说明](../reports/README.md)。
