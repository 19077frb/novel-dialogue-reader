# 0003 schema、迁移与枚举存储的选择

日期：2026-09-28 · 状态：已采纳（T01）

## 问题

T01 要把 DEVELOPMENT.md 第 3 节的逻辑表结构落成真实 SQLite schema，并决定几处会长期影响
迁移、约束与后续任务的做法：枚举怎么存、有没有循环外键、待确认/更正表何时建、
迁移由谁执行、以及迁移配置文件的编码。

## 选择

1. **枚举以 VARCHAR 存字符串值，不建数据库级 CHECK。**
   `storage.base.enum_type()` 用 `sa.Enum(..., native_enum=False, values_callable=...)`，
   数据库里存 `TXT`/`speech` 这类契约取值，ORM 层用 `validate_strings=True` 校验。
   实测：SQLAlchemy 2.1 + Alembic autogenerate 在 `create_constraint=True` 时会把同一个
   CHECK 渲染两遍（`name='book_format'` 与 `name='ck_books_book_format'`），既冗余又难维护。
   取值正确性由后端枚举（唯一权威）与测试保证；新增枚举值需要新迁移。
2. **`books.active_version_id` 不建外键约束。** 它与 `book_versions.book_id` 构成循环外键，
   SQLAlchemy/Alembic 会报循环依赖，SQLite 也不支持 `ALTER TABLE ADD CONSTRAINT`。
   该列保留索引，语义仍是“当前活动版本”，由服务层维护；其它引用一律使用真实外键。
3. **`0002` 提前建立 `review_items` 与 `corrections`。** T12/T13 才使用它们，但先建表可以得到
   真实的“已有库升级”路径（测试：升级到 0001 → 写入数据 → 升级到 head → 数据保留、新表出现），
   而不是只验证一次空库迁移。
4. **迁移由显式命令执行**：`dev.ps1` 在启动前运行 `alembic upgrade head`；
   另提供 `ndr.storage.migrate.run_migrations()` 供脚本/测试使用，并提供
   `NDR_AUTO_MIGRATE=1` 的显式 opt-in（E2E 的隔离数据目录使用）。默认不会自动迁移，
   避免应用隐式改动用户书库。
5. **`alembic.ini` 保持纯 ASCII。** Alembic 用平台默认编码读取配置文件，本机是 GBK，
   中文注释会导致启动直接抛 `UnicodeDecodeError`。README/文档仍用中文说明这一点。
6. **数据库状态如实分档**：`NOT_INITIALIZED`（无 alembic_version）、`OUTDATED`（版本落后于 head）、
   `READY`（等于 head）、`ERROR`（读不出结构），健康检查同时返回 `revision` 与 `head_revision`。

## 被放弃的方案

- **数据库级 CHECK 约束**：见第 1 点，会让 autogenerate 与模型漂移难以稳定。
- **把 `active_version_id` 做成 `use_alter=True` 外键**：SQLite 上 SQLAlchemy 直接跳过该约束，
  等于写了不生效的契约，反而误导。
- **启动时自动迁移（默认开启）**：会让“打开应用”隐式修改书库结构，破坏“用户更正与原文不可变”的
  可追溯性；改为显式命令 + opt-in。
- **在 `alembic.ini` 写中文注释**：平台编码不可控，直接导致无法启动。

## 验证

- `pytest backend/tests` → 41 passed，其中 `test_schema.py` 覆盖：空库迁移到 head、重复迁移安全、
  未迁移库状态、模型与数据库列不漂移、0001→head 升级保留数据、`PRAGMA foreign_keys` 生效、
  外键违例被拒、版本冲突返回期望/当前版本、时间 UTC 往返、枚举以契约字符串落库、
  `model_profiles` 无明文密钥列、UNKNOWN/`user_locked`/`stale`/队列状态互相独立、
  `review_items` 目标恰好一种、JobState 覆盖恢复场景。
- `ruff check backend/src backend/tests backend/scripts` → All checks passed。
- 真实运行：`dev.ps1` 执行迁移后 `GET /api/health` 返回
  `{"database":{"state":"READY","revision":"0002","head_revision":"0002"}}`。

## 迁移与回滚

结构变更一律新增迁移文件（`0003_...`），不修改已发布的 `0001`/`0002`。
`alembic downgrade` 仅用于开发环境；用户书库的回滚依赖备份而非自动降级。