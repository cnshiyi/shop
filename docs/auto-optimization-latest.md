# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 20:00 CST
- 状态：完成代理列表全部标签 10 万级以上分页压测，覆盖后端数据库对账和真实前端点击翻页。
- 后端 Commit：待提交。
- 前端 Commit：本轮无前端变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 前端页面：`http://127.0.0.1:5666/admin/cloud-assets`
- 后端接口：`/api/admin/cloud-assets/`
- 查询层：`cloud/api_asset_snapshots.py`
- 响应入口：`cloud/api_assets.py` 的 `cloud_assets_list`

## 当前数据规模

真实库当前代理资产规模：

- `CloudAsset` 服务器资产：`2500003`
- `CloudAssetDashboardSnapshot`：`2500003`

真实库当前标签计数：

- 全部：`2489998`
- 运行中：`549988`
- 即将到期：`101250`
- 已过期：`101752`
- 未附加固定IP：`100001`
- 异常/待确认：`100000`
- 云账号异常：`1145002`
- 关机计划关闭：`100384`
- 未绑定用户：`100001`
- 未绑定群组：`100013`
- 续费关闭：`104558`

## 后端数据库对账

本轮按前端真实页大小 `20` 条对账，直接比对 API 返回 `id` 与 `CloudAssetDashboardSnapshot` 服务端排序结果：

- 覆盖标签：
  - 全部、运行中、即将到期、已过期、未附加固定IP、异常/待确认、云账号异常、关机计划关闭、未绑定用户、未绑定群组、续费关闭。
- 覆盖页位：
  - 第 1 页、第 2 页、第 1000 页、最后页。
- 对账结果：
  - 11 个标签 API 顺序均与数据库快照排序一致。
  - 页内无重复。
  - 最后一页 `loaded` 与总数余数一致。
  - 未发现丢数据、串页或跳页错误。
- 后端耗时：
  - 多数标签单页约 `0.37s - 0.76s`。
  - 运行中标签约 `1.43s - 1.52s`，仍可用但后续可继续优化。

## 真实前端验证

真实打开：

```text
http://127.0.0.1:5666/admin/cloud-assets
```

前端实际点击每个标签，并分别进入第 1 页、第 2 页、最后页：

- 运行中：总数 `549988`，最后页第 `27500` 页，最后页 `8` 条。
- 即将到期：总数 `101250`，最后页第 `5063` 页，最后页 `10` 条。
- 已过期：总数 `101752`，最后页第 `5088` 页，最后页 `12` 条。
- 未附加固定IP：总数 `100001`，最后页第 `5001` 页，最后页 `1` 条。
- 异常/待确认：总数 `100000`，最后页第 `5000` 页，最后页 `20` 条。
- 云账号异常：总数 `1145002`，最后页第 `57251` 页，最后页 `2` 条。
- 关机计划关闭：总数 `100384`，最后页第 `5020` 页，最后页 `4` 条。
- 未绑定用户：总数 `100001`，最后页第 `5001` 页，最后页 `1` 条。
- 未绑定群组：总数 `100013`，最后页第 `5001` 页，最后页 `13` 条。
- 续费关闭：总数 `104558`，最后页第 `5228` 页，最后页 `18` 条。
- 全部：总数 `2489998`，最后页第 `124500` 页，最后页 `18` 条。

结果：

- 页面表格可见行数与 API `items.length` 一致。
- 业务 API 失败：`0`
- 控制台 error/warning：`0`
- request failed：`0`
- 截图：`/private/tmp/shop-cloud-assets-labels-10w-front.png`

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_assets.py cloud/api_asset_snapshots.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_uses_true_database_pages cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_uses_true_database_pages_for_telegram_group_sort cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_paginated_uses_twenty_user_groups_per_page cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_duplicate_groups_do_not_repeat_across_pages cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_risk_page_tolerates_old_snapshot_payload_missing_user_fields cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_compact_returns_ip_view_payload cloud.tests.CloudServerServicesTestCase.test_cloud_assets_risk_ordering_uses_existing_page_indexes --settings=shop.settings --verbosity=1
```

说明：

- SQLite 聚焦测试仍输出既有 `db_comment` / `db_table_comment` 警告，不属于本轮问题。
- 本轮临时前端登录 session 和 storageState 已清理。
- `.playwright-cli/` 临时产物已删除。

## 结论

- 代理列表 11 个标签均已达到或超过 10 万级压测，其中“全部”和“云账号异常”超过百万级。
- 标签切换、第 2 页、最后页和真实前端显示均正常。
- 本轮未发现需要修复的代理列表分页问题，没有代码变更。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播或生产发布。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥、有效登录 token 或完整代理链接。
- `docs/real-machine-test-report.md` 当前存在未提交真实机器测试记录，本轮不覆盖、不提交。

## 尚未完成

- 机器人多任务高并发真机点击压测还没有完成。
- 真实云资源创建、到期关机、删机、释放 IP 的生命周期开关矩阵还没有完整闭环。
- 服务器创建后的完整生命周期链路还没有在本轮压测中闭环到真实关机、真实删机和真实 IP 释放。
