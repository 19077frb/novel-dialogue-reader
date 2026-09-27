# 0008 模型配置与凭据处理

日期：2026-09-28 · 状态：已采纳（T06）
## 问题

T06 要让用户不改源码就配好 API 地址/模型/密钥，同时保证密钥不回传、不落盘、不进日志，
并且把“系统凭据库不可用”处理成可解释的降级，而不是静默明文保存。还要定义适配器接口，
为 T07 的真实调用与 T10 的预算/缓存留位置。

## 选择

1. **凭据三态 + 明确降级**：`session`（进程内存，退出即失效）、`system`（keyring/Windows 凭据管理器）、
   `none`（不保存）。系统库不可用或写入失败时**降级为会话密钥**并把真实模式改成 `session`，
   同时在响应里返回 `credential_warning`。切换模式或删除配置时两个存储都会清理，避免残留旧密钥。
   测试与 E2E 用 `NDR_CREDENTIAL_BACKEND=session` 显式隔离，绝不触碰真实系统凭据库。
2. **数据库只存引用**：`model_profiles` 保存 `credential_mode` 与 `credential_ref`
   （形如 `model-profile/<id>`），API 响应**只有 `has_key`**，不返回 `credential_ref` 与任何密钥字段；
   测试断言响应文本里既没有密钥也没有 `api_key`/`credential_ref` 字段。
3. **Base URL 必须是 API 根路径**：填成 `.../v1/chat/completions` 这类完整端点返回 422 并给出纠正提示，
   避免“根路径 + 端点”拼出错误地址（DEVELOPMENT.md 5.4）。
4. **params 禁止密钥类字段**：`api_key/token/authorization/password/secret` 等键直接 422，
   防止有人把密钥塞进生成参数表；密钥只能走 `api_key` + 凭据模式。
5. **删除保护**：被任务引用（`jobs.profile_snapshot_json` 含该配置 id）时删除返回 409
   `RESOURCE_CONFLICT`；新增该错误码（同名配置冲突也用它）。
6. **适配器接口在 T06 只定义不实现**：`ProviderAdapter` 声明
   `test_connection / generate_labels / estimate_tokens / normalize_usage / capabilities`；
   协议能力表如实声明“兼容服务不保证支持 json_schema”，`fake-provider` 明确标注仅供测试。
   `POST /api/model-profiles/test` 属于 T07，本任务**不发起任何真实请求**。
7. **前端三态密钥控件**：编辑时提供“保持不变 / 替换密钥 / 清除密钥”，保存成功后立即清空表单
   （含密码框），`api_key` 不进入查询缓存或任何持久化状态；页面文案明确“连接测试与实际识别调用在 T07 提供”。

## 被放弃的方案

- **把密钥存进 `params_json` 或新增一列**：会让密钥随数据库/备份泄露；改为只存引用。
- **系统凭据库不可用时直接报错**：用户就无法保存配置；改为降级 + 警告，并如实报告实际模式。
- **系统库不可用时明文落盘**：明确禁止（DEVELOPMENT.md 2.1）。
- **允许任意 URL 形态**：无法区分“根路径”与“完整端点”，错误拼接后只会得到难懂的 404。
- **在 T06 就实现连接测试**：会把真实网络调用引入配置任务，且与 T07 的适配器/校验设计耦合。

## 验证

- `pytest backend/tests` → **189 passed**。新增 `test_credentials.py` 10 项（会话/系统存储、
  系统不可用与写入失败降级、读取回退、空密钥清除、双存储清理、引用命名）与
  `test_model_profiles.py` 15 项（创建/列表不回传密钥、`has_key`、keep/replace/remove 三态、
  409 版本冲突、系统模式降级警告、同名冲突、未知协议、完整端点 422、params 密钥 422、
  删除与凭据清理、被任务引用时 409、配置操作不产生任何任务、协议能力声明、按配置隔离密钥、
  **数据目录中不含密钥**）。
- `ruff` 全绿；`scripts/verify.ps1` 退出码 0。
- 前端 `vitest` **25 passed**（空状态与 T07 说明、提交密钥但不回显、JSON 参数本地拦截、
  编辑 keep/remove、版本冲突刷新、降级警告展示、删除后回到空状态）。
- Playwright **8 passed**：新增 `settings.spec.ts` 两个用例（新建→编辑→清除→删除全流程，
  并断言页面上不出现密钥；完整端点 Base URL 得到可理解的 422）。
- 说明：真实连接测试与真实模型调用**未执行**（无凭据，且属于 T07），本任务没有任何网络调用。

## 迁移与回滚

不新增迁移（复用 T01 的 `model_profiles`）。回滚 = 删除 `llm/` 与设置页；已保存的会话密钥随进程退出消失，
系统凭据条目需要用户在系统凭据管理器中手动清理（删除配置时程序已尽力清理）。