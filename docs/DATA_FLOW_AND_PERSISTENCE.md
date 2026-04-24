# 数据流与数据库落库说明

## 目标
- 盘点项目中所有“产生数据”和“获取数据”的主要入口
- 区分哪些数据已经落数据库、哪些仅存在缓存/会话/前端本地
- 给出统一原则：**业务数据全部以数据库为准，缓存只做加速，会话只做临时状态，前端本地只做界面偏好**
- 给出后续改造时的保存方式规范，避免再出现“数据只在 Redis / localStorage 里，数据库没有”的情况

## 一、当前数据来源与去向总览

### 1. Django 业务数据库（主数据源）
当前绝大多数核心业务数据已经落在 Django ORM 对应的数据表：

- 用户与余额
  - `bot.TelegramUser`
  - `orders.BalanceLedger`
- 商品与订单
  - `orders.Product`
  - `orders.Order`
  - `orders.CartItem`
- 云服务器业务
  - `cloud.CloudServerPlan`
  - `cloud.ServerPrice`
  - `cloud.CloudServerOrder`
  - `cloud.CloudAsset`
  - `cloud.Server`
- 充值与财务
  - `orders.Recharge`
- 地址监控
  - `cloud.AddressMonitor`
  - `cloud.DailyAddressStat`
  - `cloud.ResourceSnapshot`
- 系统配置与云账号
  - `core.SiteConfig`
  - `core.CloudAccountConfig`

这些模型当前真实定义位于：
- `bot/models.py:1`
- `orders/models.py:1`
- `cloud/models.py:1`
- `core/models.py:1`

### 2. Redis（缓存 / FSM / 临时统计）
Redis 当前承担的是“加速层”和“临时状态层”，不是最终业务真相来源：

- 系统配置缓存：`core/cache.py:64`
- 每日收入/支出临时统计：`core/cache.py:108`
- 地址监控缓存：`cloud/cache.py:1`
- Bot FSM 状态：`bot/fsm.py:1`

结论：
- Redis 里的数据**不应视为唯一保存位置**
- Redis 失效后，业务应可从数据库恢复
- Redis 里保留的内容应满足“可重建、可过期、可丢失”

### 3. 前端本地存储（仅前端状态）
`vue-vben-admin` 当前使用 `pinia-plugin-persistedstate`，默认会把一部分前端状态写到浏览器本地：
- `packages/stores/src/setup.ts:43`
- `packages/stores/src/setup.ts:55`
- `packages/stores/src/setup.ts:57`
- `packages/stores/src/modules/tabbar.ts:616`

这里保存的是：
- 登录 token / 权限状态
- 用户界面偏好
- 标签页状态
- 部分时区/展示偏好

结论：
- 这类数据属于“前端体验数据”，不是业务主数据
- 不应该把订单、资产、用户配置、监控规则等核心业务数据只存在前端本地

### 4. 外部数据源
项目还会从外部系统读取数据，再决定是否入库：

- TRONGrid / 链上接口
  - `tron/scanner.py:363`
  - `tron/scanner.py:492`
  - `tron/resource_checker.py:74`
- AWS Lightsail / boto3
  - `cloud/services.py:1`
  - `cloud/lifecycle.py:19`
  - `mall/management/commands/sync_aws_assets.py:23`
- 阿里云资产同步
  - 见 README 中 `sync_aliyun_assets` 说明：`README.md:74`

这类数据的原则应是：
- 外部接口返回值是“采集源”
- 采集后需要映射、清洗、再写入数据库业务表
- 不应只在内存中短暂使用后丢弃，尤其是和订单、资产、资源状态相关的数据

## 二、当前“获取数据”的主要位置

### 1. Dashboard API 读取数据库
后台前后端分离接口现在按域拆分，主要由以下模块直接读 ORM：
- `bot/api.py`：站点配置、云账户、工作台、用户、商品
- `orders/api.py`：商品订单、充值记录
- `cloud/api.py`：云服务器订单、云资产、服务器、云套餐、监控地址

结论：后台 API 的读取主链路已经以数据库为主，真实实现现在集中在 `bot/api.py`、`orders/api.py`、`cloud/api.py`。

