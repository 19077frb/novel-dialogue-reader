# 0025 真实提供方联调：纠错重发、提示词收紧与本地参数

日期：2026-09-28 · 状态：已采纳（T07/T09 范围，live 联调）

## 问题

在用户环境用真实提供方（`https://api.deepseek.com/v1`，模型 `deepseek-v4-flash` / `deepseek-v4-pro`）处理
《义妹生活 第一卷》时，接连暴露四类真实问题（每一类都先用探针复现，再改代码）：

1. `PROVIDER_TIMEOUT: 请求超时（30.0 秒）`：强模型 + 大窗口超过默认 30 秒；
2. `模型返回空内容`：推理模型把输出预算花在 `reasoning_content`（实测 8000 token 上限仍被思考吃光，38.7 秒后
   `finish_reason=length`、`content` 为空）；
3. `模型输出不是合法 JSON`：`max_tokens` 偏小导致 JSON 被截断；
4. `模型输出未通过校验：['undeclared_new_speaker']` / `unknown_speaker`：模型自造说话人引用
   （例如 `speaker:浅村悠太`）或忘记先声明 `temp_ref`。

## 选择

1. **per-profile 本地参数 `timeout_seconds`**：`ChatCompletionsAdapter` 从 `params` 里取走该键（不发进请求体），
   覆盖默认超时；大窗口/强模型可设 120–300 秒。
2. **提示词收紧为 `labeling-2`**：显式写出说话人引用的唯一合法写法（`NEW` 先声明 `temp_ref`；
   `EXISTING` 只能用给定的 `S1`/分组 ID；人名只能进 `description`）。
3. **契约错误纠错重发一次**（调度器）：坏 JSON / 空内容 / 校验不通过时，带着具体问题重发一次；
   两次都失败才让窗口失败，且每次调用各写一条 `inference_runs`（用量不被吞）。
4. **失败必须可定位**：错误带 `finish_reason`、`reasoning_content` 线索与脱敏响应片段（决策 0024 的延续）。
5. **推荐参数写进文档**：`{"thinking": {"type": "disabled"}, "max_tokens": 8000, "timeout_seconds": 180}`——
   其中 `thinking.disabled` 是 DeepSeek 侧关闭思考的写法（实测把「38.7 秒空内容」变成「3 秒有效 JSON」）。

## 被放弃的方案

- **默认把超时调到很大**：会让真正卡死的请求挂很久；改为按配置覆盖，默认仍 30 秒。
- **自动使用 `reasoning_content` 当答案**：那是思维链，不是回答；只在错误里提示，不偷偷采用。
- **无限重试直到成功**：会放大费用；重发严格限制为一次。
- **放宽校验接受自造 speaker id**：会破坏「只引用给定 ID」的契约，宁可重发/报错。

## 真实联调证据（用户环境，真实计费）

| 场景 | 结果 |
| --- | --- |
| 连接测试（deepseek-v4-flash） | 0.8 秒、149/70 tokens、`ok=true` |
| 关闭思考前后对比（同一窗口 7 目标） | 关闭前：38.7 秒、28623 字符思考、`content` 空；关闭后：3.6 秒、有效 JSON |
| 纠错重发（构造坏输出） | 首次失败 → 带问题重发 → 第二次成功；两次各记一条 `inference_runs` |
| `deepseek-v4-flash` 小窗口 | 1 窗口 1 次调用，5 accepted / 2 unknown |
| `deepseek-v4-pro` 小窗口 | 7/7 accepted（提示词收紧后无需重发） |
| `deepseek-v4-pro` 全范围 37–6218 | **COMPLETED**：2 窗口 2 次调用、12926/9297 tokens、47 accepted / 45 unknown、`unknown_runs=0` |
| 超时路径 | 30 秒默认超时确实产生 `PROVIDER_TIMEOUT` 并进入人工对账（不自动重发），改用 180 秒后完成 |

## 仍未验证

- 其它网关/模型族（OpenAI 官方、兼容网关、本地 llama.cpp 等）未联调；
- 效果质量未评测（无人工金标准）：45/92 条被判 UNKNOWN 是模型保守 + 我们的「不确定就拒答」策略，
  要在 T16 的评测里量化，而不是在这里下结论。