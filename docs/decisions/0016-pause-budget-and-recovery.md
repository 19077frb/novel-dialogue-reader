# 0016 暂停、预算与故障恢复

日期：2026-09-28 · 状态：已采纳（T14）

## 问题

T10 已经有暂停/恢复与未知结果的基本机制，但缺三样东西：进程重启后的任务可见状态、
「非完成状态 → 用户能做什么」的统一翻译、以及限流/缺凭据这类常见故障的处理。
同时要守住两条底线：**未知付费结果不自动重发**、**默认不做自动付费重算**。

## 选择

1. **恢复动作由后端定义**：新增只读 `GET /api/jobs/{id}/recovery`，按状态返回动作
   （`pause`/`resume`/`run`/`reconcile_keep`/`reconcile_retry`/`new_job`/`open_settings`/`wait`）、
   中文说明、`paid` 标记与建议退避秒数。前端只渲染，不自己推断——避免两侧判断漂移。
2. **启动恢复扫描**（`recover_on_startup`，lifespan 内执行，失败不阻止启动）：
   - 超租约仍是 `DISPATCHED` 的尝试 → `UNKNOWN_OUTCOME`，任务与窗口 → `NEEDS_RECONCILIATION`；
   - `PAUSING` → `PAUSED`（worker 已随进程退出，暂停确定生效）；
   - `RUNNING` → `PARTIAL`（上次执行被中断；已完成窗口保持 `COMPLETED`，未完成窗口留在 `QUEUED`）。
   绝不「把所有 RUNNING 重发」。
3. **未知结果只记录、不重发**：`NEEDS_RECONCILIATION` 的两个动作分别是免费保留与付费重发，
   界面明确标注后者「可能计费」，并且系统在任何路径上都不会自动重发超时的请求。
4. **限流/暂时不可用按有上限的退避重试**：只有「确定没有被处理」的错误自动重试
   （默认 1 次基线、指数退避 `min(base·2^(n-1), cap)`、最多 2 次），每个失败尝试都写 `inference_runs`
   并保留 `error_code`；超时属于结果未知，绝不进入自动重试。
5. **缺凭据可解释、可恢复**：协议不需要密钥（本地网关）或 FakeProvider 时不拦；
   用户选了 `session`/`system` 却取不到密钥时，任务直接落到 `FAILED` +
   `PROVIDER_AUTH_FAILED: 缺少模型凭据…`，`recovery` 返回 `open_settings`。
   这样不会留下「RUNNING 但永远不动」的孤儿任务。
6. **预算不可变 → 用新任务继续**：预算属于任务创建时的快照，不允许在失败后偷偷放宽；
   预算到顶的恢复动作是 `new_job`（付费、需用户显式操作）。已完成窗口命中缓存，
   所以重建任务通常只对未完成窗口计费。
7. **默认关闭自动付费重算**：没有任何自动重建/自动继续的后台循环；预览页提供显式的
   「用当前预算重新处理此范围」入口，并说明「已完成窗口不会重复计费」。
8. **测试用故障注入**：FakeProvider 支持失败脚本，可以来自全局 `NDR_FAKE_PROVIDER_SCRIPT`，
   也可以来自**单个模型配置**的 `params.script`，方便离线验证限流/超时/缺凭据路径而不触网。

## 被放弃的方案

- **前端自己判断「能不能重试」**：会与后端状态机漂移，也无法表达「付费与否」。
- **把 RUNNING 一律重发**：可能对同一窗口重复计费；改为 `PARTIAL` + 用户显式继续。
- **超时也自动重试**：结果未知时重发等于可能重复计费（违反 F15/F20）。
- **失败后允许直接改预算并自动接着跑**：等于在用户不知情时放宽花费上限。
- **缺凭据时回退到 FakeProvider**：会让用户看到假结果（明确禁止）。
- **启动扫描失败就拒绝启动**：数据库未迁移时也能启动服务更实用；失败只记录警告。

## 验证

- `pytest backend/tests` → **309 passed**（T13 时 303；新增 `test_recovery.py` 6 项）：
  - F15：构造「已发出、未落库」的 DISPATCHED 尝试 → 租约内不动；租约过期 → `UNKNOWN_OUTCOME` +
    `NEEDS_RECONCILIATION`；直接重跑任务**不发任何新调用**；显式 `retry` 后才回到队列并成功。
  - `RUNNING` → `PARTIAL`、`PAUSING` → `PAUSED`，已完成窗口保持有效。
  - F20：预算到顶不发调用、恢复动作是 `new_job`（付费）；用更大预算重建后跑完。
  - 限流：默认上限下第一次失败、退避后成功（`inference_runs` = FAILED + SUCCEEDED）；
    上限设为 0 时任务失败并给出 `run` 动作。
  - 超时：`NEEDS_RECONCILIATION`、未知用量保持 NULL、恢复动作只有「保留未知 / 确认重发」。
  - 缺凭据：`FAILED` + `requires_credential=true` + `open_settings`，且原文仍可读。
- 前端：`vitest` → **59 passed**（T13 时 55；新增 `JobPanel` 4 项：真实计数/未知用量/退避提示、
  保留未知与确认重发分别调用、暂停、缺凭据入口）；typecheck/build 通过。
- E2E：`npm --prefix frontend run test:e2e` → **18 passed**（T13 时 15；新增 `recovery.spec.ts` 3 项：
  预算到顶 → 提高预算后显式重算成功；缺 Key → 明确失败 + 指向模型配置且原文可读；
  提供方超时 → 未知结果不自动重发、可保留未知转 `PARTIAL`）。
- 本轮修复的**真实缺陷**：适配器构建失败（缺凭据/配置被删）会让 `run_job` 抛异常、
  任务卡在 `RUNNING`；现在会落到可解释的 `FAILED` 并给出恢复动作。
  另外把「缺凭据」判定限定为「非 FakeProvider + 用户选了密钥模式 + 取不到密钥」，
  避免误伤不需要密钥的本地网关与测试用 FakeProvider。
- 未验证（BLOCKED）：真实提供方下的限流/超时行为（无凭据，属 T16/T18）。

## 迁移与回滚

不新增迁移（沿用 `jobs` / `job_windows` / `inference_runs`）。
回滚 = 删除 `ndr/recovery/`、`domain/recovery.py` 与 `GET /api/jobs/{id}/recovery`，
并把 lifespan 里的启动扫描去掉；任务与尝试记录保留以便追溯费用。