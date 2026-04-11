# Shop 项目说明

## 项目简介
`shop` 是一个基于 `Django + aiogram + APScheduler + Redis + MySQL` 的电报机器人商城与 TRON 地址监控系统。

当前包含四个核心能力：
- 商品购买与订单管理
- 余额充值与充值记录
- TRON 地址转账监控
- TRON 资源变动监控（能量 / 带宽）

## 技术栈
- Web / Admin: `Django`
- Bot: `aiogram`
- Scheduler: `APScheduler`
- Database: `MySQL`
- Cache / FSM / Daily Stats: `Redis`
- Chain API: `TRONGrid`

## 目录结构
- `run.py`：PyCharm 一键启动入口
- `shop/settings.py`：Django 配置
- `accounts/`：用户账户命名层（逐步替代 `users/` 的对外命名）
- `mall/`：商城业务命名层（逐步替代 `shopbiz/` 的对外命名）
- `finance/`：充值与财务命名层（逐步替代 `payments/` 的对外命名）
- `monitoring/`：监控命名层（逐步替代 `monitors/` 的对外命名）
- `biz/`：统一业务模型聚合层（用户、商品、订单、充值、监控）
- `ARCHITECTURE.md`：目录分层与后续迁移规划
- `bot/`：机器人菜单、回调、业务逻辑
- `core/cache.py`：Redis 连接、配置缓存、每日统计等公共缓存能力
- `monitoring/cache.py`：地址监控缓存
- `tron/scanner.py`：TRON 转账扫描与支付匹配
- `tron/resource_checker.py`：TRON 资源巡检（能量 / 带宽）
- `tron/cache.py`：Redis 缓存、监控地址缓存、每日统计
- `core/`：站点配置
- `users/`：用户模型（旧命名，后续逐步淡出）
- `shopbiz/`：商品与订单（旧命名，后续逐步淡出）
- `payments/`：充值记录（旧命名，后续逐步淡出）
- `monitors/`：地址监控模型（旧命名，后续逐步淡出）

## 启动方式
### 1. 安装依赖
使用项目虚拟环境安装依赖。

### 2. 配置环境变量
主要配置位于 `.env`：
- `BOT_TOKEN`
- `REDIS_URL`
- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DATABASE`
- `SCANNER_VERBOSE`

### 3. 执行迁移
```bash
python manage.py migrate
```

### 4. 启动项目
```bash
python run.py
```

## 机器人能力
### 主菜单
- `🛒 购买商品`
- `👤 个人中心`

### 个人中心
- `📋 我的订单`
- `💰 充值余额`
- `📜 充值记录`
- `🔍 地址监控`

### 地址监控
每个监控地址支持两个开关：
- `监控转账`：收到转入或发生转出时通知
- `监控资源`：可用能量 / 带宽增加时通知，资源消耗不通知

## 通知说明
### 转账通知
- 收入：`🟢 收入提醒`
- 支出：`🔴 支出提醒`
- 包含：地址、金额、时间、手续费、余额、今日收入 / 支出 / 利润
- 带 `查看交易详情` 按钮

### 资源通知
- 资源增加时发送 `⚡ 资源变动提醒`
- 包含：可用能量增加、可用带宽增加、当前资源值
- 带 `查看资源详情` 按钮

## 每日统计
Redis 中维护按天隔离的临时统计：
- 当天转入计入 `income`
- 当天转出计入 `expense`
- `profit = income - expense`
- 允许负数，例如 `-100 USDT`
- key 自动按日期切换，相当于每天 0 点清零

## 日志策略
- 默认压低 `httpx` / `httpcore` / `apscheduler` 日志噪音
- 扫描器每 10 分钟输出一次摘要
- 只有实际命中监控或支付时输出详细信息

## 开发说明
- 监控地址、站点配置、每日统计均优先走 Redis
- Redis 不可用时，部分功能会自动降级到数据库
- `INSTALLED_APPS` 当前已切换到新结构主入口：`accounts / mall / finance / monitoring`
- 旧目录不再作为 Django 主 app 入口加载，后续可继续朝删除旧目录推进

## 常见维护项
- 修改机器人 Token：更新 `.env` 或站点配置
- 修改收款地址 / TRON API Key：更新 `configs`
- 新增模型字段后执行：`python manage.py migrate`

## 后续可扩展方向
- 资源提醒频率改为后台可配置
- 资源详情保留更长历史
- 每日统计增加后台报表视图
