# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-04 20:05 CST
- 状态：按用户要求处理工作树脏文件。将既有模型主键注释、对应迁移、Django 系统表 MySQL 注释迁移、Redis 失败重连退避测试和 `.env.example` 示例配置整理成可提交状态。
- 本轮提交：`record database comment migrations`；本轮不改生命周期业务逻辑、不触发真实云资源操作、链上转账或生产发布。
- 本轮范围：`bot/models.py`、`cloud/models.py`、`core/models.py`、`orders/models.py` 显式声明 `BigAutoField id` 并补 `db_comment`；新增/保留对应迁移文件；`core/tests.py` 覆盖 Redis 失败退避期间不重复重连；`.env.example` 使用占位数据库名、用户名和密码。
- 本轮结论：模型和迁移一致，默认 MySQL 迁移计划无待执行操作，SQLite 迁移计划可生成完整计划；Redis 退避测试通过。

## 最近验证

- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test core.tests.RedisCacheBackoffTestCase --settings=shop.settings --verbosity=2` 通过，1 个测试 OK。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run --settings=shop.settings` 通过，输出 `No changes detected`。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test core.tests --settings=shop.settings --verbosity=1` 通过，15 个测试 OK。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --settings=shop.settings` 通过，可生成完整迁移计划；SQLite 仅打印不支持 `db_comment` / `db_table_comment` 的预期 warning。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan` 通过，输出 `No planned migration operations`。
- `git diff --check` 通过。

## 剩余风险

- 这批迁移主要是数据库注释和显式主键字段声明，不改变业务生命周期规则；生产环境如已应用这些迁移，提交后代码库与数据库迁移记录重新对齐。
- SQLite 测试环境会继续打印 `db_comment` / `db_table_comment` 不支持的 warning，属于后端能力差异。
- 本轮未执行真实云删除、固定 IP 释放、链上转账或生产发布。

## 下一步

- 脏文件提交后，继续按用户要求做后续测试或巡检；如继续生命周期专项，避免直接运行全局 `lifecycle_tick` 处理无关真实候选。
