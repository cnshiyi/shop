# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-03 16:05 CST
- 状态：已完成固定巡检；本轮未发现需要修改运行代码的问题。
- 最近提交：本轮提交后以当前 `HEAD` 为准。
- 本轮范围：在 `TODO.md` 固定任务全部完成后，按控制台固定巡检清单复核后台 API 路由拆分、云资产生命周期唯一到期事实、机器人返回链、Telegram `callback_data` 长度、任务中心状态统计、废弃 app 回流和迁移变更。
- 本轮结论：后台 API 仍收口到 `/api/auth/` 与 `/api/admin/`；废弃 runtime app 未恢复；云资产到期事实仍只来自 `CloudAsset.actual_expires_at`；任务中心 pending/failed 统计和机器人返回链聚焦测试未发现回归。
- 本轮改动：仅更新自动化中文记录和版本记录。

## 最近验证

- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/tests.py cloud/tests.py cloud/tests_task_center.py cloud/task_center.py cloud/api_tasks.py cloud/lifecycle_tasks.py cloud/sync_jobs.py` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2` 通过，共 10 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2` 通过，共 14 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2` 通过，共 49 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."` 确认废弃 app 未安装，`CloudAsset` 到期字段只有 `actual_expires_at`，`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`，`CloudAssetDashboardSnapshot` 未恢复到期字段。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run` 输出 `No changes detected`；当前沙箱仍无法连接默认 MySQL `127.0.0.1:3306`，因此会打印迁移历史一致性检查 warning。
- 红线关键字扫描未发现订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流；命中项仍为资产侧唯一到期事实或固定 IP 回收时间同步。
- 废弃 app 目录扫描无输出。
- `git diff --check` 通过。

## 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划，因为当前沙箱禁止连接本地 `127.0.0.1:3306`。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

## 下一步

- 继续按固定巡检清单监控机器人返回链、云资产生命周期唯一到期事实、后台任务中心状态统计和云资产同步 worker 可观测性。
- 若前端仍调用旧 `/api/dashboard/` 或根 `/api/` 后台业务前缀，需要在前端仓库同步切换到 `/api/auth/` 与 `/api/admin/`。
