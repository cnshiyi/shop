# Shop 后端重构迁移图

## 一、目标结构

- `shop/`：Django 配置、路由、启动
- `core/`：公共能力，配置、缓存、加解密、工具
- `bot/`：Telegram 用户、机器人交互、会话相关
- `orders/`：商品、购物车、订单、充值、TRON 支付监控
- `cloud/`：云服务器、套餐、资产、生命周期、资源监控

## 二、目录迁移

### 迁入 `bot/`
- `accounts/models.py` → `bot/models.py`
- `accounts/services.py` → `bot/services.py`
- `accounts/admin.py` → `bot/admin.py`
- 用户与余额相关逻辑已迁入 `bot/api.py`

### 迁入 `orders/`
- `finance/models.py` → `orders/models.py`
- `mall` 中商品/购物车/订单相关模型 → `orders/models.py`
- `tron/parser.py` → `orders/tron_parser.py`
- `tron/scanner.py` → `orders/payment_scanner.py`
- `biz/services/commerce.py` → `orders/services.py`
- `biz/services/payments.py` → `orders/payment_services.py`

### 迁入 `cloud/`
- `mall` 中云套餐/云订单/云资产/服务器模型 → `cloud/models.py`
- `monitoring/models.py` → `cloud/models.py`
- `monitoring/cache.py` → `cloud/cache.py`
- `biz/services/cloud_servers.py` → `cloud/services.py`
- `biz/services/custom.py` → `cloud/services.py`
- `biz/services/monitoring.py` → `orders/services.py` / `cloud/cache.py`

### 删除或下线
- `biz/`：已退出 `INSTALLED_APPS`，当前仅保留兼容导入目录与测试命名空间
- `dashboard_api/`：已退出 `INSTALLED_APPS`，目录本身也已删除，路由已并回 `shop/dashboard_urls.py`
- `dashboard_api/views.py`：已删除
- `accounts_telegramusername` 相关逻辑：下线
- 新规则：迁移完成并验证通过后，立即删除旧实现，不长期保留双实现

## 三、表名迁移

- `users` → `bot_user`
- `balance_ledgers` → `order_balance_ledger`
- `products` / `mall_product` → `order_product`
- `cart_items` / `mall_cartitem` → `order_cart_item`
- `orders` / `mall_order` → `order_order`
- `recharges` / `finance_recharge` → `order_recharge`
- `cloud_server_plans` / `mall_cloudserverplan` → `cloud_plan`
- `server_prices` / `mall_serverprice` → `cloud_price`
- `cloud_server_orders` / `mall_cloudserverorder` → `cloud_order`
- `cloud_assets` / `mall_cloudasset` → `cloud_asset`
- `servers` / `mall_server` → `cloud_server`
- `address_monitors` / `monitoring_addressmonitor` → `cloud_address_monitor`
- `daily_address_stats` / `monitoring_dailyaddressstat` → `cloud_address_stat_daily`
- `resource_snapshots` / `monitoring_resourcesnapshot` → `cloud_resource_snapshot`
- `site_configs` / `core_siteconfig` → `core_site_config`
- `cloud_account_configs` / `core_cloudaccountconfig` → `core_cloud_account`
- `external_sync_logs` / `core_externalsynclog` → `core_sync_log`

## 四、用户名策略

- 保留 `bot_user.username`
- 多用户名用逗号分隔，表示当前有效用户名集合
- 不保留历史用户名
- 删除 `TelegramUsername` 子表及相关查询

## 五、分阶段执行

### 第一阶段：过渡层
- [x] `bot/models.py` 建立 bot 域过渡入口
- [x] `orders/models.py` 建立订单域过渡入口
- [x] `cloud/models.py` 建立云资源域过渡入口
- [x] 增加 `bot/api.py`、`orders/api.py`、`cloud/api.py` 过渡入口

### 第二阶段：清理重复真相
- [x] 下线 `TelegramUsername`（Django 状态已移除，数据库表暂保留）
- [x] 清理 `prefetch_related('telegramusernames')`
- [x] 改为只读写 `bot_user.username`

### 第三阶段：迁移 imports
- [x] 核心运行时模块已基本改从 `bot/orders/cloud` 导入
- [x] `biz` 已降为最薄兼容壳并退出 `INSTALLED_APPS`

### 第四阶段：迁移表名
- [ ] 为目标模型统一补 `Meta.db_table`
- [ ] 生成改表名迁移
- [ ] 执行迁移并验证数据

#### 批次 A 已完成
- [x] `users` → `bot_user`
- [x] `balance_ledgers` → `order_balance_ledger`
- [x] `recharges` → `order_recharge`

#### 批次 B 已完成
- [x] `products` → `order_product`
- [x] `cart_items` → `order_cart_item`
- [x] `orders` → `order_order`

#### 批次 C 已完成
- [x] `cloud_server_plans` → `cloud_plan`
- [x] `server_prices` → `cloud_price`
- [x] `cloud_server_orders` → `cloud_order`
- [x] `cloud_assets` → `cloud_asset`
- [x] `servers` → `cloud_server`

#### 批次 D/E 进行中
- [x] `address_monitors` → `cloud_address_monitor`
- [x] `daily_address_stats` → `cloud_address_stat_daily`
- [x] `resource_snapshots` → `cloud_resource_snapshot`
- [x] `configs` → `core_site_config`
- [x] `cloud_account_configs` → `core_cloud_account`
- [x] `external_sync_logs` → `core_sync_log`

### 第五阶段：删除旧目录
- [ ] 删除 `accounts/finance/mall/monitoring` 中剩余仅为迁移图服务的兼容壳
- [x] 删除 `biz/dashboard_api` 中已无运行时必要的业务实现与 app 注册残留
- [x] 兼容壳已大幅压薄；新实现稳定后优先直接删除旧层
- [x] `shop/dashboard_urls.py` 已完全改走 `bot/orders/cloud` API 入口

### 第六阶段：收尾
- [ ] 执行测试并确认通过
- [ ] 写版本记录 / 更新变更说明
- [ ] 提交 Git
- [ ] 停止巡检任务

## 六、清理原则
- 每完成一段迁移，都必须检查并删除对应旧实现
- 删除顺序：旧函数实现 → 旧 helper → 旧转发层 → 无用 import / 路由 / 文档
- `dashboard_api`、`biz.services.*`、旧模型壳是全面清理旧代码的重点区域
