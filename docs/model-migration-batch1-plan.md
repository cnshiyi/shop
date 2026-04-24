# 第一批模型迁移方案：`bot.TelegramUser` + `orders.Recharge`

## 目标

先拿最小闭环开刀，验证“真实模型定义迁入新域 + 状态迁移不动表”的可行路径。

本批只处理：

- `accounts.TelegramUser` → `bot.TelegramUser`
- `finance.Recharge` → `orders.Recharge`

暂不在这一批处理：

- `accounts.BalanceLedger`
- `mall.*`
- `monitoring.*`
- `INSTALLED_APPS` 删除旧 app

## 为什么先做这组

### 1. 依赖面相对最小

- `TelegramUser` 是 bot 域根模型
- `Recharge` 是 orders 域里最独立的一张表
- 两者都已经使用目标表名：
  - `bot_user`
  - `order_recharge`

### 2. 业务路径清晰

- `bot/services.py` 已接管用户同步逻辑
- `orders/services.py` 已接管充值逻辑
- API / handler 已基本不依赖旧 `dashboard_api.views`

### 3. 可以先验证“app label 迁移”方法论

这批成功后，后面再迁：

- `BalanceLedger`
- `Product` / `CartItem` / `Order`
- `CloudServerPlan` / `CloudServerOrder` / `CloudAsset`
- `AddressMonitor` 等监控模型

## 当前阻塞点

### 代码层外键/字符串引用

当前仍有这些旧引用：

- `accounts.TelegramUser`
  - `accounts/models.py`
  - `finance/models.py`
  - `mall/models.py`
  - `monitoring/models.py`
- migration 历史中也大量写死 `accounts.telegramuser`

### 应用配置

当前 `shop/settings.py` 中：

- 已有 `bot`
- 还没有 `orders`
- 还没有 `cloud`
- 旧 app 仍在：`accounts` / `finance` / `mall` / `monitoring`

## 建议施工顺序

### 步骤 1：在新域建立真实模型定义

目标文件：

- `bot/models.py`
- `orders/models.py`

要求：

- 复制当前真实字段定义
- 保持 `Meta.db_table` 不变
- 先不删除旧模型文件中的类定义
- 新旧模型短期不能同时以同名实体完整注册，避免 Django 冲突

### 步骤 2：先处理运行时代码中的引用

先把代码中的运行时引用逐步改成：

- `bot.TelegramUser`
- `orders.Recharge`

优先改：

- 新域服务层
- API 层
- handlers / commands
- 旧模型里的字符串外键（只改当前 models，不动历史 migrations）

### 步骤 3：设计 `SeparateDatabaseAndState` 迁移

目标不是改表，而是改 Django state：

- 把 `TelegramUser` 的 state 从 `accounts` 切到 `bot`
- 把 `Recharge` 的 state 从 `finance` 切到 `orders`

原则：

- 数据库表名不变
- 不做 rename table
- 只迁 Django state / app label

### 步骤 4：验证 `makemigrations --check --dry-run`

这一步必须确认：

- Django 不再把它们识别成“新增一张表 + 删除旧表”
- state 迁移不会引发整串联动模型误判

### 步骤 5：再评估是否收掉旧模型壳

只有当下面都成立，才考虑把旧文件进一步清空：

- 运行时引用已切到新域
- state 迁移已稳定
- `manage.py check` 通过
- `makemigrations --check --dry-run` 无新增

## 暂不做的事

本批不做：

- 删除 `accounts` / `finance` app
- 修改历史 migration 文件
- 迁 `BalanceLedger`
- 迁 `mall.*` 云资源模型
- 迁 `monitoring.*`
- 修改 contenttypes / auth permission 数据

## 风险提示

### 1. 同名模型双注册

如果直接把真实类复制到新 app，又保留旧 app 注册，Django 会出现模型冲突或 state 异常。

### 2. 外键字符串联动

哪怕只迁 `TelegramUser`，`mall` / `monitoring` / `accounts.BalanceLedger` / `finance.Recharge` 都会被牵动。

### 3. `INSTALLED_APPS` 时机不能抢跑

在 `orders` / `cloud` 还没真正接管模型前，不要先删旧 app。

## 本批完成标志

满足以下条件才算第一批模型迁移完成：

- `bot.TelegramUser` 成为真实模型定义来源
- `orders.Recharge` 成为真实模型定义来源
- 旧引用已切到新域或兼容壳
- `manage.py check` 通过
- `manage.py makemigrations --check --dry-run` 无新增
- 未发生表级 destructive change
