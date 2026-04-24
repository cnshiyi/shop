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
- `core/`：站点配置、公共缓存、公共格式化工具
- `bot/`：Telegram 用户模型、认证/用户/配置 API、机器人交互
- `orders/`：充值、余额流水、商品、购物车、订单与交易服务
- `cloud/`：云套餐、价格模板、云订单、云资产、服务器、监控模型与缓存/服务
- `tron/scanner.py`：TRON 转账扫描与支付匹配
- `tron/resource_checker.py`：TRON 资源巡检（能量 / 带宽）
- `ARCHITECTURE.md`：当前收口架构与后续拆旧计划
- `docs/DATA_FLOW_AND_PERSISTENCE.md`：数据产生/获取入口盘点与数据库落库规范
- `docs/DB_NAMING_CONVENTIONS.md`：数据库对象命名统一规范

### 旧目录说明
以下目录仍在仓库中，但当前目标已经降级为兼容/迁移壳，不再作为真实业务归属：
- `accounts/`
- `finance/`
- `mall/`
- `monitoring/`
- `biz/services/`

补充说明：后台聚合路由已并回 `shop/dashboard_urls.py`。

## 启动方式

### 当前本地开发建议
- 一键同时启动后台和机器人：运行 `python run.py`
- 也可以显式运行：`python run.py all`
- 只调后台接口：运行 `python run.py web`
- 只跑机器人：运行 `python run.py bot`
- PyCharm 可直接运行内置配置 `Shop All`

### 1. 安装依赖
使用项目虚拟环境安装依赖。

## 数据流与持久化说明
- 已新增 `docs/DATA_FLOW_AND_PERSISTENCE.md`，用于盘点项目中所有主要数据来源、读取入口、写入入口与统一落库规范
- 当前原则明确为：业务主数据以 MySQL/Django ORM 为准；Redis 仅用于缓存、FSM 和临时统计；前端 `localStorage/sessionStorage` 仅用于界面偏好与会话态
- 后续新增功能时，所有订单、资产、监控规则、配置变更都应先写数据库，再同步缓存
- 已补充数据库持久化基础：`cloud.DailyAddressStat`、`cloud.ResourceSnapshot`、`core.ExternalSyncLog`
- 新表已按“多账户预留”设计，支持通过 `account_scope` / `account_key` 和 `CloudAccountConfig` 关联扩展到平台账户、用户账户、云账户、第三方接口账户

## 数据库命名规范
- 已新增 `docs/DB_NAMING_CONVENTIONS.md`，统一约定后续数据库表、字段、约束、索引与多账户扩展字段的命名格式
- 当前策略是：新表严格统一命名，历史表保持兼容，不为纯命名美化直接改线上表名

## Django Admin 视觉风格
- 当前后台已调整为更接近运营大盘 / 仪表盘的布局
- 顶部使用浅色头部栏，桌面端显示深色左侧导航栏，移动端支持按钮展开导航
- 首页工作台分为总览横幅、待处理提醒、核心指标、快捷入口、说明面板几个区域
- 列表页、筛选器、分页、表单、按钮统一为白色卡片 + 圆角 + 轻阴影风格
- 首页与导航已做响应式适配，可随屏幕宽度自动切换布局

## 方案 B：独立后台前端
- 后端接口仍由当前仓库提供：`/api/admin/` 与 `/api/dashboard/`
- 真实前端仓库位于 `C:\Users\Administrator\Desktop\vue-vben-admin`
- 当前实际使用前端位于 `C:\Users\Administrator\Desktop\vue-vben-admin\apps\web-antd`
- 当前仓库内的 `dashboard_web/` 仅保留说明文档，不承载真实前端源码
- 如需修改后台页面、菜单、文案、代理与交互，请直接在前端仓库修改

## 统一云资产
- 当前由 `cloud.CloudAsset` 统一记录云服务器与 `MTProxy` 资产
- 同一张表支持：资产类型、来源、实例 ID、IP、`MTProxy` 链接、真实到期时间、绑定用户、绑定订单，且允许留空
- 云服务器开通成功后，会自动把服务器和 `MTProxy` 信息写入统一资产表
- 后台已提供 `CloudAsset` 管理入口，可手工维护 AWS 资产和代理信息

## 同步与手工录入命令
- 阿里云自动同步：`python manage.py sync_aliyun_assets --region cn-hongkong`
- AWS / 代理手工录入：`python manage.py upsert_cloud_asset --kind server --instance-id xxx --asset-name xxx --public-ip x.x.x.x --actual-expires-at 2026-05-15T00:00:00+08:00`
- 手工录入 `MTProxy`：`python manage.py upsert_cloud_asset --kind mtproxy --asset-name proxy-1 --public-ip x.x.x.x --mtproxy-port 9528 --mtproxy-link "tg://proxy?..." --actual-expires-at 2026-05-15T00:00:00+08:00`

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

### 5. PyCharm 一键运行
- 打开项目后，右上角运行配置选择 `Shop All`
- 直接点击绿色运行按钮即可
- 该配置会执行 `run.py all`

如果只想启动 Web：
```bash
python run.py web
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
- Django Admin 首页已新增第二排运营统计看板
- 默认有效期 31 天；到期未续费保留 3 天，再关机 3 天后删机，删机后 IP 继续保留 10 天
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
- 缓存职责现固定为：`core/cache.py` + `cloud/cache.py`

## 配置与敏感信息管理
- 支持在 Django Admin 的 `系统配置` 中维护：`bot_token`、`m_account_token`、`receive_address`、`trongrid_api_key`、`redis_url`、`database_url`、`mysql_host`、`mysql_port`、`mysql_user`、`mysql_password`、`mysql_database`、`admin_password_notice`
- 敏感配置会通过 `core.crypto` 加密后写入数据库，后台列表页显示脱敏值
- 支持在 Django Admin 的 `云账户配置` 中维护多个 `AWS / 阿里云` 账户，`access_key / secret_key` 同样加密存库
- 如本地 MySQL 账号无建库权限，可设置 `DJANGO_TEST_REUSE_DB=1` 让 Django 测试复用当前库；也可通过 `MYSQL_TEST_DATABASE` 指定已有可用测试库名


## 后续可扩展方向
- 资源提醒频率改为后台可配置
- 资源详情保留更长历史
- 每日统计增加后台报表视图
