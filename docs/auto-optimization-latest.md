# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 16:18 CST
- 状态：完成代理列表风险标签深页排序修复收口验证，并追加机器人 `callback_data` 长度专项巡检。
- Commit：本轮记录随本轮提交一起保存。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`（仅检查 git 状态，当前无改动）
- 固定巡检项：
  - 代理列表风险标签深页排序与分页真实性
  - 机器人返回链 / `callback_data <= 64` 字节
  - 并发后台任务隔离

## 本轮修复与审计

- `cloud/api_asset_snapshots.py`
  - 风险标签默认排序改为复用快照表已有组合索引，避免在 `cloud_asset_dashboard_snapshot` 64 索引上限下继续堆索引。
  - `normal`、`due_soon`、`expired`、`unattached_ip`、`abnormal`、`unbound_user`、`unbound_group` 统一走：
    - `asset_due_sort_null_rank`
    - `asset_due_sort_at`
    - `group_user_label`
    - `group_user_key`
    - `-asset_id`
  - `shutdown_disabled`、`auto_renew_off` 统一走：
    - `group_telegram_key`
    - `group_telegram_label`
    - `-asset_id`
- `cloud/tests.py`
  - 将原未绑定标签排序测试扩展为全风险标签排序测试，锁定“复用已有索引”的实现契约。
- `bot/keyboards.py` / `bot/tests.py`
  - 本轮未改代码。
  - 追加只读专项巡检，确认极端订单详情回跳、资产详情回跳、IP 查询动作回跳、自动续费回跳仍保持 `callback_data` 长度不超过 `64` 字节。

## 重要发现

- 真实 MySQL 的 `cloud_asset_dashboard_snapshot` 已达到单表 `64` 个索引上限。
- 本轮未再尝试真实库新增索引；沿用上一轮结论，采用“改排序复用现有索引”而非继续堆索引。
- `uv run python manage.py makemigrations --check --dry-run` 在当前沙箱内会提示无法连 `127.0.0.1:3306` 检查迁移历史，但命令返回 `No changes detected`，未发现模型漂移。

## 压测与回归规模

- 真实 MySQL 风险标签分页复核规模：总资产约 `2489998`，继续覆盖第 `1` 页、第 `2` 页、深页与末页候选页。
- 机器人并发回归规模：`60` 路后台任务（`20` 组钱包直付、`20` 组钱包补付、`20` 组续费后巡检）。
- 回调长度专项：覆盖极端长 ID、订单详情嵌套返回链、资产详情嵌套返回链、IP 查询动作返回链、自动续费返回链。

## 数据对账与结论

- 风险标签排序修复对应的聚焦用例全部通过，保留分页真实性契约：
  - `test_cloud_assets_risk_ordering_uses_existing_page_indexes`
  - `test_cloud_assets_paginated_uses_true_database_pages`
  - `test_cloud_assets_list_compact_returns_ip_view_payload`
- 机器人返回链和回调长度专项通过，未发现 `order-xxx/asset-xxx` 混合主键回流，也未发现超 `64` 字节的 `callback_data`：
  - `test_cloud_server_list_order_detail_uses_short_back_callback`
  - `test_asset_detail_callback_from_extreme_order_detail_stays_under_limit`
  - `test_asset_detail_callback_recompacts_nested_asset_detail_back_path`
  - `test_cloud_ip_query_actions_return_to_query_menu`
  - `test_cloud_auto_renew_callbacks_keep_nested_back_under_limit`

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_risk_ordering_uses_existing_page_indexes cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_uses_true_database_pages cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_compact_returns_ip_view_payload bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_list_order_detail_uses_short_back_callback bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_from_extreme_order_detail_stays_under_limit bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_recompacts_nested_asset_detail_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu bot.tests.RetainedIpRenewalUiTestCase.test_cloud_auto_renew_callbacks_keep_nested_back_under_limit bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_asset_snapshots.py cloud/tests.py bot/keyboards.py bot/tests.py
git diff --check
```

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥或完整代理链接。

## 剩余风险

- 分组视图下的用户分组 / 群组分组深分页尚未做真实浏览器跳页复核，目前只确认非分组 IP 视图稳定。
- SQLite 聚焦测试仍会打印大量 `db_comment` 告警；它们不是本轮回归失败，但会抬高测试日志噪音。
- `makemigrations --check --dry-run` 在沙箱内无法直连本地 MySQL 迁移历史，仍需依赖现有模型无漂移结论与 SQLite 聚焦回归交叉确认。

## 下一步

- 优先巡检代理列表分组视图下的用户分组 / 群组分组深分页与跳页真实性。
- 继续把机器人返回链和 `callback_data` 64 字节限制作为每轮固定回归项。
- 继续把机器人多任务高并发作为固定回归项。
