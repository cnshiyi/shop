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
