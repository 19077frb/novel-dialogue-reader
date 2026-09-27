# 0011 场景状态、接受策略与身份修订

日期：2026-09-28 · 状态：已采纳（T09）

## 问题

T09 要把 T07 的输出契约与 T08 的窗口变成真正的标注结果：谁在说话（或未知）、场景是否延续、
新声音怎么编号、后文揭示的身份怎么合并，以及人工确认如何不被自动结果覆盖。

## 选择

1. **场景转移只由 Gap 决策驱动**：`CONTINUE` 不变、`UPDATE` **不切场景**（只更新状态）、
   `BREAK` 关闭当前场景并把参与者清空（新场景重新编号）、`UNCERTAIN` 标 `PENDING_BOUNDARY`
   并把该 Gap 记进 `unresolved` 留给下一窗口。沉默不是离场：参与者只在 BREAK 时清空。
2. **状态可序列化传递**：`SceneState.snapshot()/from_snapshot()`（`SCENE_STATE_VERSION="scene-state-1"`），
   T10 会把它写进 `jobs.checkpoint_json`；窗口重叠区重复出现的同一引语按 `quote_id` 覆盖，
   不会产生第二条有效标注（同一引语只有一份有效标注）。
3. **展示编号按首次可靠发言顺序**：S1、S2…只在所属场景内有意义；跨窗口沿用同一分组（不重新编号）；
   编号空出后可复用（`next_label` 取最小可用编号）。
4. **冷启动保守接受策略**（`acceptance-1`，校准前始终按冷启动）：
   - `basis=DIRECT` → `ACCEPTED`；
   - `COREFERENCE`/`RESPONSE_LINK`/`STYLE_ONLY` → `PROVISIONAL` + 待确认项；
   - `INSUFFICIENT` 或 `assignment=UNKNOWN` → `UNKNOWN` + 待确认项，**绝不因此新建人物分组**；
   - 非 speech 的类型判断（心声/引用/集体声音…）接受它本身，但按契约不带 `speaker_ref`，
     因此不会污染普通人物色彩（F10）。
5. **可见时点由后端算**：`compute_visible_from_cp` 取证据中最靠后的位置（没有证据时退回到本句起点）。
   模型自报的 `visible_from_cp` 会被 schema 直接拒绝。这样后文才揭示的身份不会在初读时提前生效（F08/F17）。
6. **人工确认优先**：`apply_window` 先看 `locked_quote_ids`，锁定的对白**完全不采纳**模型结果
   （不写标注、不写历史），只记录 `locked_quote_skipped` 警告（F14）。
7. **身份修订要证据**：`identity_proposals` 没有 `basis` 字段，因此约定「带 `evidence_refs` 视为直接证据」；
   没有证据、证据类型弱、或牵涉人工锁定的分组一律**不自动应用**，只进待确认队列。
   合并时把被合并分组的标注重新指向幸存分组，旧值写入 `annotation_history`，并记录
   `identity_revisions.visible_from_cp`（后文揭示的位置）。
8. **窗口内引用要自洽**：校验允许 `EXISTING` 引用**本次输出里刚声明**的临时人物
   （同一窗口里同一新声音的第二句），否则模型无法表达「本窗口内第二次说话」。

## 被放弃的方案

- **按 Gap 长度判定场景断开**：长度是弱信号；F06 的短 Gap 也可能结束场景，决定权交给模型 + BREAK。
- **UNKNOWN 也建新分组**：会把「听不出来」变成「又一个新人」，正是 PLAN 3 明确禁止的。
- **把风格依据当真**：`STYLE_ONLY` 只做暂定，避免冷启动期大量误接受。
- **自动应用所有身份修订**：会悄悄改变历史投影；只应用明确证据且不碰人工锁定的情况。
- **让模型自报可见时点**：模型看不到阅读进度，必须由后端按证据位置计算。

## 验证

- `pytest backend/tests` → **271 passed**。新增 `test_scene_state.py` 16 项（UPDATE 不切场景、
  BREAK 关旧开新并清空参与者、UNCERTAIN 保留待定、状态快照往返、提示状态有长度上限、
  编号按首次发言顺序且可复用、接受策略四种分支、可见时点取最靠后证据、身份修订的四种判定）
  与 `test_attribution_engine.py` 9 项（F09 UNKNOWN 不建人、F07 三说话人与连续同人、
  F05/F06 UPDATE vs BREAK、F14 锁定不被覆盖、F12/F08 跨窗口沿用分组、后文证据合并记录可见时点、
  F13 坏 JSON 有限重试且拒绝提交不留半成品、坏→好重试后接受、整书窗口规划回归）。
- `ruff` 全绿。
- 修复的问题：校验不允许同一窗口内引用刚声明的临时人物；FakeProvider 重复取件（一次调用消耗两条脚本）；
  `RetryPolicy` 语义含糊（改为“额外重试次数”）；适配器旁路字段 `_usage` 会被 `extra=forbid` 拒绝
  （校验前剥离）。
- 未验证：真实模型效果（T16）；本任务全部用 FakeProvider 验证状态机。

## 迁移与回滚

不新增迁移（沿用 T01 的 scenes/speaker_groups/annotations/annotation_history/scene_memberships/
identity_revisions/review_items）。回滚 = 删除 `scenes/`、`speakers/` 与相关测试；
已写入的标注/修订是用户可见数据，回滚前需导出或清理。