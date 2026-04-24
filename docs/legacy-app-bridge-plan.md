# 旧 app 桥接方案复盘

## 目标

本文档保留为桥接方案复盘，记录旧 app 如何从运行时退出，以及为什么历史 migration label 仍会在文档中出现。

## 当前结论

### 必须保留的 app label

当前仍必须保留在 `INSTALLED_APPS` 中的旧 app：

- 无

已经完成脱钩、可退出运行时配置的旧 app：

- `monitoring`
- `mall`
- `finance`
- `accounts`

原因不是运行时代码，而是 Django migration graph：

- 历史迁移链仍显式依赖这些 label
- fresh test DB 从零初始化时仍需要加载这些 app 的历史 migrations
- 某些跨 app 的 state-only 迁移仍直接依赖旧 app 节点

### 已经可以彻底退出的旧 app

- `dashboard_api`
  - 已从 `INSTALLED_APPS` 移除
  - 路由已并回 `shop/dashboard_urls.py`
  - 目录本体已删除
- `biz`
  - 已从 `INSTALLED_APPS` 移除
  - 目录本体已删除，测试已迁入 `cloud/tests.py`

## 桥接结论

这份方案对应的桥接实验已经完成：

- `accounts`
- `finance`
- `mall`
- `monitoring`

都已从 `INSTALLED_APPS` 脱钩成功。

因此，“最小桥接层”现在只作为历史设计复盘存在；这些旧目录已经不再需要继续保留在当前工作树中。

仍然保留的只有历史 migration 文件中的旧 app label 记录，这属于迁移历史的一部分，不等于运行时目录仍然存在。

## 当前已完成的压缩

以下旧目录都已从当前工作树删除：

- `accounts/`
- `finance/`
- `mall/`
- `monitoring/`

相关模型、服务、admin、命令与运行时 app 注册职责都已迁入 `bot/`、`orders/`、`cloud/` 与 `shop/`。

## 哪些阻塞是“图问题”，不是“代码问题”

### `accounts`
这条最后的根桥也已完成脱钩实验：

- `bot.0001_initial` 已不再依赖 `accounts.0010`，并补上 `bot_user` 的 fresh DB 建表 `database_operations`
- `orders.0002_move_balanceledger_state_from_accounts` 已不再依赖 `accounts.0011`，并补上 `order_balance_ledger` 的 fresh DB 建表 `database_operations`
- 当前 `accounts` 已可从 `INSTALLED_APPS` 中移除，并通过 `check` / `makemigrations --check --dry-run` / fresh SQLite test DB 验证

### `finance`
这条桥也已完成第一轮脱钩实验：

- `orders.0001_initial` 已不再依赖 `finance.0002`，并补上 `order_recharge` 的 fresh DB 建表 `database_operations`
- `accounts.0011_move_telegramuser_state_to_bot` 已不再依赖 `finance.0003`，改为直接依赖 `orders.0001`
- 当前 `finance` 已可从 `INSTALLED_APPS` 中移除，并通过 `check` / `makemigrations --check --dry-run` / fresh SQLite test DB 验证

### `mall`
这条桥也已完成第一轮脱钩实验：

- `orders.0003_move_product_cart_order_from_mall` 已不再依赖 `mall.0028`，改由 `cloud.0001` 承接依赖
- `orders.0003` 已补足 fresh DB 所需的 `order_product` / `order_cart_item` / `order_order` 建表 `database_operations`
- `cloud.0001` 已补足 fresh DB 所需的云模型建表 `database_operations`
- `accounts.0011` 已不再依赖 `mall`，并通过拆分 `TelegramUser` 删除时机消除了与 `orders.0002/0003/0004` 的循环依赖
- 当前 `mall` 已可从 `INSTALLED_APPS` 中移除，并通过 `check` / `makemigrations --check --dry-run` / fresh SQLite test DB 验证

### `monitoring`
这条桥已经完成第一轮脱钩实验：

- `accounts.0011_move_telegramuser_state_to_bot` 对 `monitoring.0003` 的依赖已改为依赖 `cloud.0002`
- `cloud.0002` 已补足 fresh DB 所需的监控表建表 `database_operations`
- 当前 `monitoring` 已可从 `INSTALLED_APPS` 中移除，并通过 `check` / `makemigrations --check --dry-run` / fresh SQLite test DB 验证

这说明：`monitoring` 已不再是必须保留的运行时 app label，后续只剩历史提交意义上的旧目录存在价值。

## 当前复盘结论

桥接目标已经完成：

- 旧 app 已全部退出运行时配置
- 旧目录本体已从工作树删除
- fresh DB 构建责任已迁到 `bot` / `orders` / `cloud` 新域 migrations
- 历史 migration label 仍会留在复盘与迁移链说明中，但不代表这些目录仍然存在

## 一句话结论

这些旧 app 现在只剩“历史迁移链记录”的意义，不再承担任何运行时职责，也不再需要以目录形式保留在仓库中。
