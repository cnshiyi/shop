# Shop 真机测试报告

## 测试范围

本报告记录真实云资源测试，不属于普通单元测试。测试目标覆盖：

- 服务器创建
- 服务器删除
- IP 变更
- 附加 IP / 固定 IP 变更
- 人工创建的无订单资产续费和生命周期变化
- 通知计划和删除计划执行情况

## 安全边界

- 不记录云账号密钥、SSH 密钥、登录密码、代理 secret 或完整代理链接。
- 资源清理必须记录订单 ID、资产 ID、实例名、固定 IP 名称、区域、执行时间和结果；云资源 ID 和公网 IP 需脱敏。
- 真实支付和链上广播不在本次测试范围内。
- 删除服务器和释放固定 IP 前临时打开项目内删除总开关，测试后还原。

## 2026-06-02 测试环境

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 云厂商：AWS Lightsail
- 云账号：后台账号 `#55`，状态 `ok`，区域提示 `ap-southeast-1`
- 套餐：`#131`，新加坡，`nano_3_0`，名称 `实机测试 Nano`
- 本地测试用户：`#172`，`codex_real_machine_test`

## 执行记录

### 服务器创建

- 状态：通过
- 执行时间：2026-06-02 23:35-23:38
- 订单：`#77` / `SRV20260602153507421013`
- 资产：`#323`
- 云实例：`20260602-************-*-o77`
- 区域：`ap-southeast-1`
- 固定 IP 名称：`20260602-************-*-o77-ip`
- 公网 IP：`13.215.xxx.xxx`
- 本地状态：订单 `completed`，资产 `running`
- 到期事实：`CloudAsset.actual_expires_at=2026-07-03T15:38:23.718542+00:00`
- 结果：AWS Lightsail 实例创建、固定 IP 分配绑定、BBR 初始化、MTProxy 主/备用/Telemt/SOCKS5 安装均通过。
- 敏感信息：未写入登录密码、代理 secret 或完整代理链接。

### IP 变更

- 状态：通过
- 来源订单：`#77`
- 来源资产：`#323`
- 执行时间：2026-06-02 23:39-23:41
- 新订单：`#78` / `SRVIP20260602153939972101O77`
- 新资产：`#324`
- 新云实例：`20260602-************-*-o78`
- 新固定 IP 名称：`20260602-************-*-o78-ip`
- 新公网 IP：`13.251.xxx.xxx`
- 新本地状态：订单 `completed`，资产 `running`
- 新资产到期事实：`CloudAsset.actual_expires_at=2026-07-03T15:38:23.718542+00:00`
- 来源订单变化：`ip_change_quota=0`
- 来源订单迁移时间：`migration_due_at=2026-06-07T15:39:39.971719+00:00`
- 来源订单删机时间：`delete_at=2026-06-10T15:39:39.971719+00:00`
- 来源固定 IP 保留到期：`ip_recycle_at=2026-06-25T15:39:39.971719+00:00`
- 来源资产到期事实：`CloudAsset.actual_expires_at=2026-06-07T15:39:39.971719+00:00`
- 结果：新实例真实创建成功，源订单生命周期时间被调整为迁移宽限和后续删除计划。

### 灰色地带续费

- 状态：待执行
- 范围：云端已关机或已删机、本地还未同步期间，用户从代理列表/详情续费。
- 预期：不能误判为普通续费成功；应识别真实云端状态，并转入固定 IP / 无订单资产恢复或阻断提示。

### 人工创建的无订单资产

- 状态：待执行
- 预期：使用真实云端资源对应一条 `CloudAsset(order=None)` 记录，验证代理列表、续费入口、生命周期变化和删除计划。

## 2026-06-03 计划复查

- 状态：未执行真实云资源操作。
- 原因：本轮没有获得用户明确授权真实云资源成本。
- 本轮动作：只读复查真机测试报告、自动优化控制台、TODO 和红线记录；未创建、删除、变更或续费任何真实云资源。
- 安全结论：服务器创建、服务器删除、IP 变更、附加 IP / 固定 IP 变更、人工创建的无订单资产续费、生命周期变化、通知计划和删除计划仍需在获得明确授权后再执行。
- 禁止项确认：本轮未做真实支付、链上广播、生产发布、删除数据或其它不可逆操作。
- 后续执行前置条件：用户需明确授权真实云资源成本，并指定云账号、区域、套餐、测试用户和允许执行的动作范围；执行后继续在本报告记录，云资源 ID、公网 IP 和实例名保持脱敏。

## 2026-06-04 Telegram Bot 真机购买与恢复初始化

- 状态：通过，过程中发现并修复 bot handler 问题。
- 授权：用户已明确授权使用真实 Telegram 账号、项目数据库余额和真实云资源测试。
- 后端仓库：`/Users/a399/Desktop/data/shop`
- bot：`@ceshiayan_bot`
- 登录账号：项目数据库内 `TelegramLoginAccount #1`，状态 `logged_in`
- 测试用户：`TelegramUser #173`
- 云厂商：AWS Lightsail
- 套餐：新加坡，`实机测试 Nano`

### 购买与初始开通

- 订单：`#79` / `SRV20260604084904042580`
- 资产：`#325`
- 云实例：`20260604-************-*-o79`
- 公网 IP：`54.255.xxx.xxx`
- 支付方式：USDT 钱包余额支付
- 金额：5 USDT
- 初始结果：云实例创建并同步为运行中，但第一次 SSH 初始化失败，用户侧订单显示“开通失败”。
- 初始故障：订单详情按钮点击无响应，原因是 async handler 内同步查库触发 `SynchronousOnlyOperation`。

### 恢复初始化

- 操作路径：`📋 我的订单` -> 订单详情 -> `🛠 继续初始化` -> 确认。
- 结果：BBR 初始化、MTProxy 主代理、备用代理、Telemt 链路和 SOCKS5 安装成功。
- 本地状态：订单 `completed`，资产 `running`。
- 到期事实：`CloudAsset.actual_expires_at=2026-07-05T09:53:52.191087+00:00`，后续续费后延长到 `2026-08-05T09:53:52.191087+00:00`。
- 敏感信息：未写入登录密码、代理 secret 或完整代理链接。

### 续费与幂等验证

- 操作路径：`🔎 到期时间查询` -> `IP查询到期` -> 输入脱敏 IP -> `🔄 续费IP` -> `💳 USDT钱包支付`。
- 结果：钱包续费成功，余额扣除 5 USDT，到期时间延长 31 天。
- 幂等验证：重复点击同一续费支付按钮后，余额未再次扣除，用户侧显示“这笔续费已完成”并发送续费后巡检结果。

### Bot 功能点击覆盖

- 已通过：主菜单、购买节点、个人中心、我的订单、订单详情、继续初始化确认、IP 查询、自动续费开/关、续费支付、充值入口、充值记录、余额明细、提醒列表、地址监控添加/列表/详情/删除、联系客服。
- 追加通过：IP 详情页 `🌐 更换IP` 已进入地区选择页并返回；`🛠 重新安装` 已进入确认页并取消；`⚙️ 修改配置` 已实际点击并返回“暂无可修改的配置”；`🔄 续费IP` 已进入续费页并返回，未再次支付。
- 已修复：个人中心文本按钮、客服文本按钮、订单详情、云服务器详情、初始化成功通知、续费成功提示、续费后巡检、IP 查询到期、提醒列表和提醒详情中的 async 同步查库问题。

### 未执行项

- 未执行链上广播或真实地址充值到账。
- 修改配置实际变更未执行，原因是当前资产实际点击后返回“暂无可修改的配置”。

### 追加安全点击复核

- 时间：2026-06-04 18:23-18:28 CST。
- 范围：使用项目数据库中已登录 Telegram 账号，向 bot 发送脱敏资产 IP，实际点击 IP 查询结果中的剩余按钮。
- 结果：`🌐 更换IP` 显示地区选择；`🛠 重新安装` 显示确认页且未确认；`⚙️ 修改配置` 返回暂无可修改配置；`🔄 续费IP` 显示钱包/地址续费入口且未支付。
- 复核：点击后测试用户余额仍为 USDT `990.000000`、TRX `1000.000000`，订单 `#79` 仍为 `completed`，资产 `#325` 仍为 `running`，余额流水仍为 2 条，地址监控仍为 0。
- 敏感信息：未记录完整公网 IP、代理链接、Telegram session、bot token、云账号密钥或登录密码。

### 追加全功能真机复核

- 时间：2026-06-04 18:37-18:59 CST。
- 范围：在用户要求“全部测完”后继续执行剩余真实路径，覆盖 TRX 钱包续费、重新安装最终确认、换 IP 最终确认、旧机删除、旧固定 IP 释放、新旧 IP 查询复核和完整 Django 测试套件。
- TRX 续费：从 IP 详情进入续费页，点击 `💳 TRX钱包支付`，扣除 15.253 TRX，到期时间延长到 `2026-09-05T09:53:52.191087+00:00`。
- 重新安装：从 IP 查询结果点击 `🛠 重新安装` 并确认，bot 返回重试初始化完成，代理链路重新生成。
- 更换 IP：从 IP 查询结果点击 `🌐 更换IP`，选择新加坡，创建新订单 `#80` / 新资产 `#326`，新资产状态为 `running`，用户侧收到“服务器重建完成，固定 IP 已迁移”通知。
- 删除与释放：临时打开 `cloud_server_delete_enabled` 和 `cloud_ip_delete_enabled`，对迁移旧订单 `#79` 执行真实旧机删除，再执行旧固定 IP 释放；执行后立即还原两个开关。旧订单 `#79` 与旧资产 `#325` 均为 `deleted`，旧 IP 查询不可续费。
- 最终状态：测试用户余额为 USDT `990.000000`、TRX `984.747000`；余额流水 3 条；最终可用订单为 `#80`，最终可用资产为 `#326`。
- 验证：`manage.py check`、相关编译检查、`git diff --check` 和 SQLite 完整测试套件 519 个测试均通过。
- 敏感信息：未记录完整公网 IP、代理链接、代理 secret、登录密码、Telegram token、session 或云账号密钥。

## 2026-06-04 重装后旧服务器处理补充

- 状态：通过，且记录一次全局生命周期扫描的真实候选处理。
- 授权：延续用户对真实 Telegram 账号、项目数据库余额和真实云资源测试的授权。
- 范围：重装/重建后旧服务器进入迁移保留期、未到期不删除、到期后删除状态转换。
- 单元回归：11 个旧机迁移和生命周期聚焦测试通过。
- 真实库事务验证：临时旧单进入 `deleting` 保留期后，旧资产为 `deleting/is_active=False`，资产到期事实保持不变；未到迁移时间时指定旧机删除执行器返回清理时间未到；事务回滚后临时订单和资产数量为 0。
- 真实库到期验证：临时旧单迁移时间调到过去后，仅调用指定旧单执行器，并用本地替身阻断云 API；旧单和旧资产均标记 `deleted`，`migration_delete/done` 生成，资产到期事实保持不变；事务回滚后临时订单和资产数量为 0。
- 真实候选处理：曾从全局 `lifecycle_tick` 入口尝试覆盖到期链路，真实库中一个既有普通删机候选被扫描并执行为 `deleted`，关联资产也为 `deleted/is_active=False`，生命周期任务为 `delete/done`。该资源属于既有替换链订单，不记录完整公网 IP、实例名、云资源 ID 或凭据。
- 后续规则：除非明确要处理真实库全部到期候选，生命周期专项验证使用指定订单/资产执行器或只读计划刷新，不直接跑全局 `lifecycle_tick`。
- 敏感信息：未记录完整公网 IP、代理链接、代理 secret、登录密码、Telegram token、session 或云账号密钥。

