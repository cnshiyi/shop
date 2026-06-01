# 数据库命名规范

## 目标

- 文档必须反映当前真实数据库约定，不能只写理想规则。
- 保持历史表兼容，不因为纯命名整理破坏现有数据与迁移链。
- 后续新增表、字段、索引、约束按同一套项目规则执行。

## 总原则

- 历史表：保持现状，不做无业务收益的硬改名。
- 新增运行时表：优先使用 `域前缀_单数语义名`。
- 约束/索引：名称显式、可读、统一前缀。
- 外键字段：统一使用 `<target>_id` 列语义，Django 默认即可。
- 枚举值：统一使用小写蛇形或固定小写短词。

## 1. 表名规则

当前项目真实运行约定不是复数表名，而是：

```text
<domain_prefix>_<singular_semantic_name>
```

当前域前缀：

- `core_`：共享配置、云账号、外部同步日志。
- `bot_`：Telegram 用户、登录账号、后台操作日志、聊天记录。
- `order_`：商品、购物车、余额流水、充值、商品订单。
- `cloud_`：云套餐、云资产、生命周期、通知、同步快照。

新增表默认按当前运行约定命名，例如：

- `core_site_config`
- `core_cloud_account`
- `core_sync_log`
- `bot_user`
- `bot_operation_log`
- `order_product`
- `order_balance_ledger`
- `cloud_asset`
- `cloud_notice_plan`
- `cloud_auto_renew_plan`

不要在同一业务域里新建另一套复数表名，例如不要再新增：

- `cloud_assets`
- `cloud_server_orders`
- `balance_ledgers`
- `external_sync_logs`

除非这是一次明确设计过的迁移，并且同时提供兼容、数据迁移和回滚方案。

## 2. 当前真实表名清单

`core`：

- `core_site_config`
- `core_cloud_account`
- `core_sync_log`

`bot`：

- `bot_telegram_login_account`
- `bot_user`
- `bot_operation_log`
- `bot_telegram_chat_archive`
- `bot_telegram_group_filter`
- `bot_admin_reply_link`
- `bot_telegram_chat_message`

`orders`：

- `order_product`
- `order_cart_item`
- `order_balance_ledger`
- `order_recharge`
- `order_order`

`cloud`：

- `cloud_plan`
- `cloud_price`
- `cloud_order`
- `cloud_asset`
- `cloud_asset_dashboard_snapshot`
- `cloud_asset_sync_job`
- `cloud_ip_log`
- `cloud_lifecycle_plan_note`
- `cloud_lifecycle_plan`
- `cloud_notice_plan`
- `cloud_auto_renew_plan`
- `cloud_auto_renew_retry_task`
- `cloud_auto_renew_patrol_log`
- `cloud_user_notice_log`
- `cloud_address_monitor`
- `cloud_address_stat_daily`
- `cloud_resource_snapshot`

## 3. 云资源表结论

- `cloud_asset` 是唯一云资源事实表。
- 历史 `cloud_server` 表已拆除。
- 代码中的 `Server` 模型仅作为兼容投影映射到 `cloud_asset`。
- 新增云资源状态、同步、安全确认逻辑应优先挂到 `cloud_asset` 或明确的 `cloud_*` 辅助表，不要恢复 `cloud_server`。

## 4. 模型名规则

- Python 类名使用 PascalCase。
- 模型类语义使用单数。

示例：

- `TelegramUser`
- `BalanceLedger`
- `CloudServerOrder`
- `ExternalSyncLog`

## 5. 字段名规则

- 小写蛇形。
- 避免缩写不清。
- 时间字段统一以 `_at` 结尾。
- 布尔字段统一以 `is_` / `has_` / `can_` 开头。

推荐示例：

- `created_at`
- `updated_at`
- `completed_at`
- `service_expires_at`
- `is_active`
- `is_sensitive`
- `provider_resource_id`

## 6. 外键字段规则

- 模型层使用语义化字段名：`user`、`order`、`monitor`、`account`。
- 数据库列默认落为：`user_id`、`order_id`、`monitor_id`、`account_id`。

不要使用：

- `uid`
- `oid`
- `cid`
- `ref1` / `ref2`

## 7. 唯一约束与索引命名规则

- 唯一约束：`uniq_<table>_<semantic>`。
- 普通索引：`idx_<table>_<semantic>`。
- 外键索引：默认 Django 即可，特殊场景再显式补。

示例：

- `uniq_cloud_asset_provider_resource`
- `uniq_order_balance_ledger_trace`
- `idx_cloud_asset_public_ip`

## 8. 枚举值命名规则

- 小写。
- 单词间下划线连接。
- 不混中英文。
- 不混大小写。

示例：

- `pending`
- `completed`
- `renew_pending`
- `aws_lightsail`
- `aliyun_simple`
- `manual_adjust`

## 9. 多账户预留规则

所有未来可能按账户拆分的数据表，优先至少具备以下一种方式。

方案 A：显式外键，适用于已有统一账户主表的情况：

- `account = ForeignKey(CloudAccountConfig, ...)`

方案 B：作用域 + 标识，适用于暂时没有统一账户主表、但未来需要扩展的场景：

- `account_scope`
- `account_key`

当前已按此规范处理：

- `cloud.ResourceSnapshot`
- `core.ExternalSyncLog`

## 10. 迁移策略

原则：

- 不为“纯好看”大规模重命名历史表。
- 新表优先匹配当前运行约定：`域前缀_单数语义名`。
- 需要改历史表时，必须满足：
  - 有明确业务收益。
  - 有数据迁移方案。
  - 有回滚方案。
  - 已确认不影响线上依赖、后台 API、管理命令和测试数据。

当前不建议做的重命名：

- 不要直接把 `bot_user` 改成 `telegram_users`。
- 不要直接把 `order_order` 改成 `orders` 或 `product_orders`。
- 不要直接把 `core_site_config` 改成 `configs` 或 `site_configs`。
- 不要直接把 `cloud_asset` 改成 `cloud_assets`。

如果未来必须统一，可按“三步走”：

1. 新模型或兼容层先兼容旧表。
2. 补数据迁移、视图或双写验证。
3. 最终窗口期再切表名并清理兼容层。

## 11. 当前项目统一结论

从现在开始，数据库命名统一按以下标准执行：

- 新运行时表名：`域前缀_单数语义名`。
- 新字段名：小写蛇形。
- 新时间字段：`*_at`。
- 新布尔字段：`is_*` / `has_*` / `can_*`。
- 新约束：`uniq_*`。
- 新索引：`idx_*`。
- 多账户相关表：优先加显式 `CloudAccountConfig` 外键；无法绑定外键时使用 `account_scope` / `account_key`。

## 12. 本次整理范围

本次只做文档校正，让规范与真实 `db_table` 保持一致。

本次不做：

- 强改已有历史表名。
- 重命名生产中已存在的数据库表。
- 修改既有业务枚举值导致兼容风险。
