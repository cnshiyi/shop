# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 18:01 CST
- 状态：完成机器人 `callback_data` 长度与返回链专项只读审计，未发现需要立即修复的回归。
- 本轮范围：云资产机器人详情页、续费页、换 IP 页、自动续费回调压缩链路，以及资源监控详情缓存键隔离。

## 审计摘要

- 复查 `bot/keyboards.py` 的 `compact_callback_path`、`append_back_callback`、`_compact_back_button_callback` 缩短链路，确认云资产详情、续费、换 IP、重建迁移、自动续费的嵌套返回路径仍走短回调约定。
- 复查 `bot/tests.py` 里极端长 ID 和深层嵌套回调用例，确认详情页、IP 查询页和自动续费回调都维持 `<= 64` 字节。
- 复查 `cloud/resource_monitor.py`，确认资源详情按钮使用 `_cache_resource_detail()` 生成 16 位哈希短键，不会把地址和时间原样拼进 `callback_data`。
- 本轮未改业务代码，只补中文记录；专项结论是当前机器人高风险回调入口没有发现长度越界或返回链丢失。

## 数据与样本

- 回调压测样本：使用 `999999999999999999` 级别超长资产/订单 ID 与深层嵌套返回路径。
- 资源详情缓存样本：同一地址时间戳下按不同 `user_id` 生成不同短键，验证缓存作用域不会串用户。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_detail_actions_from_long_asset_detail_stay_under_callback_limit bot.tests.RetainedIpRenewalUiTestCase.test_extreme_nested_cloud_callbacks_stay_under_telegram_limit bot.tests.RetainedIpRenewalUiTestCase.test_cloud_auto_renew_callbacks_keep_nested_back_under_limit bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.DashboardTronBalanceQueryTestCase.test_resource_detail_cache_is_scoped_per_user_for_same_address_time --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/keyboards.py bot/handlers.py cloud/resource_monitor.py
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

结果：4 个机器人回调聚焦测试、1 个资源监控缓存测试、Django 系统检查、编译检查以及前后端空白检查均通过。SQLite 的 `db_comment` 警告仍为已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款入口或旧兼容壳。

## 剩余风险

- 本轮是机器人回调只读审计，没有覆盖浏览器页面或 50 万级后台分页耗时。
- 下一轮应回到高数据量页面链路，继续对任务中心、生命周期计划或通知计划做 50 万到 100 万级跳页耗时与数据库对账。
