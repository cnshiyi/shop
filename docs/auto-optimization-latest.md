# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-04 12:04 CST
- 状态：已用默认 MySQL 配置完成一轮非破坏性项目冒烟验证。
- 最近提交：本轮提交后以当前 `HEAD` 为准。
- 本轮范围：读取自动化记忆、当前 git 状态、最近提交和 `django-shop-backend` 技能；按用户要求用默认 MySQL 配置跑一遍项目，选择非破坏性验证，不创建/删除 MySQL 测试库，不执行真实云资源、真实支付、链上广播或生产操作。
- 本轮结论：默认数据库为 MySQL，`manage.py check`、`migrate --plan`、`makemigrations --check --dry-run`、关键模块编译、默认库 ORM 只读内省和短启动 `runserver` 冒烟均通过；`runserver` 监听 `127.0.0.1:18080` 时返回 HTTP 200，随后已主动中断。字段内省继续确认 `CloudAsset.actual_expires_at` 是唯一资产到期事实，废弃 runtime app 未安装。

## 最近验证

- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/keyboards.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/provisioning.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py cloud/tests.py cloud/tests_task_center.py` 通过。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile run.py core/management/commands/ensure_dashboard_admin.py` 通过。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py ensure_dashboard_admin --help` 通过，确认命令帮助文案为中文且未执行创建/更新管理员。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...默认 MySQL 只读内省..."` 确认 `db_vendor=mysql`，当前库名为 `a`，废弃 app 未安装，`CloudAsset` 到期字段只有 `actual_expires_at`，`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`，`CloudAssetDashboardSnapshot` 未恢复实际到期字段，并可读取 `TelegramUser`、`CloudAsset`、`CloudServerOrder` 计数。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2` 通过，共 14 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2` 通过，共 59 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_rebuild_source_migration_schedule_preserves_asset_expiry cloud.tests.CloudServerServicesTestCase.test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_order_preserves_asset_expiry_when_migration_due_is_earlier cloud.tests.CloudServerServicesTestCase.test_source_migration_schedule_keeps_asset_actual_expiry cloud.tests.CloudServerServicesTestCase.test_aws_sync_deleted_migration_order_keeps_asset_actual_expiry --settings=shop.settings --verbosity=2` 通过，共 5 个测试。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run` 输出 `No changes detected`。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput` 可生成完整迁移计划；SQLite 仍打印不支持 `db_comment` / `db_table_comment` 的预期 warning。
- 默认 `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput` 本轮可连接默认数据库并输出 `No planned migration operations`。
- 默认 MySQL 短启动冒烟通过：`runserver` 在 `127.0.0.1:18080` 返回 HTTP 200，随后已主动中断。
- 红线关键字扫描未发现运行代码恢复订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流；剩余命中为资产侧唯一到期事实、固定 IP 回收时间同步或 `_asset_expires_at` 临时属性。
- 废弃 app 目录扫描无输出。
- 后端旧 API 前缀扫描未发现旧路由重新挂载；`dashboard_api` 命中来自 `core.dashboard_api` 共享 helper、历史文档和确认旧入口不可解析的测试。
- 前端只读扫描显示 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src` 与 `/Users/a399/Desktop/data/vue-shop-admin/docs` 未检出旧 `/api/dashboard`、`/api/users`、`/api/task-list` 或 `/api/plan-settings` 调用；仅 `/Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文字说明残留。
- `git diff --check` 通过。

## 剩余风险

- 本轮未跑完整测试套件。
- 本轮未用 MySQL 创建/删除测试库运行 Django 测试；为避免触碰数据库删除类操作，仅执行默认库非破坏性冒烟。
- 本轮默认数据库 `migrate --plan` 可执行且无待迁移操作，但未在生产或独立真实 MySQL/MariaDB 环境执行完整迁移演练。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端 `DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 描述残留；本轮未跨仓库修改。

## 下一步

- 继续按固定巡检清单监控机器人返回链、云资产生命周期唯一到期事实、后台任务中心状态统计和云资产同步 worker 可观测性。
- 后续若切到前端仓库，优先清理 `DEVELOPMENT.md` 中旧 `/api/dashboard/` 文档说明，并继续用前端测试或浏览器联调复核 `/api/auth/` 与 `/api/admin/`。
