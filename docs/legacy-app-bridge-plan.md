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

- `accounts`
- `finance`
- `mall`
- `monitoring`

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
它是上游根节点：

- `finance.0001_initial` 依赖 `accounts.0008_telegramuser_cloud_reminder_muted_until`
- `mall` 多条早期迁移依赖 `accounts` 旧节点
- `monitoring.0001_initial` 依赖 `accounts.0008_telegramuser_cloud_reminder_muted_until`

### `finance`
它是 `accounts` 与 `orders` 的桥：

- `accounts.0011_move_telegramuser_state_to_bot` 依赖 `finance.0003_move_recharge_state_to_orders`
- `orders.0001_initial` 依赖 `finance.0002_alter_recharge_table`

### `mall`
它仍是关键桥：

- `accounts.0011_move_telegramuser_state_to_bot` 依赖 `mall.0028_switch_user_fk_to_bot`
- `orders.0003_move_product_cart_order_from_mall` 依赖 `mall.0028_switch_user_fk_to_bot`
- 已实测：直接移除 `mall` 会报 `NodeNotFoundError`

### `monitoring`
它仍是关键桥：

- `accounts.0011_move_telegramuser_state_to_bot` 依赖 `monitoring.0003_switch_user_fk_to_bot`
- `monitoring.0004_remove_dailyaddressstat_monitor_and_more` 仍在旧迁移链中承担 state 清理职责

更重要的是：`monitoring` 的历史迁移还承担了相关监控表的**数据库创建与改表链**。如果直接移除 app，不只是依赖节点丢失，fresh DB 的建表流程也会断。

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
