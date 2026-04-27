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
```

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
uv run python -m py_compile bot/api.py bot/handlers.py cloud/services.py cloud/bootstrap.py cloud/api.py
```

云同步相关：

```bash
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
uv run python manage.py sync_aws_assets --region ap-southeast-1
uv run python manage.py sync_aliyun_assets --region cn-hongkong
```

如果用户反馈“没生效”，优先检查：

```bash
ps -axo pid,lstart,command | grep -E 'run.py all|bot.runner|manage.py runserver' | grep -v grep
```

很多 Bot/生命周期修改必须重启 `/Users/aaaa/Desktop/shop/run.py all` 或 `bot.runner` 才生效。

## 开发方向

### P0：稳定性

- 继续把 Bot 回调日志做成“入口、用户、按钮、数据源、生成按钮、结果”全链路可追踪
- 云资产同步日志保留摘要和关键状态变更，避免逐条噪音
- 生命周期真实删机、换 IP、重装要继续保持失败快返回，不阻塞 Bot 主流程

### P1：云资产模型收口

- 继续弱化 `Server` 旧表存在感，避免新功能再依赖它作为主数据源
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
