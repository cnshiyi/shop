# 第一批模型迁移方案复盘：`bot.TelegramUser` + `orders.Recharge`

## 结果

这一批已经完成，并且验证了当前整套“模型迁入新域 + state-only 迁移不动表”的方法论是可行的。

本批实际完成：

- `accounts.TelegramUser` → `bot.TelegramUser`
- `finance.Recharge` → `orders.Recharge`

并且在后续连续扩批中，已经进一步完成：

- `accounts.BalanceLedger` → `orders.BalanceLedger`
- `mall.Product` / `mall.CartItem` / `mall.Order` → `orders.*`
- `mall.CloudServerPlan` / `mall.ServerPrice` / `mall.CloudServerOrder` / `mall.CloudAsset` / `mall.Server` / `mall.CloudIpLog` → `cloud.*`
- `monitoring.AddressMonitor` / `monitoring.DailyAddressStat` / `monitoring.ResourceSnapshot` → `cloud.*`

## 这批验证了什么

### 1. 真实模型可以迁入新域而不改表

验证结论：

- 保持 `Meta.db_table` 不变
- 通过 `SeparateDatabaseAndState` 前移 Django state
- 不做额外 rename table

这条路径已经在多批模型上被反复验证通过。

### 2. 旧模型文件不能保留“导入别名”式双注册

迁移 `mall.Product` / `CartItem` / `Order` 时已经踩过坑：

- 只要旧 `models.py` 还导入这些类
- Django 仍可能把它们注册成旧 app 模型
- 最终表现为双注册与 state 异常

结论：旧 app 里不能靠“import alias”伪装兼容；要么清空，要么只保留极薄兼容出口且确保不触发模型注册。

### 3. fresh test DB 是最关键闸门

后续实践已经证明，单看运行时库不够，必须同时过：

- `manage.py migrate`
- `manage.py check`
- `manage.py makemigrations --check --dry-run`
- `DJANGO_TEST_SQLITE=1 manage.py test biz.tests`

只有 fresh test DB 也能完整跑通，才能说明 state-only 迁移链顺序正确。

## 当前落地状态

已完成：

- `bot.TelegramUser` 已成为真实模型定义来源
- `orders.Recharge` 已成为真实模型定义来源
- `accounts.models` / `finance.models` 已删除
- 当前 models 中所有 `user -> accounts.TelegramUser` 已切到 `bot.TelegramUser`
- 已手写并验证 state-only 迁移链，不做表级变更
- 这一批的方法论后来已扩展应用到 `orders` 与 `cloud` 后续所有主模型批次

## 仍然留下的真正问题

第一批本身已经结束，现在剩下的问题不是这批模型怎么迁，而是：

- 剩余旧 app label 如何安全退出 `INSTALLED_APPS`
- `biz` 兼容层何时可以进一步缩减或移除
- 历史 migration 对旧 app 的依赖如何在不破坏 fresh test DB 的前提下处理

## 结论

这份文档现在更适合作为“第一批模型迁移方法论复盘”，而不是待办施工单。

后续如需继续 cutover，重点不再是 `TelegramUser` / `Recharge` 本身，而是围绕：

- `INSTALLED_APPS` 收口
- 旧兼容层删除
- 历史 migration / app label 风险验证

展开。