# 旧 app 最小桥接层方案

## 目标

把 `accounts` / `finance` / `mall` / `monitoring` 从“旧业务 app”降级为**最小迁移桥接层**，只保留 Django 仍然必须依赖的最小结构。

当前重点不是继续迁业务代码，而是回答三个问题：

1. 哪些旧 app label 现在还必须保留
2. 它们最小能缩到什么形态
3. 后续如果还要继续缩，应该动哪一层

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
- `biz`
  - 已从 `INSTALLED_APPS` 移除
  - 当前只剩兼容导入层与测试 patch 路径

## 最小桥接层形态

对 `accounts` / `finance` / `mall` / `monitoring`，当前建议的**最小可行桥接层**为：

- `__init__.py`
- `apps.py`
- `migrations/`

除此之外，其余运行时代码都应删除。

## 为什么现在还不能继续删

### 1. `apps.py` 还不能删

只要旧 app label 还在 `INSTALLED_APPS` 中，Django 就仍需要能够导入并识别该 app。

因此在当前阶段：

- `apps.py` 是最小必要件
- 继续删除 `apps.py` 会直接让 app 无法被 Django 正常识别

### 2. `migrations/` 还不能删

这些旧 app 当前真正的核心职责，已经不是业务实现，而是：

- 提供历史 migration graph 节点
- 支持 fresh test DB 从零构建数据库状态
- 承接 app label 级别的依赖关系

如果删掉 `migrations/`，会直接导致：

- `NodeNotFoundError`
- fresh DB 无法重建历史表/状态链
- 当前 state-only cutover 链条断裂

## 当前已完成的压缩

### `accounts`
已删除：
- `models.py`
- `services.py`
- `admin.py`

当前保留：
- `__init__.py`
- `apps.py`
- `management/commands/`（已继续清理为最小残留）
- `migrations/`

### `finance`
已删除：
- `models.py`
- `admin.py`

当前保留：
- `__init__.py`
- `apps.py`
- `migrations/`

### `mall`
已删除：
- `models.py`
- `admin.py`
- `management/commands/*`（已迁入 `cloud/management/commands/*`）
- `management/` 空包

当前保留：
- `__init__.py`
- `apps.py`
- `migrations/`

### `monitoring`
已删除：
- `models.py`
- `admin.py`
- `cache.py`

当前保留：
- `__init__.py`
- `apps.py`
- `migrations/`

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

## 未来若还想继续缩，有两条路线

### 路线 A：停在最小桥接层

这是当前最稳、也最推荐的方案：

- 保留 `__init__.py + apps.py + migrations/`
- 不再给旧 app 放任何运行时代码
- 让业务继续全部收口在 `bot / orders / cloud / core / shop`

优点：
- 风险最低
- 不改历史迁移链
- fresh DB 仍稳定

缺点：
- 旧 app label 仍存在于 `INSTALLED_APPS`

### 路线 B：继续做 migration graph 脱钩

这是下一阶段真正困难的工作，不再是删文件，而是改迁移图：

可能方向包括：

1. 把跨 app 的 state-only 依赖改写到新域节点
2. 为 fresh DB 增补新的桥接迁移，承接旧 app 迁移结束态
3. 评估是否能让旧 label 迁移历史“封箱”，再让新 app 接管最终可见状态

风险：
- 极易引发 fresh DB 与已有 DB 行为不一致
- 需要反复验证 `migrate` / `makemigrations --check --dry-run` / fresh SQLite test DB
- 可能需要改写部分历史迁移，风险显著高于当前阶段

## 建议顺序

### 现在就做
- 把 `accounts / finance / mall / monitoring` 固化为最小桥接层
- 不再往旧 app 写任何新业务实现

### 下一阶段再做
- 先从 `monitoring` 开始设计 migration graph 脱钩实验
- 再评估 `mall`
- `finance` / `accounts` 最后动

## 一句话结论

当前这 4 个旧 app 已经不再是“旧业务层”，而是“历史 migration label bridge”。

在不重写迁移图的前提下，它们已经基本缩到极限；下一刀如果还要砍，就必须砍 migration graph，而不是继续砍业务代码。
