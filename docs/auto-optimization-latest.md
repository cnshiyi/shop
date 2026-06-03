# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-03 16:05 CST
- 状态：已完成固定巡检、路由拆分后续复核和提交前验证；本轮未修改运行代码。
- 最近提交：本轮提交后以当前 `HEAD` 为准。
- 本轮范围：读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、本文件、`docs/refactor-version-record.md` 末尾、`AGENTS.md` 和 `TODO.md`；由于 `TODO.md` 已全部勾选，按固定巡检清单执行。
- 本轮结论：后台 API 路由拆分后的后端契约继续通过；云资产生命周期仍只以 `CloudAsset.actual_expires_at` 作为唯一资产到期事实；订单表、计划快照、旧退款入口和废弃 runtime app 未回流；机器人返回链和 Telegram `callback_data` 长度控制未发现新问题。
- 前端只读观察：`/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src` 及已知前端文档路径未检出旧 `/api/dashboard`、`/api/users`、`/api/task-list` 或 `/api/plan-settings` 调用。

## 最近验证

- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- 首次编译命令误包含不存在的 `cloud/tasks.py`，已用实际存在模块重跑并通过：`UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/tests_task_center.py`。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2` 通过，共 10 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2` 通过，共 14 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2` 通过，共 49 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."` 确认废弃 app 未安装，`CloudAsset` 到期字段只有 `actual_expires_at`，`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`，`CloudAssetDashboardSnapshot` 未恢复到期字段。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run` 输出 `No changes detected`；当前沙箱仍无法连接默认 MySQL `127.0.0.1:3306`，因此会打印迁移历史一致性检查 warning。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput` 可生成完整迁移计划；SQLite 仍打印不支持 `db_comment` / `db_table_comment` 的预期 warning。
- 默认 `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput` 因当前沙箱禁止连接本地 MySQL `127.0.0.1:3306` 失败，属于环境限制。
- 红线关键字扫描未发现订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流；命中项仍为资产侧唯一到期事实、固定 IP 回收时间同步或 `_asset_expires_at` 临时属性。
- 废弃 app 目录扫描无输出。
- `git diff --check` 通过。

## 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划，因为当前沙箱禁止连接本地 `127.0.0.1:3306`。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端扫描仅覆盖已知本地源码和文档路径；未做浏览器联调。

## 下一步

- 继续按固定巡检清单监控机器人返回链、云资产生命周期唯一到期事实、后台任务中心状态统计和云资产同步 worker 可观测性。
- 后续若切到前端仓库，可继续用前端测试或浏览器联调复核 `/api/auth/` 与 `/api/admin/`。
