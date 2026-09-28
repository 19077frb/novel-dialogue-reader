# 0024 连接测试的容错与诊断（真实提供方联调）

日期：2026-09-28 · 状态：已采纳（T07 范围修复）

## 问题

真实提供方（`chat-completions-compatible` / `deepseek-v4-flash`）连接测试失败：

```
对照：输入 132 · 输出 64 · 合计 196
错误码：INVALID_MODEL_OUTPUT —— 输出不符合 schema（ValidationError）
```

两个可疑点，都能从代码与用量看出来：

1. 连接测试的 `max_tokens` 固定 64，而**输出正好是 64**：模型很可能被截断成不完整 JSON；
2. 连接测试用的是 `LlmOutput.model_validate_json(text)`（严格 JSON），而标注链路用的是
   `validation.parse_output`（接受整段 JSON **或整段 ```json 代码块**）——同一契约两套解析，
   真实模型只要包一层代码块，连接测试就必然失败。
3. 失败详情只有「输出不符合 schema（ValidationError）」，没有原始片段与具体字段问题，
   用户只能靠猜。

## 选择

1. **同一套解析**：连接测试改用 `validation.parse_output`（内部 `load_json_object`），与标注链路一致：
   接受整段 JSON 或整段代码块，**仍然拒绝**从解释性长文里截取片段。
2. **上限提到 256**：连接测试的回显对象很小，但缩进/代码块/前后空白会放大；64 太紧。
3. **失败必须可定位**：`INVALID_MODEL_OUTPUT` 的 `detail` 带（a）解析/校验问题最多 3 条（loc + message），
   （b）脱敏后的原始输出片段（截断 200 字符）；`content` 为空时返回响应片段（例如内容在
   `reasoning_content` 里）。提供方的 usage 照实回报，不因解析失败丢弃。
4. **提示词更明确**：`connection-2` 要求「不要代码块、不要前后文字」；版本随快照记录，便于回溯。
5. **任务级同样可定位**：标注任务因 `INVALID_OUTPUT` 失败时，`job.last_error` 也带上脱敏片段。

## 被放弃的方案

- **放宽成「从长文里找 JSON」**：会把解释性文字当数据，违反 DEVELOPMENT 4.5。
- **失败时静默重试**：连接测试是一次性显式操作，重试只会掩盖真实问题；重试策略仍只用于标注任务。
- **不返回原始片段**：这正是本次定位困难的根因；片段脱敏且截断，风险可控。
- **把 usage 置空**：提供方确实计费了，必须照实显示。

## 验证

- `backend/tests/integration/test_provider_adapter.py` 新增 3 项：代码块内容 → 连接成功且
  `max_tokens >= 128`；被截断的输出 → 失败详情含「原始输出片段」与片段内容，usage 仍为真实值；
  空 `content`（内容在 `reasoning_content`）→ 详情含响应片段。
- `pytest backend/tests` → **379 passed**（T19 时 376）；`ruff` 全绿。
- **仍未验证**：该真实提供方在修复后的实际表现——需要用户重新点一次「连接测试」；
  若仍失败，界面现在会直接显示原始片段，足够定位（例如仍是截断、或字段不合法）。

## 补充：`模型返回空内容`（同日，同一提供方的处理阶段）

连接测试通过后，实际处理（`mode=process`）报 `INVALID_MODEL_OUTPUT: 模型返回空内容`。这条错误来自
`_content_of`：`choices[0].message.content` 为空。常见原因有三类，都必须在错误里说清楚：

1. **输出预算被推理吃掉**（`finish_reason=length`）：推理模型常把 `max_tokens` 花在思考上，
   800 的默认上限不够 → 提示用户去「模型配置 → 生成参数」写 `{"max_tokens": 4000}`（会覆盖默认值）；
2. **内容落在 `reasoning_content`**：网关把答案放进推理字段 → 提示换非推理模型或按网关文档关闭思考；
3. **响应结构异常**（缺 `choices`/`message`）：直接给出脱敏响应片段。

实现：`_content_of`（及结构异常分支）都会带上 `details.body`（脱敏截断的响应）、`finish_reason`、
`reasoning_chars`/`reasoning_snippet`；调度器把它们写进 `job.last_error`，界面直接可见
（`JobPanel` 一直会渲染 `last_error`）。测试：适配器 2 项 + 任务级 1 项，`pytest` → 382 passed。

**仍未验证**：该提供方在提高 `max_tokens` 后的实际表现——需要用户改配置后重新处理一次。