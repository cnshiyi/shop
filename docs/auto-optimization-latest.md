# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 23:43 CST
- 状态：已修复代理列表四个主标签数量不能加总等于全部的问题。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户指出代理列表标签过滤中，`运行中`、`已过期`、`即将过期`、`未附加IP` 四项加起来应该等于 `全部`。
- 复查发现原 `risk_counts` 是多标签统计：同一资产可同时计入主生命周期标签和续费关闭、未绑定用户、云账号异常等辅助标签。
- 另外默认 `全部` 会过滤隐藏删除资产，但风险统计原来基于未过滤集合，导致默认显示口径和标签计数口径不完全一致。

## 修复内容

- `cloud/api_asset_snapshots.py`
  - 新增主生命周期标签集合：`normal`、`due_soon`、`expired`、`unattached_ip`。
  - 主标签过滤改为互斥口径：
    - `unattached_ip`：未附加固定 IP。
    - `expired`：非未附加 IP 且到期时间已过。
    - `due_soon`：非未附加 IP 且到期时间在 7 天内。
    - `normal`：非未附加 IP 且无到期时间或 7 天后才到期。
  - 主标签统计改为同一互斥查询口径；辅助标签仍按风险布尔字段统计。
- `cloud/api_assets.py`
  - 默认未打开“显示删除资产”时，`risk_counts` 和列表数据都基于 `is_display_visible=True`。
  - 风险摘要接口也使用默认可见资产口径，避免前端首屏标签数和列表默认显示不一致。
- `cloud/tests.py`
  - 新增四个主标签互斥覆盖测试，验证四项加总等于 `all`，且各标签实际返回资产与计数一致。
  - 调整云账号停用资产计数测试：云账号异常仍是辅助标签，主生命周期仍按到期时间归入运行中。
  - 调整旧风险过滤测试：运行中标签可以包含辅助风险文案，不再强制行文案必须是“运行中”。

## 验证

通过：

```bash
uv run python -m py_compile cloud/api_asset_snapshots.py cloud/api_assets.py cloud/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_risk_counts_keep_disabled_account_isolated cloud.tests.CloudServerServicesTestCase.test_cloud_assets_primary_filter_counts_partition_visible_assets cloud.tests.CloudServerServicesTestCase.test_cloud_asset_dashboard_risk_counts_do_not_use_single_aggregate cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_filters_by_risk_and_searches_asset_identifiers cloud.tests.CloudServerServicesTestCase.test_cloud_asset_expired_filter_excludes_unattached_ip_assets cloud.tests.CloudServerServicesTestCase.test_cloud_asset_unattached_filter_uses_raw_provider_status --settings=shop.settings --verbosity=1
uv run python manage.py check
git diff --check
```

结果：

- 编译通过。
- 聚焦测试 6 条通过。
- Django 系统检查通过。
- diff 空白检查通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 告警，不属于本轮问题。

## 结论

- 代理列表默认可见资产中，`运行中 + 即将过期 + 已过期 + 未附加IP = 全部`。
- 云账号异常、未绑定用户、未绑定群组、续费关闭、关机计划关闭等仍作为辅助标签保留，不再破坏四个主生命周期标签的加总关系。
