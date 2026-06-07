# Shop 后端开发说明与方向

## 项目定位

`shop` 是 Telegram Bot 商城、云代理资产管理、TRON 充值/监控和后台 API 的后端仓库。

当前运行时已经收口到五个核心域：

- `shop/`：Django 配置、URL 汇总、启动入口
- `core/`：公共配置、加密、云账号、外部同步日志
- `bot/`：Telegram Bot、后台认证 API、用户/消息/操作日志
- `orders/`：商品、充值、余额、订单、链上支付扫描
- `cloud/`：云套餐、云订单、云资产、生命周期、AWS/阿里云同步与 SSH 初始化

旧 `accounts/finance/mall/monitoring/dashboard_api/biz` 运行时职责已迁出，不要再恢复旧 app。

## 本地启动

```bash
uv run python manage.py check
uv run python run.py all
```

也可拆开运行：

```bash
uv run python run.py web
uv run python run.py bot
uv run python run.py worker
```

`run.py all` 会同时拉起 web、bot 和云资产同步 worker。只运行 `run.py web` 时，后台“代理同步”只会创建 `CloudAssetSyncJob` 队列记录，不会真正执行同步；本地调试同步链路时需要同时运行 `run.py worker` 或直接使用 `run.py all`。

云资产同步 worker 常用环境变量：

- `SHOP_CLOUD_SYNC_WORKER_ENABLED=0`：`run.py all` 不启动 worker
- `SHOP_CLOUD_SYNC_WORKER_POLL_SECONDS=2`：无任务时轮询间隔
- `SHOP_CLOUD_SYNC_WORKER_STALE_MINUTES=90`：运行中超过该分钟数的任务重新入队；`0` 表示关闭恢复

同步状态更新必须保持可观测：

- `CloudAssetSyncJob.status`：`queued` → `running` → `succeeded` / `partial` / `failed` / `cancelled`
- `progress_current` / `progress_total`：按账号或选中资产任务推进
- `current_task`：记录 worker 领取、任务生成和最近完成的 provider/account
- `errors` / `warnings` / `logs`：写入可展示摘要，前端“同步任务”抽屉直接读取这些字段
- `worker_id` / `worker_heartbeat_at`：记录 worker 归属和最近心跳，卡住任务按 heartbeat 超时接管
- `CloudAssetSyncJobEvent`：记录 queued、claimed、status、task、progress、log、warning、error、cancel、retry、heartbeat 事件；事件表只存 `job_id` 数字索引，不加外键，避免日志写入阻塞同步主状态
- 同步成功后触发 `cloud_asset_dashboard_snapshot` 刷新；选中资产同步只增量刷新对应资产，全量同步刷新完整快照

常用启动脚本：

```bash
./scripts-start-all.sh
./scripts-start-web.sh
```

这些脚本使用自身所在目录启动，不再硬编码本机绝对路径。

## 本地敏感材料

项目默认把可迁移的本地敏感材料放在仓库工作目录内：

```text
.shop-secrets/
  lightsail/          # AWS Lightsail 私钥 / 自动推导出的 .pub
  aliyun-keypairs/    # 阿里云临时 keypair
  ssh/                # 通用 SSH 兜底私钥 / 公钥
```

`.shop-secrets/` 已加入 `.gitignore`，迁移项目时需要手动一起复制，但不能提交进 Git。

AWS 私钥/公钥规则：

- 私钥会扫描 `.shop-secrets/lightsail/` 和 `.shop-secrets/ssh/` 下的 `*.pem`、`*.key`、`id_*`
- 如果只有 `.pem` 私钥，会自动通过 `ssh-keygen -y` 推导对应 `.pub`
- SSH 初始化采用“私钥 × 用户”双层轮询，用户顺序通常为 `admin/debian/ubuntu/root`
- 日志会打印候选数量、当前 key 路径和 user，但不打印完整密钥内容

仍保留环境变量显式覆盖：

- `AWS_LIGHTSAIL_PRIVATE_KEY_PATH`
- `AWS_LIGHTSAIL_PRIVATE_KEY_DIR`
- `AWS_LIGHTSAIL_PUBLIC_KEY`
- `AWS_LIGHTSAIL_PUBLIC_KEY_PATH`

## 后台认证

当前后台登录目标态：

- 用户名 + 密码
- Google Authenticator TOTP
- 前端滑动验证
- Django session 2 小时有效期

注意事项：

- `dashboard_totp_secret` 是敏感配置，写入 `SiteConfig` 时加密
- TOTP 绑定必须走“生成二维码 → 扫码 → 输入动态码 → 保存”流程
- 不要恢复 GitHub OAuth 登录
- 曾经误改过现有 `admin` 密码，后续部署/交接时要确认 `admin` 密码已经重新设置

## 云资产核心规则

### 单一代理列表来源

Telegram Bot 的 `🖥 代理列表` 只以 `CloudAsset` 为数据源。

- 列表不再从 `Server` 或 `CloudServerOrder` 派生代理项
- `CloudServerOrder` 只作为续费、换 IP、升级等操作上下文
- 如果资产没有订单，但 `CloudAsset.user_id` 绑定到当前用户，也视为用户有操作权
- 这种情况下会自动创建/复用一条“资产操作订单”，再进入原续费/换 IP/升级链路

### 人工字段优先

`CloudAsset.user` 和 `CloudAsset.actual_expires_at` 是资产自己的人工管理字段。

