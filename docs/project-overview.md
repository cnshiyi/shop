# Shop 后端项目说明

## 1. 项目定位

这是一个 Django 5.2 后端仓库，当前运行时收口到五个核心域：

- `shop/`：项目配置、URL 汇总、启动入口
- `core/`：公共配置、加密、云账号、运行时配置、外部同步日志
- `bot/`：Telegram 用户、登录账号、后台认证、消息/操作日志、机器人交互
- `orders/`：商品、购物车、订单、充值、余额流水、TRON 支付扫描
- `cloud/`：云套餐、价格、订单、资产、服务器、生命周期、同步与启动编排

真实前端不在本仓库，前端代码在 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd`。

## 2. 本地启动

后端默认使用 MySQL。当前本地已通过 OrbStack 虚拟机提供数据库，`run.py` 会先执行迁移再启动服务。

常用命令：

```bash
uv sync
uv run python manage.py check
uv run python run.py web
uv run python run.py bot
uv run python run.py worker
uv run python run.py all
```

本地验证时可临时切到 SQLite：

```bash
DB_ENGINE=sqlite SQLITE_NAME=local.sqlite3 uv run python run.py web
```

## 3. 关键启动链

- `run.py`：统一启动器，负责 `web` / `bot` / `worker` / `all`
- `shop/settings.py`：数据库、会话、日志、敏感配置、默认 hosts
- `shop/urls.py`：总路由
- `shop/dashboard_urls.py`：后台 API 聚合路由

`run.py web` 会先执行：

- `manage.py migrate`
- `manage.py ensure_dashboard_admin`
- `manage.py runserver 127.0.0.1:8000`

`run.py worker` 会先执行迁移，然后启动 `process_cloud_asset_sync_jobs` 持久化同步 worker。

`run.py all` 会同时拉起 web、bot 和云资产同步 worker，并对 bot / worker 做 keepalive 重启。后台“代理同步”接口只负责创建 `CloudAssetSyncJob` 队列记录；如果只运行 `run.py web`，同步任务会停留在 queued，必须另外运行 `run.py worker`。

云资产同步状态是持久化业务状态：

- `queued`：后台 API 已入队，等待 worker 领取
- `running`：worker 已领取并执行，持续更新 `progress_current`、`progress_total`、`current_task`
- `succeeded` / `partial` / `failed`：终态，写入 `errors`、`warnings`、`logs`、`result_payload`
- 同步成功后刷新代理列表快照；选中资产同步走增量快照刷新，全账号同步刷新完整快照

## 4. 重要实现清单

### core/

- `core/models.py`
  - `SiteConfig`：系统配置，支持普通值和敏感值加密缓存
  - `CloudAccountConfig`：云账号配置，`access_key` / `secret_key` 加密存储
  - `ExternalSyncLog`：外部同步日志
- `core/runtime_config.py`
  - 从 `SiteConfig` 读取运行时参数
  - 提供云资产同步、删机、通知等时间窗配置
- `core/crypto.py`
  - 加解密工具
- `core/cloud_accounts.py`
  - 选择可用云账号
  - 按 provider / region / 负载做账号分配
- `core/trongrid.py`
  - 处理 TRONGrid API key 和请求头
- `core/order_numbers.py`
  - 生成唯一订单号
- `core/cache.py`
  - Redis / 缓存相关封装
- `core/texts.py`
  - 站点文案配置初始化与读取
- `core/views.py`
  - 站点首页入口

### bot/

- `bot/models.py`
  - `TelegramLoginAccount`：Telegram 登录账号，会话串、验证码哈希加密
  - `TelegramUser`：用户余额、折扣、静默设置、用户名集合
  - `BotOperationLog`：消息/回调操作日志
  - `TelegramChatArchive`：归档会话
  - `TelegramGroupFilter`：群组转发/推送开关
  - `AdminReplyLink`：管理员回复链路映射
  - `TelegramChatMessage`：聊天消息记录
- `bot/api.py`
  - 后台认证：登录、退出、刷新、TOTP 绑定
  - 用户/余额/折扣/配置管理
  - Telegram 登录、消息发送、群组归档、产品管理
  - 云账号、管理员、站点配置、按钮配置等后台接口
- `bot/handlers.py`
  - Bot 消息、回调、交互处理
- `bot/keyboards.py`
  - Telegram 按钮布局
- `bot/services.py`
  - 机器人侧业务服务
- `bot/runner.py`
  - bot 进程入口
- `bot/telegram_listener.py`
  - 个人号监听与转发
- `bot/telegram_sender.py`
  - 通知号消息发送
- `bot/fsm.py`
  - Redis / 内存 FSM 存储

### orders/

- `orders/models.py`
  - `Product`：商品
  - `CartItem`：购物车项，支持商品和云套餐
  - `BalanceLedger`：余额流水
  - `Recharge`：充值单
  - `Order`：普通商品订单
- `orders/services.py`
  - 订单/余额/充值相关服务
- `orders/payment_scanner.py`
  - 链上支付扫描与入账匹配
- `orders/tron_parser.py`
  - TRON 转账解析
- `orders/ledger.py`
  - 余额流水封装
- `orders/runtime.py`
  - 订单运行态辅助
- `orders/api.py`
  - 充值、订单列表、订单详情、状态更新等后台接口

### cloud/

- `cloud/models.py`
  - `CloudServerPlan`：云套餐
  - `ServerPrice`：价格模板
  - `CloudServerOrder`：云服务器订单，包含到期、续费、删机、IP 回收、自动续费等时间线
  - `CloudAsset`：云资产主表，`kind='server'` 是服务器资产唯一事实记录
  - `Server`：非 Django 模型兼容门面，仅用于旧 import/脚本过渡，不能再作为运行时主入口
  - `CloudIpLog`：IP 变更与生命周期日志，关联订单与资产，不再关联 `Server`
  - `CloudLifecyclePlanNote`：生命周期计划备注
  - `CloudLifecyclePlan`：生命周期执行计划
  - `CloudNoticePlan`：通知计划
  - `CloudAutoRenewPlan`：自动续费计划
  - `CloudAssetDashboardSnapshot`：代理列表查询快照，支撑后台分页、搜索和风险统计
  - `CloudAssetSyncJob`：后台代理同步任务队列，记录状态、进度、结果、日志和重试来源
  - `DailyAddressStat` / `ResourceSnapshot` / `AddressMonitor` 等监控相关表
- `cloud/services.py`
  - 云资产/订单/生命周期/通知的业务编排
- `cloud/provisioning.py`
  - 云服务器创建、重装、绑定、进度标记
- `cloud/bootstrap.py`
  - SSH / BBR / MTProxy / 主机初始化脚本与探测
- `cloud/aliyun_simple.py`
  - 阿里云实例创建与同步
- `cloud/aws_lightsail.py`
  - AWS Lightsail 同步与操作
- `cloud/lifecycle.py`
  - 生命周期计划生成与执行
- `cloud/resource_monitor.py`
  - 资源巡检
- `cloud/sync_safety.py`
  - 缺失确认、二次确认、防误删保护
- `cloud/api.py`
  - 云资产、云订单、云套餐、价格、监控、通知、自动续费等后台接口
- `cloud/management/commands/`
  - `sync_aws_assets`
  - `sync_aliyun_assets`
  - `reconcile_cloud_assets_from_servers`
  - `refresh_lifecycle_plans`
  - `refresh_notice_plans`
  - `dedupe_servers`
  - `dedupe_cloud_assets`
  - `audit_cloud_asset_ip_presence`
  - `upsert_cloud_asset`
  - `refresh_cloud_asset_dashboard_snapshots`
  - `process_cloud_asset_sync_jobs`

## 5. 路由面

`shop/dashboard_urls.py` 是后台 API 主聚合点，覆盖：

- 认证与会话
- 用户、余额、折扣
- Telegram 账号、登录、群组、消息
- 商品、订单、充值
- 云资产、云订单、云套餐、价格、服务器
- 云资产同步任务列表、详情、重试和状态轮询
- 生命周期、通知、自动续费、监控
- 站点配置、按钮配置、云账号、管理员账号

`shop/urls.py` 还保留了：

- `/api/admin/`
- `/api/dashboard/`
- `/api/`
- 前台首页入口

## 6. 数据与规则

- 敏感配置使用加密存储，尤其是 `SiteConfig`、云账号、Telegram 会话串、验证码哈希。
- 云资产主数据以 `CloudAsset` 为准，不应再把 `Server` 当主入口。
- `cloud_server` 表已经拆除，历史服务器数据迁入 `cloud_asset`。
- `CloudServerOrder` 只表达购买、续费、迁移、自动续费、删机等业务上下文。
- 订单和充值的余额变化要保持幂等。
- 生命周期操作要快失败，不要阻塞 bot 主流程。
- 历史 migration 可能仍保留旧 app label，这是迁移链历史，不是当前运行时结构。

## 7. 验证

已验证：

```bash
uv run python manage.py check
uv run python run.py web
```

当前 web 已可在本地启动，访问地址为：

`http://127.0.0.1:8000`

## 8. 运维备注

- 本地数据库默认走 `127.0.0.1:3306`
- 当前环境通过 OrbStack 虚拟机提供 MariaDB/MySQL
- 修改 bot、生命周期、云同步后，通常需要重启 `run.py all` 或相关进程才会生效