## 2026-06-04 Telegram Bot 全功能真机重测与测试资源清理

- 状态：通过，过程中发现并修复 1 个重装确认文案问题。
- 授权：延续用户对真实 Telegram 账号、项目数据库余额和真实云资源测试的授权。
- 后端仓库：`/Users/a399/Desktop/data/shop`
- bot：`@ceshiayan_bot`
- 登录账号：项目数据库内 `TelegramLoginAccount #1`，状态 `logged_in`
- 测试用户：`TelegramUser #173`
- 云厂商：AWS Lightsail
- 套餐：新加坡，`实机测试 Nano`

### 启动与登录

- 本轮启动 `run.py bot` 真机进程，bot 轮询成功启动。
- 项目数据库内 Telegram 登录账号可用，使用该账号向 bot 发送 `/start` 并实际点击 inline 按钮。
- 启动时后台调度器也启动了生命周期、TRON 扫块、云同步等任务；TRON 扫块出现若干 429/ReadTimeout 重试日志，但不影响 bot 点击路径。

### 主菜单与个人中心

- `/start`：返回主菜单成功。
- `👤 个人中心`：返回余额、订单、充值、明细、提醒、地址监控菜单成功。
- `🔙 返回主菜单`：返回成功。
- `📋 我的订单`：订单列表成功，筛选 `已支付`、`未付款`、`续费`、`新购` 均可切换。
- `💰 充值余额`：币种选择成功；点击 `USDT` 后进入金额输入提示，未提交真实链上充值。
- `📜 充值记录`：返回空记录提示成功。
- `💳 余额明细`：列表成功，筛选 `收入`、`支出`、`充值`、`消费` 均可切换。
- `🔔 提醒列表`：列表成功。
- `🔍 地址监控`：添加地址提示和监控列表入口成功，未提交真实监控地址。

### 查询与自助动作

- `🔎 到期时间查询`：查询中心成功。
- `🖥 代理列表`：列出当前可用代理成功。
- `⚡ 自动续费查询`：列出自动续费状态成功。
- `🔎 IP查询到期`：输入脱敏测试 IP 后返回到期时间、状态和自助动作按钮成功。
- `⚡ 开启自动续费`：实际开启成功；随后点击 `⛔ 关闭自动续费` 还原成功。
- `👩‍💻 联系客服`：提示发送问题/订单号/截图成功。
- `⚙️ 修改配置`：真实点击后返回“当前状态不允许修改配置”，未创建配置变更订单。
- `🌐 更换IP`：进入地区选择页成功，未继续确认创建新资源。
- `🔄 续费IP`：进入续费页成功；该入口会把当前订单置为待支付续费状态，本轮后续已随测试资源一起清理。

### 新购与开通

- 操作路径：`🛠 购买节点` -> `新加坡` -> `套餐一` -> `数量 1` -> `钱包支付` -> `USDT 钱包支付`。
- 订单：`#90` / `SRV20260604124116893188`
- 资产：`#335`
- 云实例：`20260604-************-*-o90`
- 公网 IP：`13.250.xxx.xxx`
- 支付方式：USDT 钱包余额支付。
- 金额：5 USDT。
- 结果：AWS Lightsail 实例创建成功，固定 IP 分配并绑定成功，SSH 密码登录成功，BBR 初始化成功，MTProxy 主代理、备用代理、Telemt 多端口和 SOCKS5 均安装成功。
- 用户侧通知：bot 发送“云服务器创建完成”通知成功。
- 敏感信息：未记录完整公网 IP、代理链接、代理 secret、登录密码、Telegram token、session 或云账号密钥。

### 重装文案问题与修复

- 发现：从 IP 查询结果点击 `🛠 重新安装` 后，确认页正文仍显示旧文案“确认重新安装/重新安装大约需要 5 分钟/期间代理可能会断连”，但按钮已显示“确认重建迁移”。
- 修复：更新 `core.texts.BOT_TEXTS` 中 `bot_reinstall_confirm`、`bot_reinstall_validate_ok` 和 `bot_reinstall_need_main_link` 默认文案，统一为“重建迁移”语义。
- 回归：重启 bot 后再次点击 `🛠 重新安装`，确认页显示“确认重建迁移？系统会新建服务器并迁移固定 IP，主/备用链接保持不变；旧机保留 3 天后进入删除流程。”，按钮为“确认重建迁移”。

### 测试资源清理

- 清理对象：本轮新购测试订单 `#90`、资产 `#335`、脱敏云实例 `20260604-************-*-o90`、脱敏固定 IP `13.250.xxx.xxx`。
- 清理方式：先将本轮测试订单标记为人工测试清理中，再调用生命周期执行器真实删除 AWS 实例，随后释放该订单固定 IP。
- 删除结果：`delete_ok=True`，实例标识已清空。
- 固定 IP 释放结果：`recycle_ok=True`，固定 IP 名称已清空。
- 最终本地状态：订单 `#90` 为 `deleted`，`public_ip` 为空，`previous_public_ip` 保留脱敏历史 IP；资产 `#335` 为 `deleted/is_active=False`。

### 本轮验证

- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile core/texts.py bot/tests.py bot/handlers.py` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_legacy_custom_port_flow_is_removed bot.tests.RetainedIpRenewalUiTestCase.test_reinstall_cancel_buttons_keep_back_path bot.tests.RetainedIpRenewalUiTestCase.test_reinstall_confirm_handlers_reuse_saved_back_path_after_submit --settings=shop.settings --verbosity=2` 通过。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `git diff --check` 通过。

### 剩余风险

- 本轮执行了真实 AWS Lightsail 创建、实例删除和固定 IP 释放；资源已按测试报告完成清理。
- 本轮没有执行链上广播或真实地址充值到账。
- 续费入口点击会让现有订单进入待支付续费状态；本轮由于测试资源最终删除，未保留该待支付状态。

## 2026-06-04 生命周期开关矩阵真机实测

- 状态：通过。
- 授权：延续用户对真实 Telegram 账号、项目数据库余额和真实云资源测试的授权。
- 后端仓库：`/Users/a399/Desktop/data/shop`
- 测试用户：`TelegramUser #173`
- 云厂商：AWS Lightsail
- 套餐：新加坡，`实机测试 Nano`
- 订单：`#91` / `SRV20260604141229230551`
- 资产：`#337`
- 云实例：`20260604-************-*-o91`
- 公网 IP：`52.76.xxx.xxx`
- 支付方式：USDT 钱包余额支付。
- 金额：5 USDT。

### 新购与开通

- 使用项目服务走真实钱包余额购买流程，订单由 `paid` 进入开通。
- AWS Lightsail 实例创建成功，固定 IP 绑定成功，BBR、MTProxy 主代理、备用代理、Telemt 多端口和 SOCKS5 初始化完成。
- 开通后本地订单为 `completed`，资产为 `running`，资产到期事实写入 `CloudAsset.actual_expires_at`。
- 敏感信息：未记录完整公网 IP、代理链接、代理 secret、登录密码、Telegram token、session 或云账号密钥。

### 关机阶段矩阵

- 非执行时间窗口：`cloud_suspend_time=00:00` 时执行计划关机，返回“当前不在后台配置的服务器关机执行时间窗口”，订单保持 `completed`。
- 关机总开关关闭：`cloud_server_shutdown_enabled=0` 时执行计划关机，返回“服务器关机总开关已关闭，跳过真实关机”，订单保持 `completed`。
- 资产开关关闭：`CloudAsset.shutdown_enabled=False` 时执行计划关机，返回“资产自动生命周期开关已关闭，跳过真实关机”，订单保持 `completed`。
- 真实关机：打开关机总开关、执行窗口和资产开关后，真实 AWS 关机成功；订单变为 `suspended`，资产变为 `stopped/is_active=False`。

### 删机阶段矩阵

- 删机总开关关闭：`cloud_server_delete_enabled=0` 时执行计划删机，返回“删除服务器总开关已关闭，跳过真实删机”，订单保持 `suspended`。
- 非执行时间窗口：`cloud_delete_time=00:00` 时执行计划删机，返回“当前不在后台配置的服务器删除执行时间窗口”，订单保持 `suspended`。
- 资产开关关闭：`CloudAsset.shutdown_enabled=False` 时执行计划删机，返回“资产自动生命周期开关已关闭，跳过真实删机”，订单保持 `suspended`。
- 真实删机：打开删机总开关、执行窗口和资产开关后，真实 AWS 删机成功；订单变为 `deleted`，资产变为 `deleted/is_active=False`，实例标识清空，固定 IP 进入保留待回收状态。

### 固定 IP 回收矩阵

- 删 IP 总开关关闭：`cloud_ip_delete_enabled=0` 时执行固定 IP 回收，返回“删除IP总开关已关闭，跳过真实释放固定 IP”，固定 IP 保留信息仍在。
- 非执行时间窗口：`cloud_unattached_ip_delete_time=00:00` 时执行固定 IP 回收，返回“当前不在后台配置的 IP 删除执行时间窗口”，固定 IP 保留信息仍在。
- 资产开关关闭：`CloudAsset.shutdown_enabled=False` 时执行固定 IP 回收，返回“资产自动生命周期开关已关闭，跳过真实释放固定 IP”，固定 IP 保留信息仍在。
- 真实释放：打开删 IP 总开关、执行窗口和资产开关后，真实 AWS 固定 IP 释放成功；订单仍为 `deleted`，固定 IP 名称、`public_ip` 和 `ip_recycle_at` 已清空，资产保持 `deleted/is_active=False`。

### 缺到期时间资产规则

- 当前真实库没有“未附加固定 IP 且缺到期时间”的现成记录，因此创建两条临时本地资产记录验证规则，随后删除。
- 临时未附加固定 IP：无 `actual_expires_at` 时，生命周期扫描自动补齐约 15 天后的删除时间。
- 临时服务器资产：无 `actual_expires_at` 时，生命周期扫描不自动补时间，保持等待人工维护。
- 清理：两条临时本地资产已删除，最终临时资产残留数量为 0。

### 配置恢复与最终状态

