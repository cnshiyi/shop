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

### 2. API 实现仍主要在 `dashboard_api.views`

虽然 URL 入口已经开始切到：

- `bot.api`
- `orders.api`
- `cloud.api`

但这些模块目前仍主要转发到 `dashboard_api.views` 里的实现。

### 3. 服务实现仍有兼容壳

当前：

- `orders.services` 仍大量转发 `biz.services.*`
- `cloud.services` 仍大量转发 `biz.services.*`
- `bot.services` 仍转发 `biz.services.users`

所以 `biz` 还不能下线。

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

- [ ] `bot.api` / `orders.api` / `cloud.api` 仍转发 `dashboard_api.views`
- [ ] 新域 `models.py` 仍不是实体模型定义
- [ ] `biz.services.*` 仍是主实现承载层

## 建议下一步

优先把 `dashboard_api.views` 按领域继续拆薄，直到它只剩少量共享辅助函数或直接退为兼容层。
