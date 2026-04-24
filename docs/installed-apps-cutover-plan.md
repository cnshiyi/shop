# `INSTALLED_APPS` 切换计划

## 当前状态

当前运行配置仍保留旧应用：

- `biz`
- `accounts`
- `mall`
- `finance`
- `monitoring`
- `dashboard_api`

同时已经补齐新域过渡入口：

- `bot/models.py` / `bot/services.py` / `bot/api.py`
- `orders/models.py` / `orders/services.py` / `orders/api.py` / `orders/ledger.py`
- `cloud/models.py` / `cloud/services.py` / `cloud/api.py` / `cloud/cache.py`

## 为什么现在还不能直接删旧 app

### 1. 模型定义仍在旧 app

当前 `bot/orders/cloud` 里的 `models.py` 还是导出壳，真实 Django 模型类仍定义在：

- `accounts/models.py`
- `finance/models.py`
- `mall/models.py`
- `monitoring/models.py`

这意味着：

- `app_label` 仍是旧 app
- 迁移历史仍挂在旧 app 下
- 管理命令、外键字符串、历史 migration 仍依赖旧 app 名称

### 2. `dashboard_api` 已基本退为兼容层

当前情况已经明显前进：

- `dashboard_api/views.py` 已清空为兼容模块
- `dashboard_api/urls.py` 已直接路由到 `bot.api` / `orders.api` / `cloud.api`

结论：`dashboard_api` 现在已不是主要阻塞点，后续更多是目录/应用级别收尾，而不是继续搬运行时逻辑。

### 3. `biz.services` 已大幅退为兼容壳

当前情况：

- `biz/services/cloud_servers.py` 已是兼容壳
- `biz/services/payments.py` 已是兼容壳
- `biz/services/monitoring.py` 已是兼容壳
- `biz/services/rates.py` 已是兼容壳
- `biz/services/commerce.py` 已是兼容壳
- `biz/services/users.py` 已是兼容壳
- `biz/services/__init__.py` 已改成惰性导出，避免循环导入

结论：`biz` 仍未到可删除阶段，但它也已经不是“主实现承载层”，主要剩余压力集中在模型定义与 app label。

## 建议切换顺序

### 阶段 1：保留旧 app，继续收实现

目标：让新域模块不再只是导出壳。

- 把 `dashboard_api.views` 中的 bot 相关实现迁到 `bot/api.py`
- 把订单/充值相关实现迁到 `orders/api.py`
- 把云资源/监控/套餐相关实现迁到 `cloud/api.py`
- 把 `biz.services.*` 逐步搬到 `orders/`、`cloud/`、`bot/`

### 阶段 2：移动模型定义

目标：让真实 Django 模型定义迁入新域 app。

建议方式：

1. 先在新 app 中创建真实模型类
2. 显式指定 `Meta.db_table` 保持现有表名不变
3. 用 `SeparateDatabaseAndState` 或受控迁移处理 app label/state 迁移
4. 保证历史 migration 可追溯

风险点：

- 外键字符串引用（如 `'accounts.TelegramUser'`）
- 历史 migration 对 app label 的依赖
- admin / contenttypes / auth 权限数据

### 阶段 3：缩减 `INSTALLED_APPS`

只有在下面条件满足后才建议动：

- `bot/orders/cloud` 中已有真实模型定义
- `dashboard_api` 仅剩薄路由或已并入新域
- `biz` 仅剩空壳或已无运行时依赖
- `accounts/finance/mall/monitoring` 已无真实模型职责

建议顺序：

1. 先让 `dashboard_api` 变为纯路由壳
2. 再让 `biz` 变为纯兼容壳
3. 最后评估是否移除旧业务 app

## 当前已完成前置条件

- [x] 目标表名已全部迁移完成
- [x] `TelegramUsername` 已从 Django 状态下线
- [x] `dashboard_api/urls.py` 已开始路由到 `bot/orders/cloud` API 入口
- [x] `orders/ledger.py` 已接管余额流水记账入口

## 当前主要阻塞点

- [x] `bot.api` / `orders.api` / `cloud.api` 已脱离 `dashboard_api.views` 主实现
- [x] 第一批真实模型已落地：`bot.TelegramUser`、`orders.Recharge`
- [x] 第二批最小块已落地：`orders.BalanceLedger`
- [x] 当前 models 中所有 `user -> accounts.TelegramUser` 已切到 `bot.TelegramUser`
- [ ] `mall.Product` / `mall.Order` / `mall.CartItem` 仍未迁入 `orders.models`
- [ ] `mall.CloudServerPlan` / `mall.CloudServerOrder` / `mall.CloudAsset` / `mall.Server` / `mall.CloudIpLog` 仍未迁入 `cloud.models`
- [ ] `monitoring.*` 真实模型仍在旧 app
- [ ] 历史 migrations 仍全面依赖旧 app label，不能粗暴替换

## 已确认的模型切换阻塞清单

### 旧 app 仍在 `INSTALLED_APPS`

当前仍保留：

- `biz`
- `accounts`
- `mall`
- `finance`
- `monitoring`
- `dashboard_api`

当前仅新增但未接管模型 app 的是：

- `bot`

### 真实模型仍定义在旧 app

- `accounts/models.py`
  - `TelegramUser`
  - `BalanceLedger`
- `finance/models.py`
  - `Recharge`
- `mall/models.py`
  - `Product`
  - `CartItem`
  - `CloudServerPlan`
  - `ServerPrice`
  - `CloudServerOrder`
  - `CloudAsset`
  - `Server`
  - `CloudIpLog`
- `monitoring/models.py`
  - `AddressMonitor`
  - `DailyAddressStat`
  - `ResourceSnapshot`

### 代码级外键字符串热点

当前代码里仍可见的旧引用主要是：

- `accounts.TelegramUser`
- `mall.Product`
- `mall.CloudServerPlan`
- `mall.CloudServerOrder`
- `monitoring.AddressMonitor`

这些旧字符串如果不先迁完，直接切 app 会导致 state / relation / contenttypes 一起炸。

## 建议下一步

优先开始“模型定义迁移草案”而不是继续清 API：

1. 先选最小闭环模型组，例如 `bot.TelegramUser` + `orders.Recharge`
2. 在新域建立真实模型类，保持 `db_table` 不变
3. 先把代码中的外键字符串改成新域引用或直接类引用
4. 再设计 `SeparateDatabaseAndState` 级别的 app-label 迁移

换句话说：下一阶段不是继续搬接口，而是正式进入模型迁移施工。 