- 已恢复测试前生命周期配置：`cloud_server_shutdown_enabled` 删除为默认值，`cloud_server_delete_enabled=1`，`cloud_ip_delete_enabled=1`，`cloud_suspend_time=15:00`，`cloud_delete_time=15:00`，`cloud_unattached_ip_delete_time=15:00`。
- 最终本地状态：订单 `#91` 为 `deleted`，资产 `#337` 为 `deleted/is_active=False`；实例标识、固定 IP 名称和 IP 回收时间均已清空。
- 最终清理结论：本轮真实 AWS 测试实例已删除，固定 IP 已释放，未发现本轮临时资产残留。

## 2026-06-05 Telegram Bot 与生命周期全流程重试实测

- 状态：通过，过程中发现 Telegram MTProto 默认连接方式超时，改用 `ConnectionTcpAbridged` 后重试成功。
- 授权：延续用户对真实 Telegram 账号、项目数据库余额和真实云资源测试的授权。
- 后端仓库：`/Users/a399/Desktop/data/shop`
- bot：`@ceshiayan_bot`
- 登录账号：项目数据库内 `TelegramLoginAccount #1`，状态 `logged_in`
- 测试用户：`TelegramUser #173`
- 云厂商：AWS Lightsail
- 套餐：新加坡，`实机测试 Nano`

### 启动与连接

- 启动 `run.py bot` 真机进程，bot 轮询最终启动成功。
- 首次使用 Telethon 默认连接访问 Telegram 连续超时，未发送消息、未点击按钮、未改订单。
- 改用 `ConnectionTcpAbridged` 和 `ConnectionTcpObfuscated` 重试后均能连接，实际发送 `/start` 并收到 bot 主菜单。
- 测试结束后已停止本轮 bot 进程；仅保留原本存在的 PyCharm `runserver`。

### 真实购买与开通

- 使用项目数据库余额创建测试订单 `#92` / `SRV20260605081555743902`。
- 支付方式：USDT 钱包余额支付。
- 金额：5 USDT。
- 结果：余额从 980 USDT 扣到 975 USDT；AWS Lightsail 实例创建成功，固定 IP 绑定成功，订单进入 `completed`。
- 资产：`#340`
- 云实例：`20260605-************-*-o92`
- 公网 IP：`54.169.xxx.xxx`
- 敏感信息：未记录完整公网 IP、代理链接、代理 secret、登录密码、Telegram token、session 或云账号密钥。

### Bot 真机点击覆盖

- `/start`：返回主菜单成功，主菜单包含 `购买节点`、`到期时间查询`、`个人中心`。
- `个人中心`：实际点击成功，二级菜单包含 `我的订单`、`充值余额`、`充值记录`、`余额明细`、`提醒列表`、`地址监控`、`返回主菜单`。
- `我的订单`：实际点击成功，订单列表展示测试订单和既有订单。
- `充值余额`：实际点击成功，进入币种选择提示，未提交链上充值。
- `余额明细`：实际点击成功，展示余额支付流水。
- `提醒列表`：实际点击成功，展示云服务器提醒列表。
- `地址监控`：实际点击成功，进入地址监控页。
- `查询中心`：实际点击成功，二级菜单包含 `代理列表`、`自动续费查询`、`IP查询到期`、`返回主菜单`。
- `代理列表`：实际点击成功，展示代理列表。
- `自动续费查询`：实际点击成功，展示自动续费列表。
- `IP查询到期`：实际点击成功，输入脱敏测试 IP 后返回 IP 查询结果。
- IP 详情按钮：实际点击 `开启自动续费`、`关闭自动续费`、`续费IP`、`更换IP`、`重新安装`、`修改配置`。
- 重建迁移：只进入确认页并取消，确认页显示“确认重建迁移”，未确认创建新机。
- 修改配置：返回“当前状态不允许修改配置”。
- 续费入口：进入续费页后返回详情；该入口会把订单临时置为待续费，本轮后续随测试订单清理。

### 生命周期开关与真实清理

- 关机阶段：`cloud_server_shutdown_enabled=0` 阻断真实关机；资产开关关闭阻断真实关机；打开后真实关机成功，订单为 `suspended`，资产为 `stopped/is_active=False`。
- 删机阶段：`cloud_server_delete_enabled=0` 阻断真实删机；资产开关关闭阻断真实删机；打开后真实删机成功，订单和资产为 `deleted`，实例标识清空，固定 IP 进入待释放。
- 固定 IP 释放阶段：`cloud_ip_delete_enabled=0` 阻断真实释放固定 IP；资产开关关闭阻断真实释放固定 IP；打开后真实释放成功，固定 IP 名称、`public_ip` 和 `ip_recycle_at` 清空。

### 配置恢复与最终状态

- 已恢复：`cloud_server_shutdown_enabled` 删除回默认值，`cloud_server_delete_enabled=1`，`cloud_ip_delete_enabled=1`，`cloud_suspend_time=15:00`，`cloud_delete_time=15:00`，`cloud_unattached_ip_delete_time=15:00`。
- 最终状态：订单 `#92` 为 `deleted`，资产 `#340` 为 `deleted/is_active=False`，实例标识、固定 IP 名称和 IP 回收时间均已清空。
- 最终余额：测试用户 USDT 余额为 975，TRX 余额为 984.747。
- 剩余风险：TRON 扫块器仍有 429 限流和积压追赶日志；本轮未执行链上广播或真实地址充值到账。

## 2026-06-07 生命周期创建、关机、删机、释放 IP 真机复测

- 状态：通过，过程中发现并修复 1 个后台订单详情前端告警。
- 授权：用户已明确授权真实创建服务器和删除服务器，重点覆盖生命周期开关影响。
- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 云厂商：AWS Lightsail
- 套餐：新加坡，`实机测试 Nano`
- 测试用户：`TelegramUser #172`，`codex_real_machine_test`
- 订单：`#50095` / `SRV20260607125634332663`
- 资产：`#1500331`
- 云实例：`20260607-************-*-o50095`
- 公网 IP：`18.141.xxx.xxx`
- 支付方式：USDT 钱包余额支付。
- 金额：5 USDT。

### 真实创建

- 使用项目服务创建余额支付订单，订单从 `paid` 进入开通流程。
- AWS Lightsail 实例真实创建成功，固定 IP 绑定成功。
- BBR、MTProxy 主代理、备用代理、Telemt 多端口和 SOCKS5 初始化完成。
- 开通后本地订单为 `completed`，资产为 `running/is_active=True`。
- 资产到期事实写入 `CloudAsset.actual_expires_at=2026-07-08T12:59:50.065766+00:00`。
- 敏感信息：未记录完整公网 IP、代理链接、代理 secret、登录密码、Telegram token、session 或云账号密钥。

### 后台页面实测

- 实际打开订单详情页 `/admin/cloud-orders/50095`：显示订单详情、已删除状态、服务器信息和生命周期区域；控制台 0 error / 0 warning。
- 实际打开资产详情页 `/admin/cloud-assets/1500331`：显示代理详情、已删除状态、生命周期区域和关联订单；控制台 0 error / 0 warning。
- 实际打开计划页 `/admin/tasks/plans`：显示计划页、关机服务器/删除服务器/删除 IP 总开关、IP 删除历史记录和计数；控制台 0 error / 0 warning。
- 计划接口刷新后计数：当前计划资产 `1500001`，关机计划 `979990`，删除计划 `2`，IP 删除计划 `500000`，IP 删除历史 `520008`。

### 关机阶段矩阵

- 关机总开关关闭：`cloud_server_shutdown_enabled=0` 时执行计划关机，返回“服务器关机总开关已关闭，跳过真实关机”，订单保持 `completed`，资产保持 `running/is_active=True`。
- 资产关机开关关闭：`CloudAsset.shutdown_enabled=False` 时执行计划关机，返回“资产关机计划开关已关闭，跳过真实关机”，订单保持 `completed`，资产保持 `running/is_active=True`。
- 非执行时间窗口：`cloud_suspend_time` 设置为当前窗口外时执行计划关机，返回“当前不在后台配置的服务器关机执行时间窗口”，订单保持 `completed`。
- 真实关机：打开关机总开关、资产关机开关和当前执行窗口后，AWS 实例真实关机成功；订单变为 `suspended`，资产变为 `stopped/is_active=False`。

### 删机阶段矩阵

- 删机总开关关闭：`cloud_server_delete_enabled=0` 时执行计划删机，返回“删除服务器总开关已关闭，跳过真实删机”，订单保持 `suspended`。
- 资产删机开关关闭：`CloudAsset.server_delete_enabled=False` 时执行计划删机，返回“资产服务器删除计划开关已关闭，跳过真实删机”，订单保持 `suspended`。
- 非执行时间窗口：`cloud_delete_time` 设置为当前窗口外时执行计划删机，返回“当前不在后台配置的服务器删除执行时间窗口”，订单保持 `suspended`。
- 第一次真实删机：AWS 返回实例正在停止状态转换，不能删除；本地保持 `suspended/stopped`，未误标已删除。
- 重试真实删机：等待状态稳定后只针对订单 `#50095` 重试，AWS 实例真实删除成功；订单变为 `deleted`，资产变为 `deleted/is_active=False`，实例标识清空，固定 IP 进入保留待释放状态。

### 固定 IP 释放矩阵

- 删 IP 总开关关闭：`cloud_ip_delete_enabled=0` 时执行固定 IP 回收，返回“删除IP总开关已关闭，跳过真实释放固定 IP”，固定 IP 保留信息仍在。
- 资产 IP 删除开关关闭：`CloudAsset.ip_delete_enabled=False` 时执行固定 IP 回收，返回“资产 IP 删除计划开关已关闭，跳过真实释放固定 IP”，固定 IP 保留信息仍在。
- 非执行时间窗口：`cloud_unattached_ip_delete_time` 设置为当前窗口外时执行固定 IP 回收，返回“当前不在后台配置的 IP 删除执行时间窗口”，固定 IP 保留信息仍在。
- 真实释放：打开删 IP 总开关、资产 IP 删除开关和当前执行窗口后，AWS 固定 IP 真实释放成功；订单仍为 `deleted`，固定 IP 名称、`public_ip` 和 `ip_recycle_at` 已清空，资产保持 `deleted/is_active=False`。

### 配置恢复与最终状态

- 已恢复测试前生命周期配置：`cloud_server_shutdown_enabled` 删除为默认值，`cloud_server_delete_enabled=1`，`cloud_ip_delete_enabled=1`，`cloud_suspend_time=15:00`，`cloud_delete_time=15:00`，`cloud_unattached_ip_delete_time=15:00`。
- 生命周期任务最终状态：`suspend/done`、`delete/done`、`recycle/done`。
- 最终本地状态：订单 `#50095` 为 `deleted`，资产 `#1500331` 为 `deleted/is_active=False`；实例标识、固定 IP 名称、公网 IP 和 IP 回收时间均已清空。
- 最终清理结论：本轮真实 AWS 测试实例已删除，固定 IP 已释放，未发现本轮测试资源残留。

## 2026-06-07 生命周期创建、关机、删机、释放 IP 二次真机复测

