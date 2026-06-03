# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-03 20:13 CST
- 状态：已完成固定巡检七次复核，未发现需要修改运行代码的新问题。
- 最近提交：本轮提交后以当前 `HEAD` 为准。
- 本轮范围：读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、本文件、`docs/refactor-version-record.md` 末尾、`AGENTS.md`、`TODO.md` 和 `django-shop-backend` 技能；由于 `TODO.md` 已全部勾选，按固定巡检清单执行。
- 本轮结论：`CloudAsset.actual_expires_at` 继续作为唯一资产到期事实；订单表未恢复 `actual_expires_at` 或 `service_expires_at`；计划快照表未恢复实际到期字段；废弃 runtime app 未重新安装；机器人返回链、Telegram `callback_data` 限制、后台任务中心状态统计和迁移/同步保留资产到期事实的聚焦测试继续通过。本轮开始时已有上一轮六次复核文档差异，已保留并在其后继续记录；本轮只读复核固定 IP 保留/回收相关命中，确认仍集中在 `ip_recycle_at` 与资产 `actual_expires_at` 的同步链路，没有恢复订单服务到期事实。

## 最近验证

- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/keyboards.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/provisioning.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py cloud/tests.py cloud/tests_task_center.py` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."` 确认废弃 app 未安装，`CloudAsset` 到期字段只有 `actual_expires_at`，`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`，`CloudAssetDashboardSnapshot` 未恢复实际到期字段。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2` 通过，共 14 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2` 通过，共 59 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_rebuild_source_migration_schedule_preserves_asset_expiry cloud.tests.CloudServerServicesTestCase.test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_order_preserves_asset_expiry_when_migration_due_is_earlier cloud.tests.CloudServerServicesTestCase.test_source_migration_schedule_keeps_asset_actual_expiry cloud.tests.CloudServerServicesTestCase.test_aws_sync_deleted_migration_order_keeps_asset_actual_expiry --settings=shop.settings --verbosity=2` 通过，共 5 个测试。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run` 输出 `No changes detected`；当前沙箱仍无法连接默认 MySQL `127.0.0.1:3306`，因此会打印迁移历史一致性检查 warning。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput` 可生成完整迁移计划；SQLite 仍打印不支持 `db_comment` / `db_table_comment` 的预期 warning。
- 默认 `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput` 因当前沙箱禁止连接本地 MySQL `127.0.0.1:3306` 失败，属于环境限制。
- 红线关键字扫描未发现运行代码恢复订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流；剩余命中为资产侧唯一到期事实、固定 IP 回收时间同步或 `_asset_expires_at` 临时属性。
- 废弃 app 目录扫描无输出。
- 后端旧 API 前缀扫描未发现旧路由重新挂载；`dashboard_api` 命中来自 `core.dashboard_api` 共享 helper、历史文档和确认旧入口不可解析的测试。
- 前端只读扫描显示 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src` 与 `/Users/a399/Desktop/data/vue-shop-admin/docs` 未检出旧 `/api/dashboard`、`/api/users`、`/api/task-list` 或 `/api/plan-settings` 调用；仅 `/Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文字说明残留。
- `git diff --check` 通过。

## 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划，因为当前沙箱禁止连接本地 `127.0.0.1:3306`。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端 `DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 描述残留；本轮未跨仓库修改。

## 下一步

- 继续按固定巡检清单监控机器人返回链、云资产生命周期唯一到期事实、后台任务中心状态统计和云资产同步 worker 可观测性。
- 后续若切到前端仓库，优先清理 `DEVELOPMENT.md` 中旧 `/api/dashboard/` 文档说明，并继续用前端测试或浏览器联调复核 `/api/auth/` 与 `/api/admin/`。
