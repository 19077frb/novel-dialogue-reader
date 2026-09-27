# 0017 初读身份还原与码点定位

日期：2026-09-28 · 状态：已采纳（T15）

## 问题

T11 的初读 horizon 只处理了「单条标注的证据时点」，身份**合并/拆分**却立即生效：
如果模型在第十章才发现「第一章的两个声音其实是一个人」，那么用初读模式看第一章时，
两条对白已经同色同号——这就是 F17 说的“提前泄露合并关系”。

另外，后端所有坐标都是**码点**，而前端一直在用 JS 字符串下标（UTF-16 单元）切片：
`'😀'.length === 2`，遇到 emoji / 扩展汉字时颜色、编号与候选都会错位（F11）。

## 选择

1. **把身份修订的可还原信息写进快照**：无论是引擎自动合并（T09）还是人工 merge/split（T12），
   都在 `identity_revisions.snapshot_json.revert` 里记录
   `{"quotes": {quote_id: 旧分组}, "groups": {新分组: 旧分组}}`。没有这份信息就无法在不破坏历史的前提下还原。
2. **投影按 horizon 还原**：`reading_mode=initial` 且 `visible_from_cp > visible_horizon_cp` 的修订
   在**读时**被反向应用（最新优先，避免多次合并互相覆盖）；被还原的对白用旧分组着色/编号，
   图例分别列出两个分组。`reread` 不做还原；缺少时点的旧数据按“已生效”处理（保守、不猜）。
3. **只读**：还原只发生在投影查询里，不写数据库、不调用模型；响应新增 `identity_reverts` 计数，
   让界面能如实说明「有几处身份是后文才揭示的」。
4. **阅读页显式提交 horizon**：初读用「本章末端」，重读不提交；界面显示 horizon 与
   「N 条后文证据暂不显示 / M 处身份合并在后文才揭示」。
5. **码点 ↔ UTF-16 统一换算**：新增 `src/text/codepoints.ts`，渲染器所有切片（候选、标注、ruby）
   都先换算成 UTF-16 下标；节点范围用后端权威 `end_cp`。
6. **任务完成后失效整族投影查询**：`['annotations']` 前缀失效。此前只失效 horizon=null 的那个键，
   导致「处理完成 → 回阅读页」时读到旧的空投影（真实现象：颜色不出来）。
7. **测试用合并脚本**：FakeProvider 增加 `params.script="split_then_merge"`（仅测试），
   让 F17 可以在离线环境端到端复现，而不是只靠直接写数据库的单元测试。

## 被放弃的方案

- **在前端按 horizon 过滤**：前端拿不到「哪一句原本属于哪个分组」，会算错，也会破坏“投影是唯一事实来源”。
- **改数据库当前投影**：会破坏历史与其它阅读模式，也违反「投影查询只读」。
- **把合并时点记成“不可见就拒绝合并”**：会让重读也看不到合并结果；F17 要求的是**按时点展示**，不是丢弃。
- **把 JS 字符串当码点用（现状）**：emoji/扩展汉字下无法正确定位；DEVELOPMENT 4.1 明确禁止。
- **只失效已知的查询键**：键里含 horizon，阅读页用的是另一个键，必须按前缀失效整族。

## 验证

- 后端：`pytest backend/tests` → **314 passed**（T14 时 309；新增 `test_visibility.py` 4 项）：
  - F17：直接构造「文末才合并」的修订 → 初读 horizon 之下 `identity_reverts=1`、两个编号/两种颜色、图例两条；
    horizon 越过证据 → 合并为一条；`reread` 直接显示合并；投影前后标注/历史/修订行数不变（只读）。
  - F17（端到端离线）：FakeProvider `split_then_merge` 跑完整流程 → 第一章初读显示 S1/S2，重读显示 S1。
  - F11：同一句重复对白 ID 不同；astral 段落按码点计数（11 码点 / 13 UTF-16 单元）；
    每段引语的 `[start_cp, end_cp)` 都能 `locate` 回原文。
  - F04：ruby 注音不进 canonical 文本、插图登记为资源、脚本与样式不渲染、
    跨块引语范围覆盖两个节点且定位回来是 `synthetic` 换行拼接。
- 前端：`vitest` → **65 passed**（T14 时 59；新增 `codepoints` 3 项 + `DocumentRenderer` astral 3 项）。
- E2E：**23 passed**（T14 时 18；新增 `reading-visibility.spec.ts` 3 项 + `review.spec.ts` 空候选独立用例
  + `settings.spec.ts` 的「不需要密钥」用例）。
- 本轮修复的**真实缺陷**：
  1. 处理完成后阅读页可能继续显示旧的空投影（只失效了带 horizon 的键之外的那一个）；
  2. 前端按 UTF-16 下标切片导致 astral 字符错位；
  3. 设置页从未提供 `credential_mode=none`，使 T14 的「缺凭据」规则对本地无鉴权网关变成死路。
- 未验证（BLOCKED）：真实模型在长文中揭示身份的时点行为（无凭据，属 T16）。

## 迁移与回滚

不新增迁移（`identity_revisions.snapshot_json` 为文本，扩展 `revert` 字段向后兼容）。
回滚 = 去掉 `horizon_identity_reverts` 与 `identity_reverts` 字段、把渲染器切回 UTF-16 下标；
历史数据无需清理（旧快照没有 `revert`，会被当作“已生效”）。