# 0020 评测工具与「指标不等于达标」

日期：2026-09-28 · 状态：已采纳（T16）

## 问题

T16 要给出效果数字（已接受准确率 ≥97%、覆盖率 ≥70%），但当前**没有真实模型凭据、没有预算、
没有人工确认的真实作品样本**。硬把离线/假提供方的结果写成质量结论，等于伪造证据。
同时必须先把评测工具做对：指标本身要能在手算样例上验证，退化预测不能被写成达标。

## 选择

1. **先交付可离线运行的评测工具**：清单校验（作品级划分 + 金标准结构/引用）、配置与配置指纹、
   B0 规则基线、指标计算、报告与复现说明；真实调用必须显式 `--allow-live` 并指定 `--profile-id`。
2. **指标口径按 DEVELOPMENT 7.3 落地**：提取 precision/recall/F1、错误切断/连接、匹配后分组准确率、
   同人 pairwise F1、覆盖率、未知强标率、新人物误建/漏建、退化标记、样本量。
3. **标签置换不变**：分组只看“是否同组”，映射在金标准场景内按最大票数贪心配对，
   预测的分组键带场景命名空间，避免不同场景里重复的 S1 被当成一致。
4. **拒答与不可确定分开处理**：拒答不计入“已接受准确率”的分子分母，但计入覆盖率分母；
   金标准 `resolvable=false` 的样本单列，并被「未知强标率」惩罚（预测硬给分组就会暴露）。
5. **退化预测必须显式标记**：全拒答（coverage 0、accuracy=null）、全合并（pairwise F1 下降 + missing_groups）、
   单分组、对不可确定样本全部强标，都会写进 `degenerate`，任何单一指标都不能拿来宣布达标。
6. **样本不足不达标**：可确定样本少于 `--min-sample`（默认 30）时 `targets_met=null`；
   只有真实运行、样本足够、且达到 97%/70% 才可能为 true。
7. **`quality_evidence` 的门槛写死**：真实模型 + 全部 book COMPLETED + 提供方不是测试用 FakeProvider +
   样本足够；否则为 false 并把原因写进 `blocks`。B0 基线的 `calls=0` 是事实（没有调用模型），
   与“提供方未返回 usage 的未知用量”不是一回事。
8. **真实运行失败就说失败**：`--allow-live` 后如果没有 `--profile-id` → `NOT_RUN`；
   任务失败（缺凭据/限流/超时）→ `LIVE_FAILED` + 原因；绝不回退到假数据、绝不写假数字。

## 被放弃的方案

- **用 FakeProvider 的结果充当效果数字**：违反门槛；本实现把它标成 `quality_evidence=false`。
- **只做单一指标（准确率）**：全合并/全拒答都能在某个单指标上“看起来不错”；
  必须同时看覆盖率、pairwise、退化标记与样本量。
- **把不可确定样本算进已接受准确率**：会奖励“硬给一个说话人”；改为单列 + 未知强标率。
- **默认允许真实调用**：会在用户不知情时花钱；`--allow-live` 必须显式给出。
- **把 B0 基线调得“好看”**：B0 的价值是低成本参照；它的弱点（同人不同称被拆开）如实暴露。

## 验证

- `pytest backend/tests` → **346 passed**（T15B 时 328；新增 `test_evaluation_metrics.py` 9 项 +
  `test_evaluation_manifest.py` 5 项 + `test_evaluation_run.py` 4 项）。`ruff` 全绿。
- 手算样例（`test_evaluation_metrics.py`）：完美预测 → 各指标 1.0；**匿名标签置换**（S1/S2 → A/B）指标不变；
  错误分场（切断 2 处 → `wrong_split=2`、场景准确率 0.5）；错误连接（→ `wrong_join=1`）；
  **全拒答** → coverage 0、`accepted_accuracy=None`、`degenerate.all_refusal=true`、`targets_met=None`；
  **全合并** → pairwise F1 0.5、`missing_groups=1`、`degenerate.all_merge=true`、`targets_met=false`；
  漏提取 → recall 0.8、coverage 0.6；强标不可确定样本 → `unknown_force_rate=1.0` 且不计入已接受准确率；
  默认 min_sample=30 时 `targets_met=None`。
- 清单/配置：`validate` 对仓库清单返回 0 并列出作品/书/划分；构造「同一作品跨两个 split + 重复 book_id +
  缺金标准」的坏清单 → 三类 error 全部命中。B0 规则基线在「回答」这种叙述上不再误判（表面形式必须干净）。
- 报告（真实生成，可复现）：`evaluation/reports/dev-b0-offline.json` 记录
  `state=OFFLINE_BASELINE`、`accepted_accuracy=1.0`（2/2 匹配映射）、`coverage=0.4`、
  `pairwise_f1=null`（本样本没有同组对）、`sample.gold_resolvable=4`、`sample_sufficient=false`、
  `targets_met=null`、`quality_evidence=false`、`usage_total.calls=0`；
  `dev-b1-notrun.json` / `dev-b2-notrun.json` 记录 `NOT_RUN` 与「需要显式 --allow-live」的原因。
- 真实运行链路（离线验证）：显式 `--allow-live` + FakeProvider 配置时，评测命令真的完成
  「导入 → 建任务 → 引擎 → 读投影 → 记指标」，并因提供方是测试用假提供方而保持 `quality_evidence=false`。
- **未验证（BLOCKED）**：真实模型上的 B0/B1/B2 对比与 97%/70% 结论——没有凭据、预算与人工确认样本，
  因此 `IMPLEMENTATION_STATUS.md` 的 Quality 一列为 BLOCKED，`targets_met` 一律 null。

## 迁移与回滚

无迁移。回滚 = 删除 `ndr/evaluation/{metrics,manifest,configs,baselines,live,runner}.py`、`__main__.py`
与 `evaluation/{manifests,configs,reports}`；已生成的真实报告（若有）应保留，不要删除历史结论。