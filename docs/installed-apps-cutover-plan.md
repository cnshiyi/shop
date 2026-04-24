# `INSTALLED_APPS` 切换计划

## 当前状态

当前运行配置仍保留旧应用：

- 无

已经不再保留：

- `biz`（已从 `INSTALLED_APPS` 移除）
- `dashboard_api`（已从 `INSTALLED_APPS` 移除，路由已并回 `shop/dashboard_urls.py`）
- `monitoring`（已从 `INSTALLED_APPS` 移除，监控模型 fresh DB 建表与状态归属已改由 `cloud` 承接）
- `mall`（已从 `INSTALLED_APPS` 移除，商品/购物车/订单与云模型 fresh DB 建表已改由 `orders/cloud` 承接）
- `finance`（已从 `INSTALLED_APPS` 移除，充值模型 fresh DB 建表与状态归属已改由 `orders` 承接）
- `accounts`（已从 `INSTALLED_APPS` 移除，`bot_user` 与 `order_balance_ledger` 的 fresh DB 建表已改由 `bot/orders` 承接）

同时新域已经成为真实运行时归属：

- `bot/models.py` / `bot/services.py` / `bot/api.py`
- `orders/models.py` / `orders/services.py` / `orders/api.py` / `orders/ledger.py`
- `cloud/models.py` / `cloud/services.py` / `cloud/api.py` / `cloud/cache.py`

## 当前状态说明

运行时层面的旧 app 收口已经完成：

- `accounts` / `finance` / `mall` / `monitoring` 已全部退出 `INSTALLED_APPS`
- 上述旧目录也已从当前工作树删除
- `dashboard_api` 已并回 `shop/dashboard_urls.py`
- `biz` 已退出运行时，仅保留测试命名空间骨架

仍需保留的“旧 app 痕迹”只存在于历史 migration 链与相关复盘文档中，而不再属于运行时目录结构。

### 3. `biz` 仅剩测试命名空间压力

当前情况：

- `biz/services/*` 与 `biz/models.py` 已删除
- `biz.tests` 仍保留为测试命名空间
- 旧 patch 路径已切到新域，例如 `cloud.services.CloudServerOrder`

结论：`biz` 已不再承担服务兼容职责，剩余是否继续清理，主要取决于是否还要保留 `biz.tests` 这个测试入口名。

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

## 当前主要阻塞点

- [x] 运行时代码已基本不再直连旧模型 app
- [x] 运行时 API 已不再依赖旧 `dashboard_api` 视图模块
- [x] `dashboard_api` 已从 `INSTALLED_APPS` 移除
- [x] `biz` 已从 `INSTALLED_APPS` 移除
- [x] `monitoring` 已从 `INSTALLED_APPS` 移除
- [x] `mall` 已从 `INSTALLED_APPS` 移除
- [x] `finance` 已从 `INSTALLED_APPS` 移除
- [x] `accounts` 已从 `INSTALLED_APPS` 移除
- [ ] 历史 migrations 仍保留旧 app label 历史链，但运行时已不再需要保留这些旧 app 注册

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

## 建议切换顺序

### 阶段 1：继续缩旧入口

- 继续减少 `biz` 剩余测试命名空间的历史包袱
- 逐步把测试从 `biz.services.*` patch 改到新域入口
- 清理 README / 架构 / cutover 计划中的旧口径

### 阶段 2：评估移除 `biz`

只有在下面条件满足后才建议动：

- `biz.tests` 不再依赖旧兼容路径
- 运行时代码不再从 `biz` 导入
- `biz.models` 不再承担任何必需兼容职责

### 阶段 3：最后评估移除 `accounts/finance/mall/monitoring`

只有在下面条件满足后才建议动：

- 已确认历史 migration loader 不再需要这些 app 在 `INSTALLED_APPS` 中出现，或已有稳定替代方案
- fresh test DB 从零迁移完整通过
- admin / contenttypes / auth 权限数据不会因 app label 缺失而异常

已确认的真实阻塞示例：

- 当前若直接移除 `mall`，`makemigrations` 会报 `NodeNotFoundError`
- 具体依赖链为：`accounts.0011_move_telegramuser_state_to_bot` 仍依赖 `('mall', '0028_switch_user_fk_to_bot')`
- 这说明剩余旧 app 的主要阻塞点已经不是运行时代码，而是跨 app 的历史迁移依赖图

## 当前结论

`INSTALLED_APPS` 收口已经进入最后阶段，但剩余问题主要是 Django 机制与历史迁移，而不是业务实现本身。

换句话说：

- 运行时真实代码收口已经基本完成
- `dashboard_api` 已成功退出运行时 app 集
- 下一步不该再做大规模业务迁移，而该集中验证还能不能进一步拿掉 `biz`，以及如何安全处理剩余旧 app label。