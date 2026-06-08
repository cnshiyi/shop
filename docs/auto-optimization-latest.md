# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 18:55 CST
- 状态：完成代理列表逐标签 10 万级压测收尾；已覆盖后端数据库精确对账、真实前端逐标签点击、翻页和跳页观测，并完成清理与验证。
- 后端 Commit：待提交。
- 前端 Commit：本轮无前端变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 重点：
  - 代理列表全部标签 10 万级以上数据量口径。
  - 每个标签第 1 页、第 2 页、第 5000 页、最后页数据库对账。
  - 真实前端逐标签点击、下一页、跳转第 5000 页。
  - 控制台错误、业务 API 失败、分页 loaded/total 返回值。

## 已完成压测

代理列表标签已完成后端精确对账和真实前端点击：

- `all`：`2,489,998`
- `normal`：`549,988`
- `due_soon`：`101,250`
- `expired`：`101,752`
- `unattached_ip`：`100,001`
- `abnormal`：`100,000`
- `account_disabled`：`1,145,002`
- `shutdown_disabled`：`100,384`
- `unbound_user`：`100,001`
- `unbound_group`：`100,013`
- `auto_renew_off`：`104,558`

对账方法：

- 后端请求 `/api/admin/cloud-assets/?paginated=1&compact=1&risk_status=...&page=...&page_size=20`。
- 数据库口径使用 `CloudAssetDashboardSnapshot`、`_filter_dashboard_snapshots_by_risk` 和 `_dashboard_snapshot_ordering`。
- 页位覆盖第 `1` 页、第 `2` 页、第 `5000` 页、最后页。
- `total`、`loaded`、资产 ID 顺序均与数据库一致，未发现丢数据、串页或排序不一致。

## 真实前端验证

实际打开：

```text
http://127.0.0.1:5666/admin/cloud-assets
```

验证动作：

- 点击代理列表全部标签。
- 每个标签点击下一页。
- 每个标签跳转第 `5000` 页。
- 观察业务接口、控制台错误和加载结果。

结果：

- 全部标签 API 返回 `200`。
- 每个标签第 `5000` 页 loaded 均为 `20`。
- 控制台 error/warning：`0`。
- 业务 API 失败：`0`。
- 浏览器层 `requestfailed` 为 Vite 开发环境脚本 `net::ERR_ABORTED`，非业务 API。

截图：

```text
/private/tmp/shop-cloud-assets-tags-10w-current.png
```

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_uses_true_database_pages cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_uses_true_database_pages_for_telegram_group_sort --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过。命中项为既有测试桩、Telegram 登录账号 API 文件名，以及 `CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实回流。

## 清理

- 已删除临时后台登录用户：
  - `codex_patrol_assets_front_probe`
  - `codex_patrol_assets_tag_probe`
- 已删除 `/private/tmp/shop_assets_front_probe_token.txt`。

## 尚未压测完

- 通知计划还没有单独构造 10 万级可清理通知分组压测数据；现有通知页只完成当前库规模真实性巡检。
- 机器人还没有完成多任务高并发真机点击压测；已做过返回链和 `callback_data` 聚焦复查，但还不是高并发真机覆盖。
- 真实云资源创建、关机、删机、释放 IP 的生命周期开关矩阵还没有完整跑完；已有真实机器测试记录显示一台测试 AWS 实例仍运行中，未执行破坏性生命周期动作。
- 服务器创建后的完整生命周期链路还没有在本轮压测中闭环到真实关机、真实删机和真实 IP 释放。
- 任务页面仍需继续关注 IP 删除计划最后页约 `1.5` 秒的加载耗时。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥或完整代理链接。
- `docs/real-machine-test-report.md` 当前有未提交的真实机器测试记录，本轮不覆盖、不提交该脏文件。

## 下一步

- 继续按 10 万级推进，不再做百万级压测。
- 优先补通知计划 10 万级可清理数据压测。
- 继续补机器人多任务高并发真机点击压测。
- 继续补真实生命周期关机、删除、IP 删除开关组合闭环测试，并对真实资源 ID 脱敏记录。
