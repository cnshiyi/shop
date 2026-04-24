# 数据库命名规范

## 目标
- 统一项目数据库对象命名格式
- 保持历史表兼容，不因为纯命名整理破坏现有数据与迁移链
- 后续新增表、字段、索引、约束全部按统一规则执行

## 总原则
- 历史表：**保持现状，不做无收益硬改名**
- 新增表：**一律使用小写蛇形命名（snake_case）**
- 约束/索引：**名称显式、可读、统一前缀**
- 外键字段：**统一使用 `<target>_id` 列语义**（Django 默认即可）
- 枚举值：**统一使用小写蛇形或固定小写短词**

## 1. 表名规则
统一格式：
- 小写
- 蛇形
- 复数名词

推荐示例：
- `telegram_users`
- `telegram_usernames`
- `balance_ledgers`
- `cloud_server_orders`
- `external_sync_logs`
- `daily_address_stats`
- `resource_snapshots`

当前项目中已符合规则的多数表：
- `balance_ledgers`
- `telegram_usernames`
- `recharges`
- `products`
- `cart_items`
- `cloud_server_plans`
- `cloud_server_orders`
- `cloud_assets`
- `address_monitors`
- `daily_address_stats`
- `resource_snapshots`
- `cloud_account_configs`
- `external_sync_logs`

当前项目中历史遗留但暂不建议强改的表：
- `users`
- `orders`
- `servers`
- `configs`

原因：
- 已在线使用
- 已有 migration 历史
- 可能已有后台、命令、测试和生产数据依赖
- 仅为了名字更“整齐”而改表名，风险高于收益

## 2. 模型名规则
统一格式：
- Python 类名使用 PascalCase
- 语义尽量单数

示例：
- `TelegramUser`
- `BalanceLedger`
- `CloudServerOrder`
- `ExternalSyncLog`

## 3. 字段名规则
统一格式：
- 小写蛇形
- 避免缩写不清
- 时间字段统一以 `_at` 结尾
- 布尔字段统一以 `is_` / `has_` / `can_` 开头

推荐示例：
- `created_at`
- `updated_at`
- `completed_at`
- `service_expires_at`
- `is_active`
- `is_sensitive`
- `provider_resource_id`

## 4. 外键字段规则
统一格式：
- 模型层使用语义化字段名：`user`、`order`、`monitor`、`account`
- 数据库列默认落为：`user_id`、`order_id`、`monitor_id`、`account_id`

不要使用：
- `uid`
- `oid`
- `cid`
- `ref1` / `ref2`

## 5. 唯一约束与索引命名规则
统一格式：
- 唯一约束：`uniq_<table>_<semantic>`
- 普通索引：`idx_<table>_<semantic>`
- 外键索引：默认 Django 即可，特殊场景再显式补

示例：
- `uniq_daily_address_stat_scope`
- `uniq_telegram_username_per_user`
- `idx_cloud_assets_public_ip`

## 6. 枚举值命名规则
统一格式：
- 小写
- 单词间下划线连接
- 不混中英文
- 不混大小写

示例：
- `pending`
- `completed`
- `renew_pending`
- `aws_lightsail`
- `aliyun_simple`
- `manual_adjust`

## 7. 多账户预留规则
所有未来可能按账户拆分的数据表，优先至少具备以下一种方式：

### 方案 A：显式外键
适用于真正有“账户主表”的情况：
- `account = ForeignKey(CloudAccountConfig, ...)`

### 方案 B：作用域 + 标识
适用于暂时没有统一账户主表、但未来需要扩展的场景：
- `account_scope`
- `account_key`

当前已按此规范处理：
- `cloud.DailyAddressStat`
- `cloud.ResourceSnapshot`
- `core.ExternalSyncLog`

## 8. 迁移策略
### 原则
- 不为“纯好看”大规模重命名历史表
- 只对新表、新字段执行严格规范
- 需要改历史表时，必须满足：
  - 有明确业务收益
  - 有数据迁移方案
  - 有回滚方案
  - 已确认不影响线上依赖

### 当前建议
- 现在不要直接把 `users` 改成 `telegram_users`
- 不要直接把 `orders` 改成 `product_orders`
- 不要直接把 `configs` 改成 `site_configs`

如果未来必须统一，可按“三步走”：
1. 新模型兼容旧表
2. 补视图/兼容层/数据迁移
3. 最终窗口期再切表名

## 9. 当前项目统一结论
从现在开始，数据库命名统一按以下标准执行：
- 新表名：小写蛇形复数
- 新字段名：小写蛇形
- 新时间字段：`*_at`
- 新布尔字段：`is_*` / `has_*`
- 新约束：`uniq_*`
- 新索引：`idx_*`
- 多账户相关表：优先加 `account_scope` / `account_key` 或显式 `account`

## 10. 本次整理范围
本次只做以下两类事情：
- 补充命名规范文档
- 保证新增加的表和约束遵守统一格式

本次**不做**：
- 强改已有历史表名
- 重命名生产中已存在的数据库表
- 修改既有业务枚举值导致兼容风险