- AWS/阿里云同步不能覆盖已有资产绑定用户
- AWS/阿里云同步不能覆盖已有资产到期时间
- 即使 `CloudAsset.order_id` 非空，也不能再用订单用户/订单到期反写资产
- 后台人工修改资产用户/到期后，同步脚本必须保留

### 云上真相

同步层以云上真实状态为准：

- 云上存在 → 本地恢复/更新运行态
- 云上不存在 → 本地标记 deleted/terminated，并从后台列表过滤
- 不使用“删除墓碑”阻止云资源复活

## 生命周期与重装

- 无订单资产按 `CloudAsset.actual_expires_at` 进入生命周期
- AWS 无订单到期资产需要真实删机，但只在安全时间窗执行
- Bot 重装链路：用户发送主代理链接 → 解析 IP/端口/密钥 → 可登录时探测服务器实际 MTProxy → 对比核心 32 位密钥 → 确认重装
- 密钥日志只显示前后片段和长度，不打印完整代理密钥
- 如果服务器 SSH 只允许 `publickey`，密码等待会快速失败，不再卡 900 秒

## 常用验证命令

后端通用：

```bash
uv run python manage.py check
uv run python -m py_compile bot/api.py bot/handlers.py cloud/services.py cloud/bootstrap.py cloud/api_assets.py cloud/api_asset_edit.py
```

云同步相关：

```bash
uv run python -m py_compile cloud/sync_jobs.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/process_cloud_asset_sync_jobs.py cloud/management/commands/prune_cloud_sync_job_events.py
uv run python manage.py sync_aws_assets --region ap-southeast-1
uv run python manage.py sync_aliyun_assets --region cn-hongkong
uv run python manage.py process_cloud_asset_sync_jobs --once
uv run python manage.py prune_cloud_sync_job_events --days 90 --keep-per-job 500 --dry-run
```

云后台 API 已经拆成域模块，旧 `cloud/api.py` 聚合层已删除：

- `cloud/api_assets.py`：代理列表、风险摘要和资产载荷辅助。
- `cloud/api_asset_snapshots.py`：代理列表快照刷新、快照搜索、风险计数、分页和分组。
- `cloud/api_asset_edit.py`：代理详情、人工编辑、自动续费开关和后台删除；删除会清理同资源残留记录，未附加固定 IP 刷新会同步相关记录到期时间。
- `cloud/api_orders.py`：云订单列表、详情、状态更新、订单删除保护。
- `cloud/api_tasks.py`：旧任务列表、通知计划、自动续费详情与手动执行。
- `cloud/api_monitors.py`：监控地址和 IP 日志 API。
- `cloud/api_sync.py`：单条代理状态同步、服务器同步、套餐/价格同步。
- `cloud/sync_jobs.py`：负责同步任务入队、worker 执行、任务事件、任务取消/重试、同步状态、同步任务指标 API。
- `shop/admin_urls.py` 直接导入上述域模块，运行时代码和测试替换目标都应指向真实域模块。
- 批量同步任务按账号/选中资产串行执行，不再在线程池里并发写任务状态；每个子任务完成后检查取消请求，保证状态推进和事件顺序可读。
- `CloudAssetSyncJobEvent` 事件表通过 `job_id` 标量索引关联任务，不加外键；生产环境用 `prune_cloud_sync_job_events` 定期清理。
- 后台业务 API 统一走 `/api/admin/` 前缀，例如 `GET /api/admin/cloud-assets/sync-jobs/metrics/`，前端代理列表抽屉和同步任务详情页读取这份指标。

后台任务中心：

- `GET /api/admin/tasks/center/` 由 `cloud/task_center.py` 提供统一任务摘要。
- 当前聚合范围包括云资产同步、云订单任务、生命周期计划、通知计划和自动续费计划。
- `/admin/tasks` 前端页优先读取任务中心 API；如果 API 不可用，回退到旧的 `/admin/tasks/` 任务列表。
- `cloud/api_monitors.py` 接管监控地址和 IP 日志 API。

如果用户反馈“没生效”，优先检查：

```bash
ps -axo pid,lstart,command | grep -E 'run.py all|bot.runner|manage.py runserver|process_cloud_asset_sync_jobs' | grep -v grep
```

很多 Bot/生命周期/云同步修改必须重启 `run.py all`、`bot.runner` 或 `process_cloud_asset_sync_jobs` 才生效。

## 开发方向

### P0：稳定性

- 继续把 Bot 回调日志做成“入口、用户、按钮、数据源、生成按钮、结果”全链路可追踪
- 云资产同步日志保留摘要和关键状态变更，避免逐条噪音
- 生命周期真实删机、换 IP、重装要继续保持失败快返回，不阻塞 Bot 主流程

### P1：云资产模型收口

- 继续清理历史文档里对旧 `Server` 入口的残留叙述，避免新功能误以为它仍是运行时入口
- Bot 代理详情、后台代理详情、同步脚本都以 `CloudAsset` 为主
- 订单只表达购买/续费/迁移/升级业务，不表达资产人工绑定真相

### P2：后台管理体验

- 代理列表继续补充可解释字段：云账号、区域、到期、绑定用户、来源、最近同步状态
- 关键危险操作要有一次确认和明确返回值
- 敏感字段统一脱敏展示，空保存必须保留旧值

### P3：迁移与部署

- 项目迁移时带上 `.shop-secrets/` 和 `.env`
- 后续可补一键诊断命令，检查私钥数量、公钥数量、AWS 账号、CloudAsset 绑定和 Bot 运行状态
- 生产部署时不要使用 Django development server，当前 `run.py` 主要服务本地/PyCharm 调试