### 2. Bot 读取数据库与缓存
机器人侧会混合读取：
- 直接查 ORM（用户、订单、套餐）
- 读 Redis FSM（会话步骤）
- 读监控缓存（监控地址）
- 读自定义套餐内存缓存

特点：
- 业务实体仍以数据库为主
- 交互态和高频监控辅助数据放在缓存

### 3. 链上扫描器读取外部接口 + 数据库
- 从数据库读取待支付订单、充值、云订单：`tron/scanner.py:149`、`tron/scanner.py:157`、`tron/scanner.py:165`
- 调 TRONGrid 查询交易与资源
- 命中后回写数据库状态

这是典型“数据库待处理队列 + 外部采集 + 数据库回写”的模式。

## 三、当前“产生数据 / 保存数据”的主要位置

### 1. 后台 API 写数据库
后台 API 仍是核心写入面，但真实实现已按域拆开：

- `bot/api.py`
  - 站点配置初始化与更新
  - 云账户创建与更新
  - 用户余额与折扣调整
  - 商品创建与更新
- `orders/api.py`
  - 充值状态流转
- `cloud/api.py`
  - 云资产更新
  - 服务器删除 / 状态处理
  - 云订单状态流转
  - 云套餐创建与更新

结论：后台 API 仍是核心写入面，真实写入逻辑现在集中在 `bot/api.py`、`orders/api.py`、`cloud/api.py`。

### 2. 机器人交互写数据库
机器人会在用户操作时创建或更新业务数据，例如：
- 添加监控地址后同步写缓存：`bot/handlers.py:537`
- 更新监控阈值：`bot/handlers.py:560`、`bot/handlers.py:580`
- 更新监控开关：`bot/handlers.py:1734`
- 删除监控缓存：`bot/handlers.py:1770`

这里要特别注意：
- `bot/handlers.py` 里的缓存更新只是**数据库写入后的同步动作**才是正确模式
- 如果未来发现某些监控开关/阈值只改了缓存没改数据库，就必须补数据库落库

### 3. 链上扫描器写数据库
`tron/scanner.py` 会在链上支付命中后写回：
- 商品订单状态：`tron/scanner.py:174`
- 商品库存：`tron/scanner.py:184`
- 充值状态与用户余额：`tron/scanner.py:194`、`tron/scanner.py:201`
- 云订单状态与续费信息：`tron/scanner.py:223`

`tron/resource_checker.py` 会更新监控资源数据：
- `AddressMonitor.objects.filter(id=monitor_id).update(...)`：`tron/resource_checker.py:53`

### 4. 云服务器创建 / 生命周期写数据库
- 创建成功回写订单：`cloud/provisioning.py:271`
- 创建/更新云资产：`cloud/provisioning.py:279`、`cloud/provisioning.py:304`
- 生命周期状态推进：`cloud/lifecycle.py:47`、`cloud/lifecycle.py:59`、`cloud/lifecycle.py:71`、`cloud/lifecycle.py:101`、`cloud/lifecycle.py:188`
- 同步服务器镜像表：`cloud/lifecycle.py:51`、`cloud/lifecycle.py:52`、`cloud/lifecycle.py:63`、`cloud/lifecycle.py:64`

### 5. 管理命令写数据库
- 手工录入/修正统一云资产：`mall/management/commands/upsert_cloud_asset.py:77`
- 同步 AWS 资产：`mall/management/commands/sync_aws_assets.py:99`
- 同步服务器镜像表：`mall/management/commands/sync_aws_assets.py:120`

## 四、哪些地方还不是“全量入库”

### 1. Redis 每日统计目前不是数据库持久化
当前每日统计放在 Redis：
- `core/cache.py:108`
- README 也明确说明了 Redis 临时统计：`README.md:132`

问题：
- Redis 过期或重建后会丢失历史
- 无法做可靠报表、趋势分析、审计复盘

建议：
- 新增数据库表，例如 `DailyAddressStat` 或 `MonitorStatSnapshot`
- 以 `日期 + 地址 + 币种 + 用户` 做唯一约束
- 每次 `bump_daily_stats(...)` 时，除了 Redis 递增，也同步数据库累加