- 状态：通过，并修复 1 个生命周期任务状态收敛问题。
- 授权：用户再次明确要求生命周期创建服务器、删除服务器也要测试到，延续对真实云资源成本的授权。
- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 云厂商：AWS Lightsail
- 套餐：新加坡，`实机测试 Nano`
- 测试用户：`TelegramUser #172`，`codex_real_machine_test`
- 订单：`#50096`
- 资产：`#1500332`
- 云实例：已脱敏记录，最终已删除并清空本地实例标识。
- 公网 IP：已脱敏记录，最终当前公网 IP 已清空。
- 支付方式：USDT 钱包余额支付。
- 金额：5 USDT。

### 真实创建

- 使用项目服务创建余额支付订单，订单从 `paid` 进入开通流程。
- AWS Lightsail 实例真实创建成功，固定 IP 绑定成功。
- BBR、MTProxy 主代理、备用代理、Telemt 多端口和 SOCKS5 初始化完成。
- 开通后本地订单为 `completed`，资产为 `running/is_active=True`。
- 资产到期事实写入 `CloudAsset.actual_expires_at`。
- 敏感信息：本报告未记录完整公网 IP、完整实例名、完整固定 IP 名、代理链接、代理 secret、登录密码、Telegram token、session 或云账号密钥。

### 关机阶段矩阵

- 关机总开关关闭：`cloud_server_shutdown_enabled=0` 时执行计划关机，返回“服务器关机总开关已关闭，跳过真实关机”，订单保持 `completed`，资产保持 `running/is_active=True`。
- 资产关机开关关闭：`CloudAsset.shutdown_enabled=False` 时执行计划关机，返回“资产关机计划开关已关闭，跳过真实关机”，订单保持 `completed`，资产保持 `running/is_active=True`。
- 非执行时间窗口：`cloud_suspend_time` 设置为当前窗口外时执行计划关机，返回“当前不在后台配置的服务器关机执行时间窗口”，订单保持 `completed`。
- 真实关机：打开关机总开关、资产关机开关和当前执行窗口后，AWS 实例真实关机成功；订单变为 `suspended`，资产变为 `stopped/is_active=False`。

### 删机阶段矩阵

- 删机总开关关闭：`cloud_server_delete_enabled=0` 时执行计划删机，返回“删除服务器总开关已关闭，跳过真实删机”，订单保持 `suspended`。
- 资产删机开关关闭：`CloudAsset.server_delete_enabled=False` 时执行计划删机，返回“资产服务器删除计划开关已关闭，跳过真实删机”，订单保持 `suspended`。
- 非执行时间窗口：`cloud_delete_time` 设置为当前窗口外时执行计划删机，返回“当前不在后台配置的服务器删除执行时间窗口”，订单保持 `suspended`。
- 第一次真实删机：AWS 返回实例正在停止状态转换，系统保持 `suspended/stopped`，未误标已删除。
- 第二次真实删机：等待后人工重试成功，AWS 实例真实删除；订单和资产进入 `deleted`，实例标识清空，固定 IP 进入待释放状态。

### 固定 IP 释放矩阵

- 删 IP 总开关关闭：`cloud_ip_delete_enabled=0` 时执行固定 IP 回收，返回“删除IP总开关已关闭，跳过真实释放固定 IP”，固定 IP 保留信息仍在。
- 资产 IP 删除开关关闭：`CloudAsset.ip_delete_enabled=False` 时执行固定 IP 回收，返回“资产 IP 删除计划开关已关闭，跳过真实释放固定 IP”，固定 IP 保留信息仍在。
- 非执行时间窗口：`cloud_unattached_ip_delete_time` 设置为当前窗口外时执行固定 IP 回收，返回“当前不在后台配置的 IP 删除执行时间窗口”，固定 IP 保留信息仍在。
- 真实释放：打开删 IP 总开关、资产 IP 删除开关和当前执行窗口后，AWS 固定 IP 真实释放成功；订单仍为 `deleted`，固定 IP 名称、当前公网 IP 和 `ip_recycle_at` 已清空，资产保持 `deleted/is_active=False`。

### 页面实测

- 实际打开订单详情页 `/admin/cloud-orders/50096`：页面标题为“云订单详情”，订单状态为已删除，生命周期区域正常显示，控制台 0 error / 0 warning。
- 实际打开资产详情页 `/admin/cloud-assets/1500332`：页面标题为“代理详情”，包含已删除状态、生命周期区域和关联订单，控制台 0 error。
- 实际打开计划页 `/admin/tasks/plans`：页面标题为“计划”，包含关机计划、删除计划、IP 删除和历史区域，控制台 0 error。

### 发现与修复

- 发现：真实删机第一次因 AWS 停止中过渡失败、第二次人工重试成功后，原 `delete` 生命周期任务仍为 `failed`，会造成计划页或任务中心假失败。
- 修复：生命周期动作成功后，统一将同一订单或资产的同类型未完成 / 失败任务收敛为 `done`。
- 回归：新增聚焦测试 `test_manual_delete_success_finishes_failed_lifecycle_delete_task`。
- 本轮测试订单最终生命周期任务为：`suspend/done`、`delete/done`、`recycle/done`，失败数 0。

### 配置恢复与最终状态

- 已恢复测试前生命周期配置：`cloud_server_shutdown_enabled` 删除为默认值，`cloud_server_delete_enabled=1`，`cloud_ip_delete_enabled=1`，`cloud_suspend_time=15:00`，`cloud_delete_time=15:00`，`cloud_unattached_ip_delete_time=15:00`。
- 最终本地状态：订单 `#50096` 为 `deleted`，资产 `#1500332` 为 `deleted/is_active=False`；实例标识、固定 IP 名称、当前公网 IP 和 IP 回收时间均已清空。
- 最终清理结论：本轮真实 AWS 测试实例已删除，固定 IP 已释放，未发现本轮测试资源残留。

## 2026-06-08 生命周期创建、关机、删机、释放 IP 三次真机复测

- 状态：通过。
- 授权：用户继续明确要求生命周期创建服务器、删除服务器也要测试到，延续对真实云资源成本的授权。
- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 云厂商：AWS Lightsail
- 套餐：新加坡，`实机测试 Nano`
- 测试用户：`TelegramUser #172`，`codex_real_machine_test`
- 订单：`#50097`
- 资产：`#1500333`
- 云实例：已脱敏记录，最终已删除并清空本地实例标识。
- 公网 IP：已脱敏记录，最终当前公网 IP 已清空。
- 支付方式：USDT 钱包余额支付。
- 金额：5 USDT。

### 真实创建

- 使用项目服务创建余额支付订单，订单从 `paid` 进入开通流程。
- AWS Lightsail 实例真实创建成功，固定 IP 绑定成功。
- BBR、MTProxy 主代理、备用代理、Telemt 多端口和 SOCKS5 初始化完成。
- 开通后本地订单为 `completed`，资产为 `running/is_active=True`。
- 资产到期事实写入 `CloudAsset.actual_expires_at`。
- 敏感信息：本报告未记录完整公网 IP、完整实例名、完整固定 IP 名、代理链接、代理 secret、登录密码、Telegram token、session 或云账号密钥。

### 关机阶段矩阵

- 关机总开关关闭：`cloud_server_shutdown_enabled=0` 时执行计划关机，返回“服务器关机总开关已关闭，跳过真实关机”，订单保持 `completed`，资产保持 `running/is_active=True`。
- 资产关机开关关闭：`CloudAsset.shutdown_enabled=False` 时执行计划关机，返回“资产关机计划开关已关闭，跳过真实关机”，订单保持 `completed`，资产保持 `running/is_active=True`。
- 非执行时间窗口：`cloud_suspend_time` 设置为当前窗口外时执行计划关机，返回“当前不在后台配置的服务器关机执行时间窗口”，订单保持 `completed`。
- 真实关机：打开关机总开关、资产关机开关和当前执行窗口后，AWS 实例真实关机成功；订单变为 `suspended`，资产变为 `stopped/is_active=False`。

### 删机阶段矩阵

- 删机总开关关闭：`cloud_server_delete_enabled=0` 时执行计划删机，返回“删除服务器总开关已关闭，跳过真实删机”，订单保持 `suspended`。
- 资产删机开关关闭：`CloudAsset.server_delete_enabled=False` 时执行计划删机，返回“资产服务器删除计划开关已关闭，跳过真实删机”，订单保持 `suspended`。
- 非执行时间窗口：`cloud_delete_time` 设置为当前窗口外时执行计划删机，返回“当前不在后台配置的服务器删除执行时间窗口”，订单保持 `suspended`。
- 第一次真实删机：AWS 返回实例正在停止状态转换，系统保持 `suspended/stopped`，未误标已删除。
- 第二次真实删机：等待后重试成功，AWS 实例真实删除；订单和资产进入 `deleted`，实例标识清空，固定 IP 进入待释放状态。

### 固定 IP 释放矩阵

- 删 IP 总开关关闭：`cloud_ip_delete_enabled=0` 时执行固定 IP 回收，返回“删除IP总开关已关闭，跳过真实释放固定 IP”，固定 IP 保留信息仍在。
- 资产 IP 删除开关关闭：`CloudAsset.ip_delete_enabled=False` 时执行固定 IP 回收，返回“资产 IP 删除计划开关已关闭，跳过真实释放固定 IP”，固定 IP 保留信息仍在。
- 非执行时间窗口：`cloud_unattached_ip_delete_time` 设置为当前窗口外时执行固定 IP 回收，返回“当前不在后台配置的 IP 删除执行时间窗口”，固定 IP 保留信息仍在。
- 真实释放：打开删 IP 总开关、资产 IP 删除开关和当前执行窗口后，AWS 固定 IP 真实释放成功；订单仍为 `deleted`，固定 IP 名称、当前公网 IP 和 `ip_recycle_at` 已清空，资产保持 `deleted/is_active=False`。

### 页面实测

- 实际打开计划页 `/admin/tasks/plans`：页面标题为“计划”，包含关机服务器、删除服务器、删除 IP 三个总开关，包含关机计划、服务器删除历史和 IP 删除历史区域，控制台 0 error / 0 warning。
- 实际打开订单详情页 `/admin/cloud-orders/50097`：页面标题为“云订单详情”，订单状态为已删除，生命周期区域正常显示，无加载失败或请求失败，控制台 0 error / 0 warning。
- 实际打开资产详情页 `/admin/cloud-assets/1500333`：页面标题为“代理详情”，包含已删除状态、生命周期区域和关联订单，无加载失败或请求失败，控制台 0 error / 0 warning。

### 配置恢复与最终状态

