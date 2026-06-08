# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 15:40 CST
- 状态：完成代理列表未绑定用户/未绑定群组慢标签排序优化，完成真实前端翻页和数据库对账。
- Commit：本轮记录随本轮提交一起保存。

## 本轮修复

- `cloud/api_asset_snapshots.py`
  - `_dashboard_snapshot_ordering()` 增加 `risk_status` 参数。
  - 在 `unbound_user` 和 `unbound_group` 标签的默认排序下，改用已有组合索引友好的顺序：
    - `asset_due_sort_null_rank`
    - `asset_due_sort_at`
    - `group_user_label`
    - `group_user_key`
    - `-asset_id`
  - 显式到期排序仍保持原有排序契约。
- `cloud/api_assets.py`
  - 代理列表分页调用排序 helper 时传入当前 `risk_status`。
- `cloud/tests.py`
  - 新增 `test_cloud_assets_unbound_risk_ordering_uses_due_group_index`，锁住未绑定标签的索引友好排序。

## 性能结果

真实 MySQL，IP 视图、普通分页、`page_size=20`：

- 修复前：
  - 未绑定用户：约 `3679ms`。
  - 未绑定群组：约 `3203ms`。
- 修复后：
  - 未绑定用户第 1 页：`947ms` 到 `1072ms`。
  - 未绑定用户第 2 页：`401ms`。
  - 未绑定用户第 1000 页：`580ms`。
  - 未绑定用户末页：`383ms`。
  - 未绑定群组第 1 页：`387ms` 到 `445ms`。
  - 未绑定群组第 2 页：`392ms`。
  - 未绑定群组第 1000 页：`582ms`。
  - 未绑定群组末页：`386ms`。

## 数据对账

- 未绑定用户：
  - 总数：`100001`。
  - 校验页：第 `1` 页、第 `2` 页、第 `1000` 页、第 `5001` 页。
  - API 返回 ID 与数据库同排序切片完全一致。
  - 抽样 `61` 条 ID，唯一数 `61`，无重复。
- 未绑定群组：
  - 总数：`100013`。
  - 校验页：第 `1` 页、第 `2` 页、第 `1000` 页、第 `5001` 页。
  - API 返回 ID 与数据库同排序切片完全一致。
  - 抽样 `73` 条 ID，唯一数 `73`，无重复。

## 真实前端结果

前端实际打开：

```text
http://127.0.0.1:5666/admin/cloud-assets
```

浏览器实测：

- 未绑定用户：
  - 第 1 页：DOM 行数 `20`，HTTP `200`。
  - 第 2 页：DOM 行数 `20`，HTTP `200`。
  - 第 1000 页：DOM 行数 `20`，HTTP `200`。
  - 第 5001 页：DOM 行数 `1`，HTTP `200`。
- 未绑定群组：
  - 第 1 页：DOM 行数 `20`，HTTP `200`。
  - 第 2 页：DOM 行数 `20`，HTTP `200`。
  - 第 1000 页：DOM 行数 `20`，HTTP `200`。
  - 第 5001 页：DOM 行数 `13`，HTTP `200`。
- 页面请求无失败，控制台 `0 error / 0 warning`。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_unbound_risk_ordering_uses_due_group_index cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_uses_true_database_pages cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_compact_returns_ip_view_payload --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_asset_snapshots.py cloud/api_assets.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过，命中项仍为 Telegram 登录账号、云账号测试桩和 `CloudServerOrder.ip_recycle_at` 同步语句，不是旧到期事实回流。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接。
- 本轮创建过一次性后台验证用户，提交前清理。

## 下一步

- 继续巡检代理列表其它标签和深页，确保排序优化没有隐藏边界。
- 继续检查生命周期计划、通知计划和机器人返回链。
- 继续把机器人多任务高并发作为固定回归项。