建议字段：
- `user`
- `address`
- `currency`
- `stats_date`
- `income`
- `expense`
- `profit`（可冗余也可计算）
- `created_at`
- `updated_at`

### 2. Bot FSM 会话状态没有数据库持久化
当前 FSM 状态保存在 Redis / Memory：
- `bot/fsm.py:1`

问题：
- 机器人会话步骤（例如用户进行到哪一步）服务重启后可能丢失
- 不利于运营排查“用户卡在哪一步”

建议：
- 如果只是临时对话步骤，可以继续保留在 Redis，不强制入库
- 如果希望做客服追踪或恢复会话，新增 `BotConversationState` 表，周期性或关键步骤落库

建议字段：
- `tg_user_id`
- `scene`
- `state`
- `payload_json`
- `updated_at`
- `expired_at`

### 3. 前端本地存储不应保存业务真相数据
当前前端本地存储主要是 UI 状态，这本身没问题；但后续要坚持：
- 不在 `localStorage/sessionStorage` 保存订单草稿最终版本
- 不在前端本地保存资产主数据
- 不在前端本地保存监控规则最终版本

所有这类数据都必须：
- 通过接口提交
- 在 Django 数据库中创建/更新
- 前端仅缓存展示结果

### 4. 外部接口原始返回值未形成统一采集日志
目前 AWS / TRONGrid / 阿里云 调用后，主要是直接驱动业务更新，但缺少统一“采集记录表”。

建议新增采集审计表，例如 `ExternalSyncLog`：
- `source`：`trongrid` / `aws_lightsail` / `aliyun`
- `action`：`query_tx` / `sync_instances` / `query_resources`
- `target`：tx_hash / instance_id / address
- `request_payload`
- `response_payload`
- `is_success`
- `error_message`
- `created_at`

这样可用于：
- 排查同步为何失败
- 核对外部接口与本地数据库差异
- 保留真实采集证据

## 五、统一保存方式规范（后续开发必须遵守）

### 规范 1：业务主数据只认数据库
以下数据必须直接落库，并以数据库为最终真相：
- 用户
- 余额
- 余额流水
- 商品
- 订单
- 充值
- 云套餐
- 云订单
- 云资产
- 服务器
- 监控地址
- 系统配置
- 云账号配置

### 规范 2：缓存必须可重建
以下数据只允许做缓存，不作为唯一保存位置：
- 配置缓存
- 地址监控缓存
- 每日统计热点值
- 前端用户界面偏好缓存
- 套餐内存缓存

要求：
- Redis 失效后可从数据库恢复
- 缓存 key 过期不会造成业务主数据丢失

### 规范 3：所有“写缓存”的地方必须先写数据库
正确顺序必须是：
1. 校验请求
2. 写数据库
3. 提交事务
4. 同步更新 Redis / 前端缓存

禁止顺序：
- 只写 Redis 不写数据库
- 先改 Redis，数据库失败后造成双写不一致

### 规范 4：外部采集数据要分层保存
对接 TRON / AWS / 阿里云时，建议分三层：
1. 原始采集数据：采集日志表
2. 标准化业务数据：订单 / 资产 / 资源快照表
3. 高速缓存：Redis

### 规范 5：敏感信息必须加密后入库
当前已经有正确做法：
- `SiteConfig` 敏感值加密：`core/models.py:22`
- `CloudAccountConfig` 密钥加密：`core/models.py:92`

后续新增以下字段时也应走同类策略：
- 云主机密码
- 第三方 API Token
- SSH 私钥
- 机器人凭据

## 六、推荐新增的数据库持久化补全项

> 说明：以下新增表设计默认按“多账户预留”处理。凡是未来可能按平台主账户、用户子账户、云厂商账户、第三方 API 账户区分的数据，优先保留 `account_scope`、`account_key` 或显式外键，避免后续二次拆表。

### 优先级 P0：必须补
1. 地址每日统计持久化表
   - 解决 Redis 丢统计的问题
2. 外部同步日志表
   - 解决 AWS/TRON/阿里云采集不可审计的问题