- 已恢复测试前生命周期配置：`cloud_server_shutdown_enabled` 删除为默认值，`cloud_server_delete_enabled=1`，`cloud_ip_delete_enabled=1`，`cloud_suspend_time=15:00`，`cloud_delete_time=15:00`，`cloud_unattached_ip_delete_time=15:00`。
- 生命周期任务最终状态：`suspend/done`、`delete/done`、`recycle/done`。
- 最终本地状态：订单 `#50097` 为 `deleted`，资产 `#1500333` 为 `deleted/is_active=False`；实例标识、固定 IP 名称、当前公网 IP 和 IP 回收时间均已清空。
- 最终清理结论：本轮真实 AWS 测试实例已删除，固定 IP 已释放，未发现本轮测试资源残留。

## 2026-06-08 人工创建无订单服务器同步与非到期自动巡检

- 状态：通过，只验证同步发现和非到期自动巡检，不执行真实关机、删机或释放 IP。
- 授权：用户明确授权创建 1 台测试服务器并接受真实云费用；随后补充要求“先别让他到期”，本轮停止到期推进和破坏性生命周期动作。
- 后端仓库：`/Users/a399/Desktop/data/shop`
- 测试数据库：`shop_manual_20260608_5676`，MySQL `127.0.0.1:3307`
- 测试入口：前端 `127.0.0.1:5676`，后端 `127.0.0.1:8010`
- 云厂商：AWS Lightsail
- 地区：`ap-southeast-1`
- 套餐：`nano_3_0`
- 云账号：后台 AWS 云账号 `#55`，启用且状态 `ok`
- 云实例：`codex-manual-lifecycle-**************`
- 公网 IP：`18.136.xxx.xxx`
- 本地资产：新库资产 `#4`
- 订单：无订单，`CloudAsset.order_id=None`
- 固定 IP：未申请固定 IP，使用实例临时公网 IP。

### 真实创建

- 使用项目 AWS Lightsail 创建函数直接创建 1 台测试实例，并绑定后台云账号 `#55`。
- 创建参数设置 `skip_static_ip=True`，未额外申请固定 IP。
- 实例真实创建成功，AWS 返回运行中状态，并拿到临时公网 IP。
- 端口放行完成：SSH `22`，MTProxy 相关端口 `443/9529-9534`。
- 敏感信息：本报告未记录完整公网 IP、完整实例名、登录密码、代理链接、代理 secret、Telegram token、session 或云账号密钥。

### 同步发现

- 在新库运行：

```bash
uv run python manage.py sync_aws_assets --region ap-southeast-1 --account-id 55
```

- 同步成功，云账号下共发现 `4` 台实例和 `1` 个未附加固定 IP。
- 刚创建的测试实例被同步为 `CloudAsset #4`。
- 该资产状态：
  - `kind=server`
  - `order_id=None`
  - `status=running`
  - `is_active=True`
  - `actual_expires_at=None`
  - `shutdown_enabled=True`
  - `server_delete_enabled=True`
  - `ip_delete_enabled=True`

### 非到期自动巡检

- 未设置 `CloudAsset.actual_expires_at`，未把资产改为到期，未手工触发关机或删机。
- 运行生命周期 due 选择器：
  - 无订单服务器待删队列为空。
  - 刚创建的测试资产不在待删队列中。
  - 未附加固定 IP 待释放队列为空。
- 当前默认总开关：
  - `cloud_server_delete_enabled=False`
  - `cloud_ip_delete_enabled=False`
- 运行一轮 `lifecycle_tick(defer_destructive_seconds=0)` 后：
  - 扫描日志显示 `孤儿资源待删=0`。
  - 测试资产仍为 `running/is_active=True`。
  - 测试资产仍无到期时间。
  - 未生成 `CloudLifecycleTask`。
- 复查 AWS 云端状态：测试实例仍为 `running`。

### 结论

- 人工创建、同步后形成的无订单服务器，如果没有人工维护 `CloudAsset.actual_expires_at`，不会进入无订单服务器删除队列。
- 本轮自动生命周期巡检没有对该人工创建服务器执行关机、删机或释放 IP。
- 当前测试实例仍真实运行中，会继续产生云费用；本轮按用户要求未清理该实例。

### 追加：添加未来到期时间后的计划变化

- 按用户要求继续测试“添加一个到期时间后，订单和关机计划、删除计划变化”。
- 只设置未来到期时间，不设置过去到期时间，不执行真实关机或删机。
- 目标资产：`CloudAsset #4`
- 设置到期时间：`2026-07-08 20:37:18 +08:00`

观察结果：

- 订单：没有创建订单，`CloudServerOrder` 仍为 `0` 条，目标资产仍为 `order_id=None`。
- 关机计划：目标资产新增 `1` 条关机计划。
- 关机计划状态：`scheduled_future / scheduled`
- 关机计划执行说明：`关机服务器 2026-07-11 15:00:00`
- 下一次计划运行时间：`2026-07-11 15:00:00 +08:00`
- 删除计划：目标资产没有进入删除计划，目标删除计划 `0` 条。
- 生命周期任务：未生成 `CloudLifecycleTask`。
- 运行一轮 `lifecycle_tick(defer_destructive_seconds=0)` 后，日志显示 `待关机=0`、`待删机=0`、`孤儿资源待删=0`，目标资产仍为 `running/is_active=True`。

结论：

- 给无订单人工服务器补未来到期时间后，系统不会创建订单。
- 当前阶段会在计划页生成未来关机计划。
- 删除计划不会立即出现；在实例仍运行且关机计划尚未执行时，删除计划为 `0`。
- 因到期时间仍在未来，自动巡检不会立即对该服务器执行破坏性动作。

### 追加：到期 4 天前与续费待支付单测试

- 按用户要求继续把目标资产到期时间调整为过去时间，并测试续费入口。
- 目标资产：`CloudAsset #4`
- 设置到期时间：`2026-06-04 21:02:58 +08:00`
- 本阶段不执行真实关机、真实删机、真实释放 IP、真实链上支付或钱包扣款。

到期 4 天前后的观察：

- 订单：调整到期时间本身不会创建订单，目标资产仍可保持 `order_id=None`。
- 关机计划：目标资产进入关机计划，计划关机时间按到期后 3 天计算为 `2026-06-07 15:00:00 +08:00`。
- 删除计划：目标资产未进入服务器删除计划页，删除计划数量为 `0`。
- 无订单服务器待删队列：目标资产会进入 `_get_orphan_asset_delete_due()`，说明如果直接执行真实生命周期删除路径，系统会把它视为到期孤儿服务器待删对象。
- 本阶段未运行会执行删机的 `lifecycle_tick`。

续费入口前置数据：

- 新测试库缺少 `cloud_plan` 基础套餐，续费入口最初返回“当前地区暂无可用套餐，请联系人工客服”。
- 为了本地续费测试，在新库补入 `9` 条 AWS Lightsail 新加坡默认测试套餐；该操作只写入本地数据库，不调用云 API。
- 选用续费套餐：`CloudServerPlan #1`，`入门款`，价格 `19.00 USDT`。

生成待支付续费单后的观察：

- 续费服务：`prepare_cloud_asset_renewal_with_link(...)`
- 结果：成功生成待支付订单 `CloudServerOrder #1`。
- 订单状态：`pending`
- 支付方式：`address`
- 币种：`USDT`
- 金额：订单总额 `19.00`，链上唯一应付金额 `19.208`。
- 订单过期时间：`2026-06-08 22:26:06 +08:00`
- 资产变化：目标资产变为 `order_id=1`，仍为 `status=running/is_active=True`。
- 到期事实：目标资产 `actual_expires_at` 未被续费单创建动作延长，仍为 `2026-06-04 21:02:58 +08:00`。
- 用户归属：目标资产 `user_id` 仍为空；续费单使用本地测试用户作为订单用户。

计划和自动程序队列变化：

- 关机计划页：目标资产仍显示在关机计划中，状态来自待支付订单 `pending`，并提示已到关机时间。
- 删除计划页：目标资产仍未进入服务器删除计划页，删除计划数量为 `0`。
- 无订单服务器待删队列：目标资产已从 `_get_orphan_asset_delete_due()` 移除，因为资产已有 `order_id=1`。
- 生命周期 due 队列：目标订单不在 `expire`、`suspend`、`delete`、`recycle` 任一执行队列中。

结论：

- 生成续费待支付单会解除“无订单孤儿服务器待删”风险，因为资产不再是 `order_id=None`。
- 生成待支付单不会延长 `CloudAsset.actual_expires_at`，不会创建真实云资源，也不会调用 AWS。
- 当前自动执行队列不会对这台测试服务器执行关机、删机或释放 IP。
- 计划页仍显示目标资产已到关机时间，但真实生命周期执行队列已排除该 `pending` 续费订单；这是后续可继续复查的展示一致性点。

### 追加：手工把续费支付订单改为已完成

- 按用户授权，把本地新库续费订单 `CloudServerOrder #1` 手工改为已完成。
- 本阶段只改本地数据库字段，不调用真实支付确认服务，不调用钱包扣款，不调用 AWS，不执行生命周期 tick。
- 修改字段：
  - `status=completed`
  - `paid_at=2026-06-08 22:12:26 +08:00`
  - `completed_at=2026-06-08 22:12:26 +08:00`
  - `expired_at=None`
  - `service_started_at=2026-06-08 22:12:26 +08:00`

修改后的状态：

- 目标资产：`CloudAsset #4`
- 关联订单：`order_id=1`
- 资产状态：`running/is_active=True`
- 资产到期事实：仍为 `2026-06-04 21:02:58 +08:00`，未被手工完成订单动作延长。
- 订单状态：`completed`

计划和队列观察：

- 关机计划数量：`1`
- 关机计划到期数量：`1`
- 目标关机计划状态：`已到关机时间，待执行关机服务器`
- 目标关机计划 `should_execute=True`
- 删除计划数量：`0`
- 删除到期数量：`0`
- 无订单服务器待删队列：目标资产不在 `_get_orphan_asset_delete_due()` 中，因为资产已有订单。
- 生命周期 due 队列：目标订单进入 `expire` 和 `suspend`，未进入 `delete` 或 `recycle`。

结论：

- 待支付订单改为 `completed` 后，系统会把目标资产重新视为已完成订单下的到期服务器。
- 因资产到期时间仍是 4 天前，自动生命周期选择器会把它列入到期和关机执行队列。
- 当前仍没有进入删机队列，也没有进入孤儿删机队列。
- 如果此时启动或手工运行真实生命周期 tick，可能触发真实关机；本阶段按测试边界没有执行。

### 追加：重新生成续费状态并手工改为已支付

- 按用户要求，基于当前订单重新生成续费状态，并手工改为已支付。
- 本阶段只调用续费准备函数，不调用真实支付确认服务，不扣钱包，不调用 AWS，不执行生命周期 tick。
- 续费准备函数：`create_cloud_server_renewal_by_public_query(order_id=1, days=31)`

重新生成续费状态后的中间结果：

- 订单：`CloudServerOrder #1`
- 状态：`renew_pending`
- 支付方式：`address`
- 币种：`USDT`
- 订单总额：`19.00`
- 链上唯一应付金额：`19.693`
- 支付过期时间：`2026-06-08 22:45:30 +08:00`
- 支付时间：空

