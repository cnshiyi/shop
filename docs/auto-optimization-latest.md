# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 04:11 CST
- 状态：完成机器人回调链与 `callback_data` 长度约束只读专项巡检，无业务代码改动。
- 本轮范围：后端/前端 git 基线、机器人资产/订单/续费/换 IP/重装/修改配置返回链测试、`callback_data <= 64 bytes` 回归、基础检查、迁移计划连通性、红线扫描。

## 发现与结论

发现：

- `TODO.md` 中可执行任务已全部完成，本轮按固定巡检清单执行只读专项。
- `uv run python manage.py check` 通过。
- `uv run python manage.py migrate --plan` 仍因沙箱禁止访问 `127.0.0.1:3306` 失败，无法验证默认 MySQL 计划，只能继续依赖 SQLite 聚焦测试和静态巡检。
- 前端仓库 `/Users/a399/Desktop/data/vue-shop-admin` 本轮 `git status --short` 为空，未发现未提交前端改动。
- 机器人回调专项中，最初误用了不存在的测试类 `bot.tests.BotCallbackContractTestCase`；实际承载云资产/订单返回链与回调长度约束的是 `bot.tests.RetainedIpRenewalUiTestCase`。

结论：

- `RetainedIpRenewalUiTestCase` 共 `49` 个测试全部通过。
- 已覆盖资产详情、订单详情、续费支付、换 IP、重装、修改配置、自动续费、只读订单详情、分页返回链等高风险回调路径。
- 现有压缩回调契约仍然生效，测试中所有相关 `callback_data` 都满足 Telegram `64` 字节上限。
- 红线扫描未发现运行时代码回流 `service_expires_at`、旧计划快照、旧退款函数名或废弃 runtime app 导入；`service_expires_at` 命中仅存在于历史 migrations。

## 机器人回调专项验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py orders/payment_scanner.py cloud/resource_monitor.py
git diff --check
```

测试覆盖结论：

- 资产详情返回代理列表/订单列表。
- 订单详情返回订单列表。
- 续费钱包支付返回详情。
- 换 IP 区域选择/提交返回详情。
- 重装确认/提交返回详情。
- 修改配置/自动续费二级动作返回详情。
- 极长 ID、深层嵌套回调、压缩回调别名路径均保持在 64 字节以内。

## 受限项

- 本轮未做真实 Telegram 账号交互：当前自动化环境未提供可安全使用的本地登录会话或测试账号，且红线禁止打印 session/token/TOTP/密钥。
- 本轮未做真实浏览器点击：本轮聚焦对象是机器人回调链，前端仓库也无新改动；页面级真实巡检留给下一轮继续覆盖代理列表/计划页。
- 本轮未做默认 MySQL 数据库对账：`127.0.0.1:3306` 访问仍被沙箱拦截。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。
- 本轮未打印 Telegram token、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。

## 下一步

- 下一轮优先恢复真实页面巡检：继续覆盖 `/admin/cloud-assets` 与 `/admin/tasks/plans`，并检查控制台、翻页和返回链。
- 如果环境允许本机 MySQL，补跑 `uv run python manage.py migrate --plan` 和相关实库对账。
- 继续关注机器人真实交互验证入口，若存在脱敏测试账号或本地安全会话，可补一轮真实菜单/回调链路验证。
