# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 22:18 CST
- 状态：完成一轮后台订单详情安全巡检、修复、后端验证和真实页面验证。
- 本轮范围：已删除云订单详情 API、历史订单摘要字段、后台订单详情页真实浏览器检查。

## 本轮修复

- `cloud/api_orders.py`：
  - 已删除订单详情返回前统一脱敏历史公网 IP、MTProxy 主机、代理链路列表和创建说明。
  - 已删除订单的 `mtproxy_link` 和 `proxy_links` 不再通过后台详情 API 返回历史完整链路。
  - 创建说明中的 `tg://proxy`、`socks5://`、`secret=` 和公网 IP 会在响应层脱敏；数据库原始审计记录不被清空。
  - `history_orders` 中已删除订单摘要的公网 IP / 历史公网 IP 也同步脱敏，避免详情主体已脱敏但历史摘要仍带出历史 IP。
- `cloud/tests.py`：
  - 新增已删除订单详情脱敏回归测试，断言响应不包含完整代理链路、完整 secret、socks5 凭据、`secret=` 和完整公网 IP。

## 页面实测

- 实际打开 `/admin/cloud-orders/50096`：
  - 页面标题为“云订单详情”，状态显示“已删除”。
  - 页面正文不包含 `tg://proxy`、`socks5://` 或 `secret=`。
  - 页面正文包含脱敏提示文本，说明历史 secret 已被响应层收敛。
  - 控制台 error 为 0，warning 为 0。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_deleted_order_detail_masks_proxy_links_and_historical_ips cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_status_edit_syncs_primary_asset_status cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_orders.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
git diff --check
```

结果：Django 系统检查、3 个聚焦测试、编译检查和空白检查通过。SQLite `db_comment` 警告仍为已知测试库能力差异。

## 清理

- 本轮使用临时后台账号 `codex_ui_tester` 做页面验证，提交前会删除。
- 本轮启动本地 Django `runserver` 做页面验证，提交前会停止。
- 本轮 Playwright 临时目录 `.playwright-cli/` 提交前会删除；前端 Vite 为既有开发进程，未处理。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、真实支付、链上广播、生产发布、删除业务数据或删除测试库。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险与下一轮

- 继续执行不少于 4 小时的自动巡检目标，下一轮回到数据库口径、代理列表翻页对账、计划页 / 通知页数据真实性和前端真实页面检查。
- 已删除资产详情、IP 删除历史和服务器删除历史也应继续检查是否存在类似历史敏感字段展示面。