手工改为已支付后的状态：

- 订单状态：`paid`
- 支付方式：`address`
- 支付时间：`2026-06-08 22:15:30 +08:00`
- 支付过期时间：空
- 资产状态：`CloudAsset #4` 仍为 `running/is_active=True`
- 资产到期事实：仍为 `2026-06-04 21:02:58 +08:00`，未被手工 paid 动作延长。

计划和队列观察：

- 关机计划数量：`1`
- 关机计划到期数量：`1`
- 目标关机计划状态：`已到关机时间，待执行关机服务器`
- 目标关机计划 `should_execute=True`
- 删除计划数量：`0`
- 删除到期数量：`0`
- 无订单服务器待删队列：目标资产不在 `_get_orphan_asset_delete_due()` 中。
- 生命周期 due 队列：目标订单不在 `expire`、`suspend`、`delete`、`recycle` 任一执行队列中。

结论：

- 重新生成续费状态并手工改为 `paid` 后，资产仍不会被延长到期时间，真实云端也不会发生变化。
- `paid` 状态下，当前生命周期执行选择器不会把目标订单列入关机、删机或 IP 回收执行队列。
- 计划页仍显示该资产已到关机时间且 `should_execute=True`，但执行器 due 队列已排除该 `paid` 订单；这是一个展示与执行选择器口径不一致的观察点。

### 追加：按用户要求删除测试服务器并收敛本地状态

- 按用户要求，删除本轮由 Codex 创建的 AWS Lightsail 测试服务器。
- 本阶段执行真实云资源删除，仅限此前授权创建的 1 台测试服务器。
- 未打印完整实例名、完整公网 IP、登录密码、代理链接、代理 secret、Telegram token、session 或云账号密钥。

云端删除：

- 删除方式：按记录文件中的实例名调用 AWS Lightsail `delete_instance`。
- 删除前云端状态：`running`
- 删除提交：成功
- 删除确认：轮询后 AWS Lightsail `get_instance` 返回不存在，云端状态确认为 `not_found`。

同步收敛：

- 先运行 `sync_aws_assets --region ap-southeast-1 --account-id 55`。
- 同步器检测到目标资产云端不存在，但因缺失删除保护阈值为 `5` 次、确认间隔为 `60` 分钟，短时间内只进入“云上未找到实例/IP-待确认”状态，不立即标记删除。
- 在云端已由 AWS 查询确认不存在后，本测试库调用同步器内部“云端缺失后标记删除”的同一收敛逻辑，避免等待 5 小时。

最终本地状态：

- 目标资产：`CloudAsset #4`
- 资产状态：`deleted`
- 资产有效性：`is_active=False`
- 资产当前公网 IP：已清空，仅保留历史 IP。
- 关联订单：`CloudServerOrder #1`
- 订单状态：`deleted`
- 订单当前公网 IP：已清空，仅保留历史 IP。
- 订单过期标记时间：`2026-06-08 22:27:22 +08:00`

最终队列复核：

- AWS 云端实例：`not_found`
- 无订单服务器待删队列：不包含目标资产。
- 生命周期 due 队列：目标订单不在 `expire`、`suspend`、`delete`、`recycle` 任一执行队列中。

清理结论：

- 本轮真实测试服务器已从 AWS Lightsail 删除。
- 新测试库中的目标资产和关联订单已收敛为已删除状态。
- 当前没有发现该测试服务器残留在生命周期执行队列中。

## 2026-06-08 运行中同步服务器真实续费闭环测试

- 状态：通过，先发现问题并完成修复，随后重跑同一真机订单验证通过。
- 授权：用户明确要求真实创建服务器测试，并已授权真实创建/删除云资源。
- 测试数据库：`shop_manual_20260608_5676`，MySQL `127.0.0.1:3307`
- 测试入口：前端 `127.0.0.1:5676`，后端 `127.0.0.1:8010`
- 云厂商：AWS Lightsail
- 地区：`ap-southeast-1`
- 套餐：`micro_3_0`
- 云账号：后台 AWS 云账号 `#55`
- 本地资产：`CloudAsset #6`
- 续费订单：`CloudServerOrder #2`
- 资源脱敏：实例名 `codex-manual-renewal-**************`，公网 IP `47.129.xxx.xxx`

### 真实创建与同步

- 使用项目 AWS Lightsail 创建函数真实创建 1 台测试服务器。
- 创建参数设置 `skip_static_ip=True`，未额外申请固定 IP。
- AWS 返回实例进入 `running`，并拿到临时公网 IP。
- 已放行 SSH `22` 和 MTProxy 相关端口。
- 运行 `sync_aws_assets --region ap-southeast-1 --account-id 55` 后，新实例同步为 `CloudAsset #6`。
- 同步后资产状态：
  - `kind=server`
  - `status=running`
  - `is_active=True`
  - `order_id=None`
  - `actual_expires_at=None`

### 人工改为已过期并生成续费单

- 把 `CloudAsset #6` 绑定到测试用户，并把 `CloudAsset.actual_expires_at` 改为过去 4 天。
- 过期后计划状态：
  - 关机计划命中 `1` 条。
  - 删除计划命中 `0` 条。
- 调用资产续费准备流程生成待支付续费单：
  - 订单：`CloudServerOrder #2`
  - 状态：`pending`
  - 支付方式：`address`
  - 金额：`19.00 USDT`
  - 资产关联订单变为 `order_id=2`

### 首次余额支付暴露的问题

- 给测试用户补足余额后，走 `pay_cloud_server_renewal_with_balance(order_id=2, user_id=1, currency='USDT', days=31)`。
- 首次实测结果：
  - 支付函数返回成功。
  - 用户余额从 `1000` 扣到 `981`。
  - 订单状态从 `pending` 变成 `paid`。
  - 订单 `paid_at` 已写入。
  - 但 `CloudAsset.actual_expires_at` 仍停留在过去时间。
  - 关机计划仍命中 `1` 条。
  - 删除计划仍为 `0` 条。

结论：

- 运行中的人工同步服务器续费不能按“未附加固定 IP 恢复单”处理。
- 余额支付成功后必须直接延长资产到期事实，并重算关机、删机、IP 回收计划。

### 修复后重跑余额支付

- 修复后把同一测试订单恢复为待支付状态，重新走余额支付入口。
- 重跑结果：
  - 支付错误：无。
  - 订单状态：`completed`
  - 订单支付时间：已写入。
  - 用户余额：`981.000000`
  - 资产到期事实：延长到 `2026-07-09 22:33:03 +08:00`
  - 订单关机时间：`2026-07-12 15:00:00 +08:00`
  - 订单删机时间：`2026-07-12 15:00:00 +08:00`
  - IP 回收时间：`2026-07-27 15:00:00 +08:00`
  - 关机到期执行：`False`
  - 删机到期执行：`False`
  - IP 删除到期执行：`False`

说明：

- 当前配置下服务器删除间隔为关机后 `0` 天，所以订单派生的删机时间和关机时间相同。
- 删除计划查询仍要求服务器已经完成关机后才进入删除计划，因此续费后不会提前出现在删除执行段。

### 前端实际核查

- 实际打开代理详情页：`/admin/cloud-assets/6`
- 页面显示：
  - 资产状态：运行中。
  - 到期时间：`2026-07-09 22:33:03`。
  - 订单状态：已创建。
  - 生命周期日志存在“运行中资产续费 31 天”记录。
- 实际打开计划页：`/admin/tasks/plans`
- 页面显示：
  - 关机计划：`1` 条，目标资产为未来排期。
  - 服务到期：`2026-07-09 22:33:03`。
  - 关机时间：`2026-07-12 15:00:00`。
  - 删机时间：`2026-07-12 15:00:00`。
  - 计划状态：已排期。
  - 删除计划：`0` 条。
  - IP 删除计划显示的是另一条未附加固定 IP，不是本次服务器资产。
- 前端控制台：仅有 Vite dev WebSocket 热更新握手错误，不是业务接口错误。

### 链上确认入口补测

- 本轮同时修复链上支付确认入口。
- 链上确认路径复用运行中资产续期完成逻辑。
- 通知阶段不再在 async 上下文里同步查询资产到期时间。
- 聚焦测试覆盖链上确认后：
  - 订单变为 `completed`。
  - `CloudAsset.actual_expires_at` 延长到未来。
  - `suspend_at`、`delete_at`、`ip_recycle_at` 全部后移。

### 清理

- 本轮新建测试服务器已调用 AWS Lightsail `delete_instance` 删除。
- AWS 查询确认目标实例已 `not_found`。
- 删除后运行 `sync_aws_assets --region ap-southeast-1 --account-id 55`：
  - 同步器扫描实例数量从 `4` 变为 `3`。
  - 资产 `#6` 进入“云上不存在 第 1/5 次确认”。
  - 当前可见代理数从 `5` 变为 `4`。
- 未打印完整实例名、完整公网 IP、登录密码、代理链接、代理 secret、Telegram token、session 或云账号密钥。

### 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery cloud.tests.CloudServerServicesTestCase.test_active_asset_renewal_wallet_payment_extends_asset_and_lifecycle cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_repairs_completed_unpaid_state orders.tests.ChainPaymentScannerTestCase.test_active_asset_renewal_chain_payment_extends_asset_and_lifecycle --keepdb
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/services.py orders/payment_scanner.py orders/tests.py
git diff --check
```

## 2026-06-10 18:22 CST 并行安装第三轮复测与备用通知兜底

### 背景

- 用户要求在已推送修复后再并行测试一轮。
- 用户随后提供 Telegram 通知失败日志，确认安装完成通知的 inline keyboard URL 无效。
- 用户最终指定备用通知链接改为 `https://t.me/sy168`。

### 真机并行复测

- 授权：延续用户对真实云资源成本的明确授权。
- 隔离数据库：`.shop-load-tests/shop-loadtest-realmachine-third.sqlite3`
- 报告文件：`.shop-load-tests/real-machine-parallel-install-report-third.json`
- 云厂商：AWS Lightsail，账号 `#55`，区域 `ap-southeast-1`。
- 套餐：`#131` / `nano_3_0`。
- 第 1 轮：并行提交 5 个创建安装任务，4 台完成创建和代理安装，1 台创建失败并进入失败清理。
- 第 2 轮：并行触发重装、重建、修改配置；重建迁移完成，修改配置迁移完成，重装入口按现有 `completed` 状态跳过。
- 第 3 轮：再次并行触发创建、重装、修改配置；新增创建因固定 IP 配额限制失败，修改配置迁移完成，重装仍按现有逻辑跳过。
- SOCKS5 输出继续保持 `https://t.me/socks?...&user=***&pass=***` 格式。
- 本轮没有复现远端安装锁权限错误。

### 清理与残留复核

- 压测脚本自动清理 `LOAD...` 订单下实例和固定 IP。
- 脚本未自动覆盖迁移订单实例，已按测试前缀手动补清 3 台 `SRVREBUILD...` / `SRVUPGRADE...` 实例：
  - `20260610***0-o7`
  - `20260610***5-o6`
  - `20260610***0-o9`
