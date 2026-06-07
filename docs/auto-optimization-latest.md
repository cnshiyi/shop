# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 00:34 CST
- 状态：完成生命周期真实创建/删除链路复核、资产详情页单项开关真页点击测试，并修复发现的问题。
- 本轮范围：订单 `#50097`、资产 `#1500333` 的生命周期终态对账；计划页、订单详情页、资产详情页真实浏览器验证；资产关机/删机/IP 删除三个单项开关逐项点击关闭再恢复。

## 覆盖结果

- 生命周期创建与删除链路：
  - 订单 `#50097` 当前为 `deleted`，资产 `#1500333` 当前为 `deleted/is_active=False`。
  - 生命周期任务 `suspend/delete/recycle` 均为 `done`，无 `last_error`。
  - 资产实例 ID、公网 IP 等执行后清理字段已清空或脱敏显示；未打印密钥、代理链接、代理 secret、登录密码或云账号密钥。
- 计划页真实验证：
  - 实际打开 `/admin/tasks/plans`，标题为“计划”。
  - 页面显示关机计划、删除计划、IP 删除计划、服务器删除历史记录、IP 删除历史记录。
  - 控制台 error / warning 为 0。
- 订单详情页真实验证：
  - 实际打开 `/admin/cloud-orders/50097`，标题为“云订单详情”。
  - 页面显示已删除、生命周期区域，以及创建、关机、删机、IP 释放相关记录。
- 资产详情页真实验证：
  - 实际打开 `/admin/cloud-assets/1500333`，标题为“代理详情”。
  - 页面显示已删除、生命周期日志、关联订单。
  - 关机计划、删除计划、IP 删除计划三个资产单项开关均显示。
  - 三个单项开关均已通过真实页面点击测试；最终数据库确认三项均恢复为 `True`。
  - 控制台 error / warning 为 0。

## 发现与修复

- 后端修复：
  - `cloud/api_assets.py` 的资产详情 payload 原来只返回 `shutdown_enabled`，缺少 `server_delete_enabled` 和 `ip_delete_enabled`。
  - 这会导致页面刷新后把服务器删除计划和 IP 删除计划错显为默认开启。
  - 已补齐两个字段，并新增聚焦测试 `test_cloud_asset_detail_exposes_lifecycle_switches`。
- 前端修复：
  - 资产详情页补齐“删除计划”和“IP 删除计划”两个单项开关，并复用资产更新接口保存。
  - 计划页“IP删除历史记录”改为“IP 删除历史记录”，避免与删除计划/记录混淆。
  - 时区按钮和布局折叠按钮从外联 Iconify 图标改为本地 lucide 组件，消除 `api.unisvg.com` 控制台错误。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_exposes_lifecycle_switches cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_exposes_related_order_click_path --settings=shop.settings --verbosity=1
/Users/a399/.homebrew/bin/pnpm --filter @vben/web-antd typecheck
```

SQLite `db_comment` 警告仍是已知数据库能力差异。

## 红线

- 本轮没有新建第二台真实云服务器；复用上一轮已授权、已清理的真实订单 `#50097` 和资产 `#1500333` 做终态和页面链路复核。
- 本轮未执行真实支付、链上广播、生产发布或删除业务压测数据。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险与下一轮

- 继续做计划页和代理列表深分页真页对账，重点验证跳页、末页和数据库精确排序一致。
- 继续关注生命周期计划页高数据量下加载速度，同时保证分页不丢数据、不重复。
