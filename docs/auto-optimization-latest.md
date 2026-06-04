# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-04 19:08 CST
- 状态：按用户授权执行真实 Telegram Bot 与真实 AWS Lightsail 购买、初始化、续费、重装、换 IP、旧机删除和旧固定 IP 释放；追加生命周期专项测试通过；完整测试套件已通过；发现并修复多处真实按钮无响应/异常问题。
- 最近提交：本轮未提交；当前工作树含本轮 `bot/handlers.py` 与文档更新，另有既存未归属脏文件未处理。
- 本轮范围：使用项目数据库中的已登录 Telegram 账号与测试 bot 实际发消息、点击按钮；使用项目数据库余额完成 USDT 与 TRX 钱包支付；创建并初始化真实 AWS Lightsail 测试服务器；执行真实重新安装、真实换 IP、新节点初始化、旧机删除、旧固定 IP 释放；验证个人中心、订单详情、IP 查询、自动续费开关、充值入口、充值记录、余额明细、提醒列表、地址监控、客服入口、续费、换 IP、重装、修改配置入口。
- 本轮结论：真实购买链路、USDT 续费、TRX 续费、重装、换 IP、新节点可用、旧 IP 不可续费、旧机删除和旧固定 IP 释放均已完成。最终活跃资产为 `#326`，订单 `#80` 为 `completed`，旧订单 `#79` 和旧资产 `#325` 已标记删除。修复了个人中心文本按钮漏处理、客服文本按钮漏处理、订单详情/提醒/续费/成功通知等 async handler 中同步查库导致的 `SynchronousOnlyOperation`。

## 最近验证

- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py` 通过。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test --settings=shop.settings --verbosity=1` 通过，519 个测试 OK。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests cloud.tests_task_center --settings=shop.settings --verbosity=1` 通过，383 个生命周期/任务中心相关测试 OK。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py refresh_lifecycle_plans` 通过，真实库计划结果：`due=0 future=2 history=3 ip_delete=3`。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py refresh_notice_plans` 通过，真实库通知计划结果：`due=1 future=2 history=7`。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python run.py bot` 已启动并进入 polling，bot 为 `@ceshiayan_bot`。
- 真实 Telegram 点击验证通过：`/start`、主菜单、购买节点、个人中心、我的订单、订单详情、继续初始化确认、IP 查询、自动续费开/关、续费钱包支付、充值入口、充值记录、余额明细、提醒列表、地址监控添加/列表/详情/删除、联系客服。
- 追加 IP 详情页按钮复核：`🌐 更换IP` 已最终确认并完成新节点创建；`🛠 重新安装` 已最终确认并完成；`⚙️ 修改配置` 返回“暂无可修改的配置”；`🔄 续费IP` 已完成 USDT 与 TRX 钱包支付路径。
- 真实云资源验证：订单 `#79` 创建过 AWS Lightsail 测试实例，初次 SSH 初始化失败后通过“继续初始化”恢复成功；资产 `#325` 绑定订单并保存 `CloudAsset.actual_expires_at`。
- 真实支付验证：测试用户 USDT 余额经购买和续费分别扣除 5 U；TRX 续费扣除 15.253 TRX；重复点击已支付续费按钮未再次扣款。
- 真实生命周期验证：换 IP 后新订单 `#80` / 新资产 `#326` 为完成/运行；旧订单 `#79` 迁移旧机删除成功，旧固定 IP 释放成功；新 IP 查询运行中，旧 IP 查询不可续费。
- 敏感信息处理：报告和总结不记录完整公网 IP、代理链接、secret、登录密码、Telegram token、session 或云账号密钥。

## 剩余风险

- 本轮没有发现可执行的“修改配置”实际变更项，真实点击返回“暂无可修改的配置”。
- 未执行链上真实充值到账，因为本轮没有外部钱包向收款地址发起真实链上转账；已覆盖充值入口、地址展示、充值记录和余额钱包支付/扣款流水。
- 用户端曾收到一条“重试初始化任务异常”的旧通知；根因已修复，订单和资产实际已完成。
- 本轮未提交 git commit；工作树中仍有本轮外既存脏文件和迁移文件，未归并处理。

## 下一步

- 如继续补充，唯一未真实链上完成的是外部钱包向充值地址转账后的到账扫描；需要真实链上转账来源。
- 可在存在可升级/可变更套餐时再补一次“修改配置”实际变更场景。
