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
- `biz/`：统一业务模型与业务服务聚合层
- `ARCHITECTURE.md`：目录分层与后续迁移规划
- `bot/handlers.py`：机器人交互入口，当前直接调用 `biz.services`
- `monitoring/cache.py`：地址监控缓存
- `tron/scanner.py`：TRON 转账扫描与支付匹配
- `tron/resource_checker.py`：TRON 资源巡检（能量 / 带宽）
- `core/`：站点配置、公共缓存、公共格式化工具

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
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `ALIBABA_CLOUD_ACCESS_KEY_ID`
- `ALIBABA_CLOUD_ACCESS_KEY_SECRET`
- `DEFAULT_SERVER_IMAGE`

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
- `✨ 订阅`
- `🛠 定制`
- `🔎 查询`
- `👤 个人中心`

### 定制流程
- 点击 `🛠 定制`
- 先选择地区
- 再查看该地区可选套餐价格表
- 选择套餐后生成云服务器订单
- 用户按指定金额付款后，系统自动监控到账并进入创建流程
- 已实测 AWS 测试实例可完成 BBR 初始化与 MTProxy 自动安装，并成功生成代理链接
- MTProxy 安装完成后会尝试提取 secret，并向用户发送 `tg://proxy` 与 `https://t.me/proxy` 链接
- AWS Lightsail 真实接入时必须申请并绑定固定公网 IP
- 当前已拆分为 `monitor.py`、`recharge.py`、`custom.py`
- `bot/fsm.py` 统一管理 Redis FSM、Memory 回退、TTL、连接复用与关闭清理
- BBR 完成后继续安装 MTProxy
- MTProxy 使用默认目录 `/home/mtproxy`
- 付款后支持选择 `使用默认端口 9528` 或 `输入自定义端口`
- 初始化阶段会按订单端口放行对应 `tcp/udp`
- 当前 BBR 初始化支持 SSH 密码登录执行
- 已预留 `AWS 光帆服务器 / 阿里云轻量云` 创建接口与 AK/SK 配置项
- 默认镜像按 `debian` 处理，AWS 登录方式按密码登录设计

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
- `tron/cache.py` 兼容壳已删除
- 缓存职责现固定为：`core/cache.py` + `monitoring/cache.py`

## 常见维护项
- 修改机器人 Token：更新 `.env` 或站点配置
- 修改收款地址 / TRON API Key：更新 `configs`
- 新增模型字段后执行：`python manage.py migrate`

## 后续可扩展方向
- 资源提醒频率改为后台可配置
- 资源详情保留更长历史
- 每日统计增加后台报表视图
