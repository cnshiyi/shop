# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 01:24 CST
- 状态：完成 `TODO.md` 全部明确任务后的固定巡检，并修复一个明确安全问题：支付扫描器资源详情按钮缓存按用户隔离，避免同一监控地址同一时间给不同用户推送时详情串读。
- 本轮范围：`orders/payment_scanner.py`、`orders/tests.py`；未改动云资产生命周期事实、订单到期字段、废弃 runtime app、前端代码或真实云资源。

## 巡检结论

- `TODO.md` 当前明确任务均已完成；本轮按 `docs/auto-optimization-control.md` 固定巡检清单执行。
- 运行时 `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- `CloudAsset.actual_expires_at` 仍是云资产唯一真实到期事实；未恢复订单侧 `service_expires_at`、旧计划快照或旧退款入口。
- 机器人返回链和 `callback_data` 聚焦测试通过；任务中心状态统计聚焦测试通过。
- 发现并修复 `orders.payment_scanner._cache_resource_detail()` 使用 `detail_id[:16]` 作为短 key 的问题：相同地址和相同秒级时间下，不同用户的资源详情可能共用同一个按钮 key。现已按 `user_id` 参与哈希生成 16 字节短 key，并保持无用户场景的旧 key 规则。

## 最近验证

- 后端检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- 编译检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile orders/payment_scanner.py orders/tests.py` 通过。
- 固定巡检聚焦测试：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase cloud.tests_task_center.CloudTaskCenterApiTestCase cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_does_not_fallback_to_order_asset_expiry --settings=shop.settings --verbosity=2` 通过。
- 本轮修复聚焦测试：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test orders.tests.TronMonitorStatsTestCase.test_tx_detail_cache_is_scoped_per_user_for_same_hash orders.tests.TronMonitorStatsTestCase.test_resource_detail_cache_is_scoped_per_user_for_same_address_time --settings=shop.settings --verbosity=2` 通过。
- 代码检查：`git diff --check` 通过。
- SQLite 测试库输出不支持 `db_comment` / `db_table_comment` 的 warnings，属于当前测试环境预期差异。

## 剩余风险

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除数据或生产发布。
- 本地 50 万压测数据仍保留；清理属于删除数据操作，需要单独确认。
- 后端仓库仍有本轮无关未跟踪文档 `docs/jisou-bot-functions.md`、`docs/telegram-search-development-plan.md`、`docs/telegram-search-large-scale-architecture.md`，本轮未处理。

## 下一步

- `TODO.md` 当前无新的未完成明确任务；下一轮继续按固定巡检清单执行，只修复一个明确安全问题，并完成验证、中文记录和提交。
