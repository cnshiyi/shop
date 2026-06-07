# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 00:15 CST
- 状态：完成生命周期真实创建、关机、删机、固定 IP 释放复测，并完成前端计划页、订单详情页、资产详情页实测。
- 本轮范围：AWS Lightsail 真机创建、生命周期开关矩阵、真实删除和释放清理、数据库终态对账、前端页面验证。

## 真机实测

- 测试用户：`TelegramUser #172`，`codex_real_machine_test`。
- 套餐：`CloudServerPlan #131`，新加坡，`实机测试 Nano`。
- 订单：`#50097`。
- 资产：`#1500333`。
- 金额：5 USDT，使用项目余额支付。
- 云资源、公网 IP、代理链接、代理 secret、登录密码和云账号密钥均未写入报告。

## 覆盖结果

- 真实创建：
  - 项目服务创建余额支付订单，订单从 `paid` 进入开通。
  - AWS Lightsail 实例真实创建成功，固定 IP 绑定成功。
  - BBR、MTProxy 主代理、备用代理、Telemt 多端口和 SOCKS5 初始化完成。
  - 开通后订单为 `completed`，资产为 `running/is_active=True`，资产到期事实写入 `CloudAsset.actual_expires_at`。
- 关机阶段：
  - `cloud_server_shutdown_enabled=0` 阻断真实关机。
  - `CloudAsset.shutdown_enabled=False` 阻断真实关机。
  - 非执行时间窗口阻断真实关机。
  - 打开总开关、资产关机开关和当前窗口后，真实关机成功，订单进入 `suspended`，资产进入 `stopped/is_active=False`。
- 删机阶段：
  - `cloud_server_delete_enabled=0` 阻断真实删机。
  - `CloudAsset.server_delete_enabled=False` 阻断真实删机。
  - 非执行时间窗口阻断真实删机。
  - 第一次真实删机遇到 AWS 停止中过渡状态，系统未误标删除。
  - 等待后第二次真实删机成功，订单和资产进入 `deleted`，实例标识清空，固定 IP 进入待释放。
- 固定 IP 释放阶段：
  - `cloud_ip_delete_enabled=0` 阻断真实释放固定 IP。
  - `CloudAsset.ip_delete_enabled=False` 阻断真实释放固定 IP。
  - 非执行时间窗口阻断真实释放固定 IP。
  - 打开总开关、资产 IP 删除开关和当前窗口后，真实释放成功。

## 数据库对账

- 最终订单：`#50097` 为 `deleted`。
- 最终资产：`#1500333` 为 `deleted/is_active=False`。
- 实例标识、固定 IP 名称、当前公网 IP 和 IP 回收时间均已清空。
- 生命周期任务最终为：`suspend/done`、`delete/done`、`recycle/done`。
- 生命周期配置已恢复：`cloud_server_shutdown_enabled` 恢复为默认缺省，`cloud_server_delete_enabled=1`，`cloud_ip_delete_enabled=1`，三个执行时间均恢复为 `15:00`。

## 前端页面验证

- 实际打开 `/admin/tasks/plans`：
  - 页面标题为“计划”。
  - 显示关机服务器、删除服务器、删除 IP 三个总开关。
  - 显示关机计划、服务器删除历史、IP 删除历史区域。
  - 页面计数显示：服务器删除历史 `20010`，IP 删除历史 `520008`。
  - 控制台 error / warning 均为 0。
- 实际打开 `/admin/cloud-orders/50097`：
  - 页面标题为“云订单详情”。
  - 显示已删除状态和生命周期区域。
  - 未出现加载失败、请求失败或异常文案。
  - 控制台 error / warning 均为 0。
- 实际打开 `/admin/cloud-assets/1500333`：
  - 页面标题为“代理详情”。
  - 显示已删除状态、生命周期区域和关联订单。
  - 未出现加载失败、请求失败或异常文案。
  - 控制台 error / warning 均为 0。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_history_mixes_orders_and_assets_by_updated_at cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_history_includes_orphan_deleted_server_asset cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_returns_server_delete_history_table --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/lifecycle_plan_queries.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
git diff --check
```

## 红线

- 本轮执行了用户明确授权的真实 AWS Lightsail 创建、关机、删除服务器和固定 IP 释放。
- 本轮未执行真实链上支付、链上广播、生产发布或删除业务压测数据。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险与下一轮

- 真实删机仍可能遇到 AWS 停止中过渡状态；当前执行器已正确保持未删除并允许重试，后续可以继续观察任务中心是否对该类重试展示足够清楚。
- 下一轮继续做计划页和代理列表深分页真页对账，重点验证跳页、末页和数据库精确排序一致。
