# `INSTALLED_APPS` 切换计划

## 当前状态

当前运行配置仍保留旧应用：

- `biz`
- `accounts`
- `mall`
- `finance`
- `monitoring`

已经不再保留：

- `dashboard_api`（已从 `INSTALLED_APPS` 移除，路由已并回 `shop/dashboard_urls.py`）

同时新域已经成为真实运行时归属：

- `bot/models.py` / `bot/services.py` / `bot/api.py`
- `orders/models.py` / `orders/services.py` / `orders/api.py` / `orders/ledger.py`
- `cloud/models.py` / `cloud/services.py` / `cloud/api.py` / `cloud/cache.py`

## 为什么现在还不能直接删剩余旧 app

### 1. 历史 migration 仍绑定旧 app label

当前最大的硬钉子已经不再是运行时代码，而是 Django 迁移机制本身：

- 历史 migration 依赖 `accounts` / `finance` / `mall` / `monitoring`
- fresh test DB 初始化时仍需要这些 app label 存在
- 不能为了删目录直接改整条历史迁移链

### 2. 旧 app 仍承载“兼容 state 壳”职责

当前真实模型已经迁入新域，但旧 app 还保留最低限度兼容入口：

- `accounts.models`：仅导出 `bot.TelegramUser`、`orders.BalanceLedger`
- `finance.models`：仅导出 `orders.Recharge`
- `mall.models`：已清空
- `monitoring.models`：仅导出 `cloud.AddressMonitor`、`cloud.DailyAddressStat`、`cloud.ResourceSnapshot`

这说明运行时真实业务已收口，但旧 app 仍承担迁移/兼容层职责。

### 3. `biz` 仍有测试与旧导入兼容压力

当前情况：

- `biz/services/cloud_servers.py`、`payments.py`、`monitoring.py`、`rates.py`、`commerce.py`、`users.py`、`cloud_queries.py`、`custom.py` 都已经是兼容壳
- `biz/services/__init__.py` 已缩成最薄惰性映射表
- 但 `biz.tests` 仍直接 patch 旧兼容路径，如 `biz.services.cloud_servers.CloudServerOrder`

结论：`biz` 已不是主实现承载层，但在删除前还需要先处理测试入口和旧导入兼容策略。

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
- [x] `mall` 已不再承载真实模型
- [ ] `accounts` / `finance` / `mall` / `monitoring` 仍需保留在 `INSTALLED_APPS` 中，以承接历史 migration app label
- [ ] 历史 migrations 仍全面依赖旧 app label，不能粗暴替换

## 已确认的剩余 app 依赖图阻塞

### `accounts`
- `finance.0001_initial` 依赖 `accounts.0008_telegramuser_cloud_reminder_muted_until`
- `mall` 早期多条迁移依赖 `accounts` 旧节点
- `monitoring.0001_initial` 依赖 `accounts.0008_telegramuser_cloud_reminder_muted_until`
- 结论：`accounts` 目前是整张历史迁移图的上游根节点，暂时不能移除

### `finance`
- `accounts.0011_move_telegramuser_state_to_bot` 依赖 `finance.0003_move_recharge_state_to_orders`
- `orders.0001_initial` 依赖 `finance.0002_alter_recharge_table`
- 结论：`finance` 仍是 `accounts` 与 `orders` 的状态迁移桥接节点，暂时不能移除

### `mall`
- `accounts.0011_move_telegramuser_state_to_bot` 依赖 `mall.0028_switch_user_fk_to_bot`
- `orders.0003_move_product_cart_order_from_mall` 依赖 `mall.0028_switch_user_fk_to_bot`
- 已实测：直接移除 `mall` 会在 `makemigrations` 阶段触发 `NodeNotFoundError`
- 结论：`mall` 虽已无真实模型，但仍是关键迁移桥接 app，暂时不能移除

### `monitoring`
- `accounts.0011_move_telegramuser_state_to_bot` 依赖 `monitoring.0003_switch_user_fk_to_bot`
- `monitoring.0004_remove_dailyaddressstat_monitor_and_more` 仍挂在其自身迁移链下
- 结论：`monitoring` 虽已无真实模型，但仍是关键迁移桥接 app，暂时不能移除

## 建议切换顺序

### 阶段 1：继续缩旧入口

- 继续减少 `biz/services` 兼容壳暴露面
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