### 优先级 P1：建议补
3. Bot 会话关键步骤表
   - 解决用户流程卡点不可追踪的问题
4. 资源变化历史表
   - 解决当前只保留最新地址资源状态，没有完整历史的问题

### 优先级 P2：按需补
5. 前端操作审计表
   - 记录后台谁修改了什么
6. 异步任务执行表
   - 记录同步、创建、续费、删机任务轨迹

## 七、建议的数据表设计草案

### 1. `cloud.DailyAddressStat`
建议用途：持久化每日监控统计

已按多账户预留落地：
- `account_scope`：区分平台账户 / 用户账户 / 云账户
- `account_key`：保存具体账户标识，便于后续一个用户绑定多个收款地址或多个云账号

建议字段：
- `user` -> `TelegramUser`
- `address`
- `currency`
- `stats_date`
- `income`
- `expense`
- `profit`
- `created_at`
- `updated_at`

唯一约束：
- `unique_together = ('user', 'address', 'currency', 'stats_date')`

写入方式：
- 在 `core/cache.py:bump_daily_stats(...)` 对 Redis 递增后，再调用 ORM 做 `update_or_create`
- 或者抽成 service，由扫描器统一同时写 Redis + DB

### 2. `core.ExternalSyncLog`
建议用途：记录外部接口采集日志

已按多账户预留落地：
- `account` -> `CloudAccountConfig`
- 后续可让 AWS / 阿里云 / TRONGrid 分别绑定不同账户配置

建议字段：
- `source`
- `action`
- `target`
- `request_payload`
- `response_payload`
- `is_success`
- `error_message`
- `created_at`

写入方式：
- 在 `tron/scanner.py`
- `tron/resource_checker.py`
- `mall/management/commands/sync_aws_assets.py`
- `cloud/lifecycle.py`
- `bot/api.py`、`orders/api.py`、`cloud/api.py` 以及相关 service 中调用第三方接口的位置
  统一补日志写入

### 3. `bot.BotConversationState`
建议用途：关键机器人流程追踪

建议字段：
- `tg_user_id`
- `scene`
- `state`
- `payload_json`
- `is_active`
- `updated_at`
- `expired_at`

写入方式：
- 在进入关键 FSM 状态时写库
- 在完成/取消流程时关闭或清空

### 4. `cloud.ResourceSnapshot`
建议用途：保留资源变化历史

已按多账户预留落地：
- `account_scope`
- `account_key`

建议字段：
- `monitor`
- `energy`
- `bandwidth`
- `delta_energy`
- `delta_bandwidth`
- `captured_at`

写入方式：
- 在 `tron/resource_checker.py` 检测到资源变化后写入

## 八、推荐实施顺序

### 第一步：先补文档与统一规则
- 本文档落地
- 所有新增需求先判断“数据库表是哪张”
- 禁止直接把业务值只写 Redis / localStorage

### 第二步：补最关键的数据库表
- `DailyAddressStat`
- `ExternalSyncLog`

### 第三步：把读写入口改成统一 service
建议把分散在：
- `bot/api.py`
- `orders/api.py`
- `cloud/api.py`
- `tron/scanner.py`
- `tron/resource_checker.py`
- `bot/handlers.py`
- `cloud/provisioning.py`
- `cloud/lifecycle.py`
中的写操作，逐步收敛到 service 层，避免多处重复双写逻辑。

### 第四步：把缓存改成“数据库派生缓存”
- 先写库
- 再刷新缓存
- 缓存 miss 时自动回源数据库

## 九、一句话结论
当前项目的**核心业务数据其实大部分已经在数据库里**，问题主要不在“完全没入库”，而在这几类还不够完整：
- 每日统计仍主要在 Redis
- Bot 会话状态仍主要在 Redis / Memory
- 外部接口采集缺少统一日志落库
- 前端本地存储需要继续限制在 UI 偏好层

最终统一要求应是：
- **业务数据全部保存数据库**
- **Redis 只做缓存和临时态**
- **localStorage 只做界面偏好**
- **所有保存动作都先写数据库，再更新缓存**
