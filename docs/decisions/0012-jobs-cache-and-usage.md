# 0012 任务、缓存与用量口径

日期：2026-09-28 · 状态：已采纳（T10）

## 问题

T10 要把「跑一次推理」变成可暂停、可恢复、可对账、可计费的任务：窗口与检查点怎么落库、
什么算同一份缓存、预算怎么预留与结算、未知远程结果怎么处理。

## 选择

1. **任务与窗口分离**：`jobs` 保存整任务状态与检查点（含 `SceneState.snapshot()`），
   `job_windows` 保存每个窗口的 `window_id`/`target_ids_json`/`dependency_hash`/`state`/`lease_until`。
   窗口按文档顺序执行，完成即写检查点；**再次运行同一任务会跳过已完成窗口**（门槛）。
2. **每个窗口只产生一次有效提交**：窗口在执行前先建 `inference_runs`（PREPARED），
   提交后置为 DISPATCHED 并再次提交，然后**不带事务**地调用适配器，最后用新事务
   应用结果（T09 引擎）并结算 usage。
3. **缓存键 = 语义输入**（`storage/cache.py`）：原文版本、目标 IDs、**完整实际输入的指纹**、
   模型与生成参数、协议/提示/schema/策略版本、依赖哈希、阅读模式、horizon。
   **排除** job_id、preview/process 目的与显示选项——所以「先预览、再处理同范围」命中同一条缓存（F16）。
   命中时不调用模型、不新增推理尝试，直接在任务里标记该窗口来自缓存。
4. **预算先预留、后结算**：调用前用窗口估算（正文 + 输出预留）判断是否超 `max_input_tokens`，
   超了就把任务置为 BUDGET_EXHAUSTED 并停止（不再追加调用，F20）；调用后按提供方 usage 结算。
   **未知 usage 保持 NULL** 并计入 `unknown_usage_runs`，同时把预留量按保守口径计入花费。
5. **未知结果不自动重发**：DISPATCHED 状态在租约内没有落库结果（进程中断、提供方超时）时，
   `reconcile_stale_runs` 把尝试标为 UNKNOWN_OUTCOME、窗口与任务标为 NEEDS_RECONCILIATION；
   调度器遇到这种窗口直接停下，等用户显式选择：`retry`（窗口回到 QUEUED，允许再发）或
   `keep_unknown`（保留未知，任务转 PARTIAL）。（F15）
6. **暂停语义诚实**：`pause` 在 RUNNING 时只置 PAUSING，当前窗口结束后才进入 PAUSED，
   界面提示“不承诺远程请求已停止计费”；`resume` 从检查点继续（PREPARED 的尝试可安全重试）。
7. **估算不调用模型**：`POST /api/books/{id}/estimates` 用 T08 的窗口规划给出窗口数、目标数与 token 估算，
   并注明“启发式口径、非计费依据”；缺价格资料时只给 token，不伪造金额。

## 被放弃的方案

- **把窗口状态只放在内存**：重启就丢，无法跳过已完成窗口，也无法支持暂停/恢复。
- **调用期间持有数据库事务**：会在网络等待期间占用 SQLite 写锁（DEVELOPMENT 5.5 明确禁止）。
- **缓存键包含 job_id 或 preview/process**：会让预览与处理各付一次费，违反 F16 与“同输入复用”。
- **超时后自动重发**：可能重复计费；改为 NEEDS_RECONCILIATION + 用户显式选择。
- **预算超限时继续调用并事后记账**：可能产生用户未授权的花费；改为调用前预留即停。
- **把未知用量写成 0**：会让费用看起来免费，明确禁止。

## 验证

- `pytest backend/tests` → **283 passed**。新增 `test_cache.py` 4 项（同语义同键、提示/策略/模式/horizon
  变化改变键、键里没有 job_id/purpose、存储往返且重复写入不覆盖）与 `test_jobs.py` 8 项
  （预览→处理命中缓存且发送次数不增加、同幂等键同摘要复用任务、同键不同摘要 409、
  已完成窗口不重复调用、未知用量不写 0 且已知用量按口径结算、预算到顶不发调用、
  未知结果不自动重发且显式 retry 才回到 QUEUED、暂停在窗口之间生效、估算纯本地）。
- `ruff` 全绿；前端 `vitest` 29 passed、typecheck/build 通过（类型已跟随 `JobDetailOut` 契约）。
- 本轮修复的问题：`run_job` 无条件把任务置 RUNNING，覆盖了 PAUSING 导致暂停失效（改为保留 PAUSING）；
  任务 API 草稿里的 `__import__` 取模型与无意义调用已清理；`JobOut` → `JobDetailOut` 契约变更后
  前端类型与测试夹具同步更新。
- 未验证：真实提供方的任务执行（无凭据，T07 起 BLOCKED）；真实并发/多进程调度属 T14。

## 迁移与回滚

不新增迁移（沿用 T01 的 jobs/job_windows/inference_runs/result_cache）。
回滚 = 删除 `jobs/` 与 `storage/cache.py`；已写入的任务与尝试是审计数据，保留以便追溯费用。