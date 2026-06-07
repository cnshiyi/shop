# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 21:36 CST
- 状态：完成任务中心、通知计划、代理列表和生命周期计划页真实浏览器巡检；修复任务中心 Ant Design Vue 运行时告警。
- 本轮范围：前端真实页面、服务端分页口径、通知计划翻页、代理列表 150 万资产压力页、生命周期计划计数、后端聚焦测试、前端类型检查。

## 真机生命周期基线

- 上一轮已按用户授权完成真实 AWS Lightsail 创建服务器、关机、删除服务器和固定 IP 释放。
- 测试订单 `#50095`、测试资产 `#1500331` 最终均为已删除状态；实例、固定 IP、公网 IP 和 IP 回收时间均已清空。
- 本轮未再次创建或删除真实云资源，只复核生命周期计划页展示和服务端计数。

## 本轮修复

- 前端 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src/views/dashboard/tasks/index.vue`：
  - 任务中心说明列的 `TypographyParagraph` 改为使用 `content` 属性承载省略文本。
  - 修复真实打开 `/admin/tasks` 时的 Ant Design Vue 告警：`When ellipsis is enabled, please use content instead of children`。

## 真实页面验证

- 实际打开 `/admin/tasks`：
  - 页面总量 `38159`，活动 `10704`，告警 `178`，失败 `1178`。
  - 分区与后端一致：云资产同步 `0/0`，云服务器任务 `10516/10516`，生命周期计划 `7/8`，通知计划 `10/22895`，自动续费 `171/4740`。
  - 控制台 0 error / 0 warning。
- 实际打开 `/admin/tasks/notices`：
  - 页面计数与服务端一致：通知计划 `21887`，近期计划 `3429`，未来计划 `18458`，历史通知 `14960`。
  - 第 2 页和末页实测通过；末页服务端 offset `21880` 返回 7 条，页面也显示 7 条有效数据。
  - 控制台 0 error / 0 warning。
- 实际打开 `/admin/cloud-assets`：
  - 页面风险计数与当前 API 完全一致：全部 `1500001`，运行中 `449988`，即将到期 `1250`，已过期 `1752`，未附加固定 IP `1`，云账号异常 `1045002`，关机计划关闭 `384`，未绑定用户 `1`，未绑定群组 `11`，续费关闭 `4556`，已删除 `5007`。
  - 默认折叠已删除后分页总分组为 `1489996`，每页 20，末页 `74500`。
  - 第 2 页真实点击验证通过，页面首尾数据与后端第 2 页一致。
  - 末页真实点击验证通过，页面显示 `16 / 16` 组，末页首尾数据与后端第 `74500` 页一致。
  - 控制台 0 error / 0 warning。
- 实际打开 `/admin/tasks/plans`：
  - 页面显示并后端核对：当前计划资产 `1500001`，服务器资产 `1000000`，缺少到期时间 `251`，未附加 IP `500001`，关机计划 `979990`，服务器删除计划 `2`，IP 删除计划 `500000`，IP 删除历史 `520008`。
  - 控制台 0 error / 0 warning。

## 压测结果

- 代理列表 150 万资产压力页接口采样：
  - page 1：`5318.89 ms`，20 组 / 20 项。
  - page 2：`3240.93 ms`，20 组 / 20 项。
  - page 1000：`3853.84 ms`，20 组 / 20 项。
  - page 74500：`4895.32 ms`，16 组 / 16 项。
- 本轮确认没有翻页丢数据；但代理列表仍未达到 2 秒内目标，继续列为性能风险。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_compact_returns_ip_view_payload cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_all_includes_disabled_or_missing_cloud_account_assets cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_paginated_uses_twenty_user_groups_per_page cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches --settings=shop.settings --verbosity=1
pnpm --filter @vben/web-antd typecheck
git diff --check
```

结果：Django 系统检查、5 个聚焦测试、前端类型检查和空白检查通过。SQLite 输出的 `db_comment` 警告为已知数据库能力差异。

## 清理

- 已删除本轮临时后台账号 `codex_ui_tester`。
- 已关闭 Playwright 浏览器并删除 `.playwright-cli/` 临时目录。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布、删除业务数据或删除测试库。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险

- 代理列表接口在 150 万资产下仍为 3.2 到 5.3 秒，需要继续优化到 2 秒内，同时保持第 2 页、深页和末页不丢数据。
- 机器人真机全菜单点击未在本轮重复执行；当前重点已完成生命周期真实创建 / 关机 / 删除 / IP 释放基线。
