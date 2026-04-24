# `INSTALLED_APPS` 切换计划

## 当前状态

当前运行配置已经不再保留任何旧应用。

已经完成退出的旧 app：

- `biz`（已从 `INSTALLED_APPS` 移除，目录本体已删除）
- `dashboard_api`（已从 `INSTALLED_APPS` 移除，目录本体已删除，路由已并回 `shop/dashboard_urls.py`）
- `monitoring`（已从 `INSTALLED_APPS` 移除，监控模型 fresh DB 建表与状态归属已改由 `cloud` 承接）
- `mall`（已从 `INSTALLED_APPS` 移除，商品/购物车/订单与云模型 fresh DB 建表已改由 `orders/cloud` 承接）
- `finance`（已从 `INSTALLED_APPS` 移除，充值模型 fresh DB 建表与状态归属已改由 `orders` 承接）
- `accounts`（已从 `INSTALLED_APPS` 移除，`bot_user` 与 `order_balance_ledger` 的 fresh DB 建表已改由 `bot/orders` 承接）

同时新域已经成为真实运行时归属：

- `bot/models.py` / `bot/services.py` / `bot/api.py`
- `orders/models.py` / `orders/services.py` / `orders/api.py` / `orders/ledger.py` / `orders/payment_scanner.py`
- `cloud/models.py` / `cloud/services.py` / `cloud/api.py` / `cloud/cache.py` / `cloud/resource_monitor.py`

## 当前状态说明

运行时层面的旧 app 收口已经完成：

- `accounts` / `finance` / `mall` / `monitoring` 已全部退出 `INSTALLED_APPS`
- 上述旧目录也已从当前工作树删除
- `dashboard_api` 已并回 `shop/dashboard_urls.py`
- `biz` 已退出运行时，目录本体也已删除

仍需保留的“旧 app 痕迹”只存在于历史 migration 链与相关复盘文档中，而不再属于运行时目录结构。

## 已完成的关键前置条件

- [x] 目标表名已全部迁移完成
- [x] `TelegramUsername` 已从 Django 状态下线
- [x] `dashboard_api/views.py` 已删除
- [x] `shop/dashboard_urls.py` 已完全路由到 `bot/orders/cloud` API 入口
- [x] `dashboard_api` 已从 `INSTALLED_APPS` 移除
- [x] `orders/ledger.py` 已接管余额流水记账入口
- [x] `bot.TelegramUser`、`orders.Recharge`、`orders.BalanceLedger` 已成为真实模型来源
- [x] `orders.Product` / `orders.CartItem` / `orders.Order` 已成为真实模型来源
- [x] `cloud.CloudServerPlan` / `cloud.ServerPrice` / `cloud.CloudServerOrder` / `cloud.CloudAsset` / `cloud.Server` / `cloud.CloudIpLog` 已成为真实模型来源
- [x] `cloud.AddressMonitor` / `cloud.DailyAddressStat` / `cloud.ResourceSnapshot` 已成为真实模型来源
- [x] `cloud.cache` 已成为真实监控缓存实现来源
- [x] `orders/payment_scanner.py` 与 `cloud/resource_monitor.py` 已接管原 `tron/*` 运行时职责

## 已确认的脱钩结果

### `accounts`
- `bot.0001_initial` 已承担 `bot_user` 的 fresh DB 建表职责
- `orders.0002_move_balanceledger_state_from_accounts` 已承担 `order_balance_ledger` 的 fresh DB 建表职责
- 结论：`accounts` 已不再需要保留在 `INSTALLED_APPS`

### `finance`
- `orders.0001_initial` 已承担 `order_recharge` 的 fresh DB 建表职责
- 结论：`finance` 已不再需要保留在 `INSTALLED_APPS`

### `mall`
- `orders.0003_move_product_cart_order_from_mall` 与 `cloud.0001_initial` 已承担原 `mall` fresh DB 建表职责
- 结论：`mall` 已不再需要保留在 `INSTALLED_APPS`

### `monitoring`
- `cloud.0002_addressmonitor_resourcesnapshot_dailyaddressstat` 已承担原 `monitoring` fresh DB 建表职责
- 结论：`monitoring` 已不再需要保留在 `INSTALLED_APPS`

## 已完成结果

这份计划涉及的运行时收口目标已经全部完成：

- `biz` 已删除，测试已迁入 `cloud/tests.py`
- `accounts` / `finance` / `mall` / `monitoring` 已退出 `INSTALLED_APPS` 且目录本体已删除
- `dashboard_api` 已并回 `shop/dashboard_urls.py`，目录本体已删除
- fresh test DB、`check`、`makemigrations --check --dry-run` 已反复验证通过

剩余的旧 app label 只存在于历史 migration 链与变更复盘中，不再构成当前运行时目录结构。

## 当前结论

后端 `INSTALLED_APPS` 收口已经完成。

当前下一步不再是后端旧 app 收口，而是继续清理历史文档口径与前端仓库收尾。
