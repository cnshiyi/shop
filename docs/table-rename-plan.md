# Shop 表名迁移执行计划

## 目标

在不破坏现网数据与外键关系的前提下，把现有历史表名逐步收敛到 `bot / orders / cloud / core` 域命名。

本阶段先做 **计划与分批顺序**，暂不直接改 `INSTALLED_APPS`，也不一次性移动模型定义。

## 原则

- 先完成 **代码导入收口**，再改 `Meta.db_table`
- 先改 **代码已走新域入口** 的模型，再碰高耦合模型
- 优先使用 Django migration + `SeparateDatabaseAndState`
- 表名迁移期间，避免同时大规模改字段名、外键名、应用标签
- 每一批迁移后都执行最小验证：`py_compile`、`manage.py migrate`、关键接口 smoke test

## 建议批次

### 批次 A：低风险基础表

- `users` → `bot_user`
- `balance_ledgers` → `order_balance_ledger`
- `recharges` → `order_recharge`

原因：
- 已有 `bot.models` / `orders.models` 过渡出口
- 业务面广，但结构简单，便于先验证迁移套路

注意：
- `TelegramUsername` 仍未下线前，不要同时动 `telegram_usernames`
- `ForeignKey('accounts.TelegramUser')` 这类引用先保持不变，只改表名

### 批次 B：商品与订单表

- `products` → `order_product`
- `cart_items` → `order_cart_item`
- `orders` → `order_order`

原因：
- 已有 `orders.models` 过渡出口
- 相关服务已开始从 `orders.services` 暴露

注意：
- 保持 `Order` / `CartItem` Python 类名先不动
- 后台接口、机器人下单链路改完再考虑 app label 收口

### 批次 C：云资源主表

- `cloud_server_plans` → `cloud_plan`
- `server_prices` → `cloud_price`
- `cloud_server_orders` → `cloud_order`
- `cloud_assets` → `cloud_asset`
- `servers` → `cloud_server`

原因：
- `cloud.models` 已经作为统一出口
- 云资源链路已开始从 `cloud.services` / `cloud.cache` / `cloud.api` 暴露

注意：
- `CloudServerOrder` 外键和生命周期逻辑较多，放在本批后段执行
- 更换 IP、续费、同步资产、生命周期任务都要在迁移后回归

### 批次 D：监控与统计表

- `address_monitors` → `cloud_address_monitor`
- `daily_address_stats` → `cloud_address_stat_daily`
- `resource_snapshots` → `cloud_resource_snapshot`
- `external_sync_logs` → `core_sync_log`

原因：
- 已新增 `cloud.cache` 过渡层
- `tron/scanner.py` 与 `tron/resource_checker.py` 已开始改走新域入口

注意：
- 监控缓存与定时任务容易受影响，迁移后要主动初始化缓存并做 smoke test

### 批次 E：配置表

- `configs` → `core_site_config`
- `cloud_account_configs` → `core_cloud_account`

原因：
- 影响面相对集中
- 与前几批解耦

## 暂缓项

- `telegram_usernames`

原因：
- 用户策略已决定下线该子表
- 应优先删除代码依赖，再决定是否直接删表或保留空表过渡

建议：
1. 先确认运行时代码对 `TelegramUsername` 零依赖
2. 再新增 migration：删除模型状态 / 可选保留数据库表
3. 最后决定是否真正 `DROP TABLE`

## 每批迁移步骤模板

1. 在过渡域模型上调整目标 `Meta.db_table`
2. 生成 migration，必要时改成 `SeparateDatabaseAndState`
3. 执行 `./.venv/bin/python manage.py migrate`
4. 执行 `./.venv/bin/python -m py_compile ...`
5. 执行关键回归：
   - `DJANGO_TEST_REUSE_DB=1 ./.venv/bin/python manage.py test biz.tests --keepdb --noinput --verbosity 1`
   - `curl -s http://127.0.0.1:8000/api/user/info -H 'Authorization: Bearer session-1'`
6. 记录到 `CHANGELOG.md`
7. Git commit / push

## 当前前置条件状态

- [x] 新域模型出口：`bot/models.py`、`orders/models.py`、`cloud/models.py`
- [x] 新域服务出口：`bot/services.py`、`orders/services.py`、`cloud/services.py`
- [x] 新域 API 出口：`bot/api.py`、`orders/api.py`、`cloud/api.py`
- [x] `TelegramUsername` 运行时依赖清零
- [x] `dashboard_api` 已拆分并并回 `bot/api.py`、`orders/api.py`、`cloud/api.py` + `shop/dashboard_urls.py`
- [ ] `INSTALLED_APPS` 调整方案单独设计

## 建议下一步

`TelegramUsername` 运行时依赖已清零，并已从 Django 状态中下线；下一步可以从批次 A 开始做第一版改表名迁移。