- AWS 只读复核：测试前缀 `20260610-930000610001` 下实例列表为空，固定 IP 列表为空。

### 备用通知修复

- 修复 `bot/keyboards.py` 中客服链接读取逻辑。
- 配置为 `@用户名`、裸 Telegram 用户名、`t.me/...` 或 `telegram.me/...` 时归一化为 HTTPS Telegram 链接。
- 配置为 `https://shiyi4` 这类 Telegram 不接受的无效 URL 时，兜底为 `https://t.me/sy168`。
- 配置读取异常或未找到客服链接时，也使用 `https://t.me/sy168`。

### 验证

通过：

```bash
uv run python -m py_compile bot/keyboards.py bot/tests.py
uv run python manage.py check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_support_contact_button_falls_back_when_config_url_is_invalid_for_telegram bot.tests.RetainedIpRenewalUiTestCase.test_support_contact_button_normalizes_telegram_username bot.tests.RetainedIpRenewalUiTestCase.test_support_contact_button_uses_default_when_config_load_fails --keepdb --noinput --verbosity 1
```

## 2026-06-10 并行安装 / 重装 / 修改配置真机压测

### 授权与隔离

- 授权：用户已明确授权真实云资源成本，目标资源使用最小套餐，并要求测试后删除服务器。
- 云厂商：AWS Lightsail，区域 `ap-southeast-1`。
- 云账号：后台账号 `#55`。
- 套餐：`#131`，`实机测试 Nano`，`nano_3_0`。
- 压测数据库：新建独立 SQLite，不使用业务库。
  - 首轮：`.shop-load-tests/shop-loadtest-realmachine.sqlite3`
  - 修复后复测：`.shop-load-tests/shop-loadtest-realmachine-rerun.sqlite3`
- 本地测试用户：`tg_user_id=930000610001`。
- 报告 JSON：
  - `.shop-load-tests/real-machine-parallel-install-report.json`
  - `.shop-load-tests/real-machine-parallel-install-report-rerun.json`
- 敏感信息：未记录云账号密钥、登录密码、代理 secret、完整公网 IP 或完整代理链接。

### 首轮压测与锁问题

- 时间：2026-06-10 17:04-17:12 CST。
- 范围：并行提交 5 个创建安装任务，随后进入 3 轮脚本流程。
- 结果：
  - 5 个创建请求均已真实提交。
  - 其中 1 个在固定 IP 分配阶段达到 AWS 配额限制。
  - 其余实例进入 BBR 后，在 MTProxy 阶段失败。
- 发现问题：远端同机安装锁使用 `/tmp/shop-cloud-bootstrap.lock`，BBR 与 MTProxy 阶段权限不一致，MTProxy 报 `Permission denied`。
- 修复：
  - 远端锁文件先通过 root/sudo 预创建并 `chmod 0666`。
  - 使用追加方式打开锁文件后再 `flock`，避免锁文件所有者导致下一阶段无法打开。
  - 该锁只限制同一台服务器上的安装任务，不限制多个服务器并行安装。
- 清理：首轮测试实例和固定 IP 均执行删除/释放；AWS 只读复核显示测试前缀实例和固定 IP 为空。

### 修复后复测

- 时间：2026-06-10 17:14-17:28 CST。
- 第 1 轮：并行提交 5 个创建安装任务。
  - 3 台创建、固定 IP 绑定、BBR、MTProxy 主/备用、Telemt、SOCKS5 安装成功。
  - 2 台因 AWS 固定 IP 配额限制失败，进入清理流程。
  - 成功服务器公网 IP 脱敏：`47.130.xxx.xxx`、`13.229.xxx.xxx`、`52.220.xxx.xxx`。
  - SOCKS5 链路已输出为 `https://t.me/socks?server=...&port=9534&user=***&pass=***`。
- 第 2 轮：对已完成服务器并行触发重装、重建、修改配置。
  - 重装入口被当前 `reprovision_cloud_server_bootstrap()` 按 `completed` 状态跳过，记录为现有行为。
  - 重建迁移创建新实例 `20260610***5-o6`，固定 IP 迁移后安装成功。
  - 修改配置迁移创建新实例 `20260610***0-o7`，固定 IP 迁移后安装成功。
- 第 3 轮：释放一个名额后再次触发创建和重装。
  - 新创建因固定 IP 配额仍为满额被拦截。
  - 重装入口再次按 `completed` 状态跳过。

### 复测发现并修复的问题

- 迁移安装成功后，保存新 `CloudAsset` 时发现 `public_ip` 唯一约束冲突。
- 原因：replacement 订单保存新资产时，源订单资产仍占用固定 IP；源资产的旧机临时 IP 状态在后续才更新。
- 修复：replacement 订单保存资产前，如果源资产仍占用该固定 IP，先将源资产标记为旧机保留、清空 `public_ip` 并保留 `previous_public_ip`，再保存新订单资产。
- 新增聚焦测试：`test_mark_success_replacement_releases_source_asset_public_ip_before_asset_upsert`。

### 清理与残留复核

- 自动清理：压测脚本删除并释放了 `LOAD...` 测试订单下的实例和固定 IP。
- 补充清理：脚本未自动覆盖 `SRVREBUILD...` / `SRVUPGRADE...` 迁移订单，已按测试实例名前缀手动补充删除：
  - `20260610***0-o7`
  - `20260610***5-o6`
- AWS 只读复核：测试前缀 `20260610-930000610001` 下实例列表为空，固定 IP 列表为空。

### 验证

通过：

```bash
uv run python -m py_compile cloud/bootstrap.py cloud/provisioning.py cloud/services.py cloud/api_orders.py cloud/api_assets.py bot/handlers.py bot/api.py
uv run python manage.py check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_mtproxy_script_runs_mtg_with_fake_tls_secret cloud.tests.CloudServerServicesTestCase.test_extract_proxy_links_labels_custom_low_port_plan cloud.tests.CloudServerServicesTestCase.test_compact_proxy_install_note_removes_raw_links cloud.tests.CloudServerServicesTestCase.test_cloud_asset_note_appends_clean_install_summary cloud.tests.CloudServerServicesTestCase.test_mark_success_replacement_releases_source_asset_public_ip_before_asset_upsert --keepdb --noinput --verbosity 1
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_created_text_includes_socks5_proxy_link bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_created_text_recovers_socks5_from_install_note bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_created_text_does_not_use_socks5_as_one_click bot.tests.RetainedIpRenewalUiTestCase.test_proxy_links_text_converts_socks5_to_telegram_link --keepdb --noinput --verbosity 1
```

## 2026-06-10 未附加 IP 续费与计划页真库实测

- 状态：先复现问题，已修复并重测通过。
- 授权：用户明确要求“跑真机测试，不要猜”，并已授权真实创建/删除服务器。
- 测试数据库：`shop_manual_20260608_5676`，MySQL `127.0.0.1:3307`
- 前端：`http://127.0.0.1:5666`
- 后端：`http://127.0.0.1:8000`
- 测试资产：`CloudAsset #556`
- 资源脱敏：公网 IP `18.138.xxx.xxx`，固定 IP 名 `StaticIp-707`

### AWS 真机创建阻塞

- 使用项目真实开通入口创建测试订单 `REALTEST...`。
- 系统按云账号轮询尝试 4 个启用 AWS 账号。
- 4 个账号在创建前真实配额检查阶段全部返回 `UnrecognizedClientException`。
- 随后对现有未附加固定 IP 调用 AWS `GetStaticIp` 做只读校验，同样返回 `UnrecognizedClientException`。
- 结论：当前后台 AWS 凭据无效或过期，无法完成云端真实创建、删除、查询闭环；本轮没有成功创建云服务器，也没有产生云资源成本。

### 真实库未附加 IP 续费复现

- 选择真实库中未绑定订单、未绑定用户的未附加固定 IP 资产 `#556`。
- 续费前后端查询：
  - IP 删除计划：包含目标 IP。
  - IP 删除历史：不包含目标 IP。
- 发起未附加 IP 续费：
  - 选择 AWS 新加坡最低价套餐。
  - 输入旧代理链接测试数据。
  - 生成待支付续费恢复订单 `SRVASSET...RENEW556`。
- 续费后后端查询：
  - IP 删除计划：不再包含目标 IP。
  - IP 删除历史：仍不包含目标 IP。

### 页面实测暴露的问题

- 实际打开本地计划页 `/admin/tasks/plans`。
- 修复前页面仍显示目标 IP，但位置不是 IP 删除计划，而是“关机计划”。
- 根因：
  - 目标资产是未附加固定 IP，但本地资产残留旧 `instance_id`。
  - 服务器计划查询只排除了“空实例 ID + 未附加 IP”。
  - 因此这类脏资产会被误当成服务器，进入关机计划。

### 修复

- 服务器生命周期计划改为：只要识别为未附加/保留固定 IP，就禁止进入关机计划和删机计划，不再依赖 `instance_id` 是否为空。
- 未附加 IP 已绑定待支付/已支付/开通中/待续费的“未绑定代理资产续费”订单时，继续排除在 IP 删除计划之外。
- 未附加 IP 续费下单后刷新计划页快照。

### 修复后重测

- 重启本地 `8000` 后端。
- 重新打开 `/admin/tasks/plans`。
- 浏览器 DOM 检查：
  - 目标 IP 出现次数：`0`
  - 目标 IP 不在关机计划。
  - 目标 IP 不在 IP 删除计划。
- 浏览器内 API 检查：
  - `shutdown_plan_items` 不包含目标 IP。
  - `ip_delete_plan_items` 不包含目标 IP。
  - 页面显示 `IP删除计划（44）`。
- 截图：`output/playwright/real-unattached-renewal-plan-page-fixed.png`

### 清理

- 删除本轮测试订单 `REALTEST...` 和 `SRVASSET...RENEW556`。
- 删除本轮测试产生的本地失败资产和测试日志。
- 恢复 `CloudAsset #556`：
  - `order_id=None`
  - `user_id=None`
  - `price=None`
  - `mtproxy_port=None`
  - 未保留测试 secret。
- 未删除任何真实云资源；原因是 AWS 创建未成功。

### 本轮验证

通过：

```bash
uv run python -m py_compile cloud/lifecycle_plan_queries.py cloud/services.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_exclude_unattached_ip_with_stale_instance_after_recovery_order cloud.tests.CloudServerServicesTestCase.test_retained_unattached_deleted_status_asset_can_start_recovery_renewal cloud.tests.CloudServerServicesTestCase.test_unattached_ip_active_recovery_order_is_excluded_from_delete_plan cloud.tests.CloudServerServicesTestCase.test_unattached_ip_renewal_lists_recovery_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_retained_ip_after_server_delete_stays_in_ip_delete_plan --settings=shop.settings --verbosity=1
```

## 2026-06-10 17:15 CST 未附加 IP 残留旧实例 ID 计划页复测

