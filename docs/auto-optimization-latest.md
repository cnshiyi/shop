# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 21:18 CST
- 状态：完成生命周期真机创建 / 关机 / 删机 / 固定 IP 释放复测，并补齐生命周期计划页全局总开关展示态。
- 本轮范围：真实云资源生命周期链路、后台订单详情 / 资产详情 / 计划页实测、计划页总开关联动、前端类型检查、后端聚焦测试。

## 真机生命周期结果

- 测试订单：`#50095` / `SRV20260607125634332663`。
- 测试资产：`#1500331`。
- 测试用户：`TelegramUser #172`，`codex_real_machine_test`。
- 云厂商和套餐：AWS Lightsail，新加坡，`实机测试 Nano`。
- 支付方式：项目数据库 USDT 钱包余额支付，金额 5 USDT；未执行链上广播或真实地址充值。
- 创建结果：AWS Lightsail 实例真实创建成功，固定 IP 绑定成功，代理初始化完成，订单进入 `completed`，资产进入 `running`。
- 关机结果：真实关机成功，订单进入 `suspended`，资产进入 `stopped/is_active=False`。
- 删机结果：第一次真实删机遇到 AWS 实例停止中状态转换，系统未误标已删除；等待稳定后只针对测试订单重试成功，订单和资产进入 `deleted`。
- IP 释放结果：固定 IP 真实释放成功，订单固定 IP 名称、`public_ip`、`ip_recycle_at` 均已清空。
- 最终清理：测试订单 `#50095` 为 `deleted`，测试资产 `#1500331` 为 `deleted/is_active=False`；实例标识、固定 IP 名称、公网 IP 和 IP 回收时间均已清空。

## 修复摘要

- `bot/api.py` 的生命周期计划展示层显式叠加三个总开关：
  - `cloud_server_shutdown_enabled=0` 时，关机计划项输出 `global_shutdown_disabled`。
  - `cloud_server_delete_enabled=0` 时，删机计划项输出 `global_server_delete_disabled`。
  - `cloud_ip_delete_enabled=0` 时，IP 删除计划项输出 `global_ip_delete_disabled`。
- 上述状态同步落到 `queue_status`、`plan_state`、`plan_state_label`、`blocked_reason`，避免执行器已拦截但后台仍显示“待执行”的口径错位。
- `cloud/tests.py` 新增总开关联动测试，并给原有单项开关测试补上显式总开关前置条件。
- 前端计划页同步使用总开关状态展示阻塞原因，并在总开关关闭时禁用对应单项开关。
- 前端订单详情页为 `Descriptions` 奇数项补齐 `:span="2"`，修复 Ant Design Vue 控制台告警。

## 真实页面验证

- 实际打开 `/admin/cloud-orders/50095`：确认订单详情、已删除状态、服务器信息和生命周期区域正常显示；控制台 0 error / 0 warning。
- 实际打开 `/admin/cloud-assets/1500331`：确认资产详情、已删除状态、生命周期区域和关联订单正常显示；控制台 0 error / 0 warning。
- 实际打开 `/admin/tasks/plans`：确认计划页、关机服务器 / 删除服务器 / 删除 IP 总开关、显示列开关和 IP 删除历史记录正常显示；控制台 0 error / 0 warning。
- 计划接口刷新后计数：当前计划资产 `1500001`，关机计划 `979990`，删除计划 `2`，IP 删除计划 `500000`，IP 删除历史 `520008`。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_global_shutdown_switch_blocks_scheduled_suspend cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_respects_asset_ip_delete_disabled cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_releases_retained_static_ip_after_recycle_due --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/tests.py cloud/lifecycle_execution.py cloud/lifecycle.py
pnpm --filter @vben/web-antd typecheck
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

结果：6 个生命周期聚焦测试、Django 系统检查、Python 编译检查、前端类型检查和前后端空白检查均通过。SQLite 输出的 `db_comment` 警告为已知数据库能力差异。

## 红线

- 本轮执行了用户已授权的真实云资源创建、关机、删除服务器和固定 IP 释放，并已单独写入 `docs/real-machine-test-report.md`。
- 本轮未执行真实链上支付、链上广播、生产发布、删除业务数据或删除测试库。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥、云厂商密钥、完整公网 IP、代理链接、代理 secret 或登录密码。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险

- 本轮重点覆盖生命周期真机创建 / 关机 / 删机 / 固定 IP 释放和计划页开关联动；机器人真机全菜单点击未在本轮重复执行。
- 下一轮继续覆盖机器人真机点击、通知计划页面、任务中心页面、代理列表页面的真实翻页 / 跳页 / 数据库对账。