### 背景

- 清理续费测试订单后，继续复测真实库资产 `CloudAsset #556`。
- 该资产是未附加固定 IP，但本地仍残留旧 `instance_id`。
- 目标：确认没有活跃续费订单时，它必须进入 IP 删除计划，不能进入关机/删机计划，也不能进入 IP 删除历史。

### 修复补充

- `unattached_ip_delete_active_queryset()` 不再要求 `instance_id` 为空。
- IP 删除计划尾页优化候选也改为按未附加/StaticIp 标识筛选，不再只扫描空实例 ID。
- IP 删除历史日志增加排除：如果日志所属资产仍在活跃 IP 删除计划内，该旧日志不再进入 IP 删除历史。
- 手动 IP 删除入口的执行窗口判断改为使用未附加 IP 识别函数，不再依赖空 `instance_id`。

### 真实库复核

- 数据库：`shop_manual_20260608_5676`
- 前端：`http://127.0.0.1:5666`
- 后端：`http://127.0.0.1:8000`
- 资产：`CloudAsset #556`，公网 IP 脱敏为 `18.138.xxx.xxx`。
- 后端查询层结果：
  - 关机/删机计划：不包含目标资产。
  - IP 删除计划：包含目标资产。
  - IP 删除历史日志：不包含目标资产。

### 页面实测

- 已重启本地 8000 后端，实际打开 `/admin/tasks/plans`。
- 页面接口返回 `200`。
- 浏览器内同源接口核对：
  - `shutdown_plan_items` 不包含 `asset_id=556`。
  - `server_delete_items` 不包含 `asset_id=556`。
  - `ip_delete_plan_items` 包含 `asset_id=556`。
  - `ip_delete_history_items` 不包含 `asset_id=556`。
- 曾出现一次 `ip_delete_history_has_556=true` 的误判，原因是历史日志自身 `id=556` 与资产 ID 撞号；改为只按 `asset_id` 判断后确认历史表不包含目标资产。
- 截图：`output/playwright/real-unattached-ip-stale-instance-fixed.png`

### 验证

通过：

```bash
uv run python -m py_compile cloud/lifecycle_plan_queries.py bot/api.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_stale_instance_unattached_ip_stays_in_ip_delete_plan cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_exclude_unattached_ip_with_stale_instance_after_recovery_order cloud.tests.CloudServerServicesTestCase.test_unattached_ip_active_recovery_order_is_excluded_from_delete_plan cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_retained_ip_after_server_delete_stays_in_ip_delete_plan cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_skip_assets_attached_to_instance --settings=shop.settings --verbosity=1
git diff --check
```

## 2026-06-11 00:37 CST 美国区服务器创建失败原因定位与真机验证

- 状态：已定位线上失败原因，并完成 1 台美国区真机创建、安装和清理。
- 授权：用户明确回复“确认”，授权本轮真实云资源成本。
- 测试数据库：`shop_manual_20260608_5676`，MySQL `127.0.0.1:3307`
- 云厂商：AWS Lightsail
- 云账号：后台 AWS 云账号 `#55`
- 地区：`us-east-1`
- 套餐：`nano_3_0`，本轮测试套餐 `#16`，名称 `真机测试 US Nano`
- 测试用户：`TelegramUser #16254`，`codex_us_real_machine_test`

### 失败复现

- 订单：`#914` / `REALUS061016302424873`
- 现象：业务开通入口在创建前返回“没有可用的后台云账号”，订单进入失败/删除清理状态。
- 云端结果：未创建真实实例，清理复核云端不存在对应实例。
- 根因：账号 `#55` 的 `region_hint` 仍只有 `ap-southeast-1`，购买美国区时账号候选函数按 `region_hint` 过滤，导致候选账号为空。
- 关键判断：同一账号直接调用 AWS Lightsail 只读接口，`us-east-1`、`us-east-2`、`us-west-2` 均可正常返回实例列表，说明不是凭据不可用，而是本地账号区域候选数据过窄。

### 修复验证

- 本轮先在测试库将账号 `#55` 的 `region_hint` 补充为包含 `us-east-1/us-east-2/us-west-2`，重新走业务开通入口。
- 成功订单：`#915` / `REALUS061016325878029`
- 云实例：`20260610-************-*-o915`
- 固定 IP 名称：`20260610-************-*-o915-ip`
- 公网 IP：`54.205.xxx.xxx`
- 本地资产：`#721`
- 创建结果：AWS Lightsail 美国区实例创建成功，固定 IP 分配绑定成功。
- 安装结果：BBR、MTProxy 主/备用/Telemt、SOCKS5 安装成功。
- SOCKS5 格式：订单保存的 SOCKS5 链接已包含 `https://t.me/socks?server=` 格式；未记录完整链接、用户名或密码。
- 本地完成状态：创建完成后订单为 `completed`，资产为 `running/is_active=True`，到期事实为 `CloudAsset.actual_expires_at`。

### 测试资源清理

- 清理方式：通过现有 AWS 生命周期删除函数删除实例，再释放固定 IP，并调用本地标记函数更新订单/资产状态。
- 删除实例结果：通过，云端实例已不存在。
- 释放固定 IP 结果：通过，云端固定 IP 已不存在。
- 最终本地状态：订单 `#915` 为 `deleted`，资产 `#721` 为 `deleted/is_active=False`，订单当前公网 IP、固定 IP 名称和实例标识已清空。
- 配置恢复：测试临时打开的 `cloud_server_delete_enabled` 和 `cloud_ip_delete_enabled` 已恢复为测试前值。

### 代码修复

- `core.cloud_accounts.cloud_account_supports_region()` 增加状态备注区域兜底：当旧 `region_hint` 不包含目标区域时，会从最近一次云同步/验证的 `status_note` 中解析已确认地区。
- 线上已有“同步完成，地区 ... us-east-1,us-east-2,us-west-2 ...”但 `region_hint` 仍过窄的账号，将不再被美国区购买流程误过滤。
- 新增回归测试覆盖：旧 `region_hint=ap-southeast-1`、`status_note` 已确认 `us-east-1` 时，美国区候选账号必须可用。

## 2026-06-11 01:15 CST AWS 全区域真实创建资源测试

- 状态：已按用户要求实际创建资源测试全部 AWS Lightsail 区域，并清理测试实例。
- 授权：用户明确要求“实际创建资源”。
- 云账号：后台 AWS 云账号 `#55`
- 测试方式：每个区域创建 1 台最小规格测试实例，实例进入 `running` 后立即删除；不安装代理，不分配固定 IP。
- 初始套餐：`nano_3_0`
- 镜像：`debian_12`
- 资源前缀：`codex-region-********`

### `nano_3_0` 创建结果

- 成功区域：`ap-northeast-1`、`ap-northeast-2`、`ap-southeast-1`、`ca-central-1`、`eu-central-1`、`eu-north-1`、`eu-west-1`、`eu-west-2`、`eu-west-3`、`us-east-1`、`us-east-2`、`us-west-2`。
- 失败区域：
  - `ap-south-1`：区域可访问，但 `nano_3_0` 在该区域不存在。
  - `ap-southeast-2`：区域可访问，但 `nano_3_0` 在该区域不存在。
  - `ap-southeast-3`：`UnrecognizedClientException`，该账号在该区域不可用。
  - `ap-southeast-5`：`UnrecognizedClientException`，该账号在该区域不可用；该区域原本不在 AWS 业务区域表中。

### 区域专属套餐复测

- `ap-south-1`：查询区域可用 bundle 后选择 `nano_3_1`，真实创建成功并删除。
- `ap-southeast-2`：查询区域可用 bundle 后选择 `nano_3_2`，真实创建成功并删除。
- `ap-southeast-3`：查询 bundle 即返回 `UnrecognizedClientException`，确认不可用。
- `ap-southeast-5`：查询 bundle 即返回 `UnrecognizedClientException`，确认不可用。

### 清理结果

- 所有成功创建的测试实例均已提交删除。
- 残留复核：按 `codex-region-` 前缀扫描可访问区域，测试实例残留数量为 `0`。
- 未记录完整实例名、公网 IP、云资源 ID、密钥、密码或代理 secret。

### 代码处理

- 从 `cloud.services.AWS_REGION_NAMES` 移除 `ap-southeast-3`。
- 从 `bot.keyboards._COMPACT_REGION_CODES` 移除 `ap-southeast-3` 的 callback 压缩映射。
- 保留 `ap-south-1` 和 `ap-southeast-2`：这两个区域真实创建成功，只是需要区域专属 bundle，不属于区域不可用。

## 2026-06-12 12:36 CST AWS 固定 IP 解绑后代理列表和到期时间真机测试

- 状态：通过。
- 授权：用户明确授权真实云资源成本，并要求真实把固定 IP 从实例解绑。
- 云厂商：AWS Lightsail
- 云账号：后台 AWS 云账号 `#55`
- 地区：`ap-southeast-1`
- 套餐：`nano_3_0`
- 镜像：`debian_12`
- 测试资源前缀：`codex-detach-********`
- 公网 IP 脱敏：`47.131.xxx.xxx`
- 本地资产：`CloudAsset #39`

### 测试过程

1. 创建独立测试实例。
2. 分配独立固定 IP。
3. 将固定 IP 绑定到测试实例。
4. 定向运行 AWS 同步，确认本地资产和代理列表快照显示为服务器。
5. 写入测试用服务器到期时间，用于验证后续是否会被未附加 IP 删除计划时间替换。
6. 真实调用 AWS `detach_static_ip`，将固定 IP 从实例解绑。
7. 按固定 IP 定向运行 AWS 同步。
8. 校验本地资产事实和代理列表快照。

### 验证结果

- 解绑前：
  - 资产存在 `instance_id`。
  - 快照 `resource_kind_label=服务器`。
  - 快照 `is_unattached_ip=false`。
- 解绑后：
  - 同一资产清空 `instance_id`。
  - 资源标识变为 StaticIp 类型。
  - `provider_status=未附加固定IP`。
  - `status=unknown`。
  - `is_active=False`。
  - `CloudAsset.actual_expires_at` 重算为未附加 IP 删除计划时间。
  - 快照 `resource_kind=unattached_ip`。
  - 快照 `resource_kind_label=未附加IP`。
  - 快照 `is_unattached_ip=true`。
  - 快照排序时间与资产到期事实一致。

### 清理结果

- 已提交删除测试实例。
- 已释放测试固定 IP。
- 清理后只读复核：
  - 测试实例不存在。
  - 测试固定 IP 不存在。
- 本地测试资产已标记为 `deleted/is_active=False`，公网 IP 清空，并刷新快照。

### 结论

- 真实 AWS 链路验证通过：固定 IP 从绑定实例变为未附加后，再运行同步会更新 `CloudAsset.actual_expires_at`，代理列表快照也会从“服务器”更新为“未附加IP”。
- 未记录完整实例名、固定 IP 名、公网 IP、ARN、密钥、密码或代理 secret。
