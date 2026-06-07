# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 22:08 CST
- 状态：完成代理列表分页性能补丁验证，并按用户授权再次完成真实生命周期创建服务器、关机、删除服务器和固定 IP 释放。
- 本轮范围：后端服务端分页优化、150 万资产压力页采样、真实 AWS Lightsail 创建 / 生命周期开关矩阵 / 删机 / IP 回收、后台订单详情 / 资产详情 / 计划页真实浏览器核对、聚焦测试。

## 本轮修复

- `cloud/api_asset_snapshots.py`：
  - 为代理列表快照风险计数和分组总数增加版本化短缓存。
  - 分组总数统计清理默认排序后再 `distinct().count()`，避免默认排序字段干扰分组总数口径。
  - 深页 / 末页分组分页增加反向分页路径，保持前端排序契约不变并降低末页 offset 成本。
- `cloud/lifecycle_tasks.py`、`cloud/lifecycle_execution.py`：
  - 新增订单 / 资产生命周期任务收敛 helper。
  - 当真实生命周期动作已成功，即使成功来自人工重试入口，也会把同一订单或资产的同类型 `pending/claimed/failed` 任务收敛为 `done`。
  - 修复真机实测暴露的问题：AWS 实例停止中过渡导致第一次删机失败，第二次人工重试成功后，旧 `delete` 任务仍显示 failed。
- `cloud/tests.py`：
  - 新增分组总数 / 末页反向分页回归测试。
  - 新增“计划删机失败后人工重试成功会收敛失败任务”的回归测试。

## 真机生命周期实测

- 用户已明确授权真实创建和删除云服务器，本轮创建 1 台 AWS Lightsail 测试服务器。
- 测试订单：`#50096`；测试资产：`#1500332`。
- 创建结果：余额支付下单成功，AWS Lightsail 实例创建成功，固定 IP 绑定成功，BBR 和代理安装完成，订单进入 `completed`，资产进入 `running`。
- 关机矩阵：
  - 关机总开关关闭：阻断真实关机。
  - 资产关机开关关闭：阻断真实关机。
  - 关机执行窗口外：阻断真实关机。
  - 开关和窗口允许：真实关机成功，订单进入 `suspended`，资产进入 `stopped/is_active=False`。
- 删机矩阵：
  - 删除服务器总开关关闭：阻断真实删机。
  - 资产服务器删除开关关闭：阻断真实删机。
  - 删除服务器执行窗口外：阻断真实删机。
  - 第一次真实删机遇到 AWS 停止中过渡状态，系统未误标删除。
  - 第二次重试真实删机成功，订单和资产进入 `deleted`，实例标识清空。
- 固定 IP 回收矩阵：
  - 删除 IP 总开关关闭：阻断真实释放固定 IP。
  - 资产 IP 删除开关关闭：阻断真实释放固定 IP。
  - IP 删除执行窗口外：阻断真实释放固定 IP。
  - 开关和窗口允许：真实释放固定 IP 成功，订单公网 IP、固定 IP 名称和 `ip_recycle_at` 清空。
- 最终状态：订单 `#50096` 为 `deleted`；资产 `#1500332` 为 `deleted/is_active=False`；实例、固定 IP 和当前公网 IP 均已清空；生命周期任务 `suspend/delete/recycle` 均为 `done`。

## 页面实测

- 实际打开 `/admin/cloud-orders/50096`：
  - 页面标题为“云订单详情”，订单状态显示已删除，生命周期区域正常显示。
  - 服务器实例 ID、当前公网 IP、固定 IP 名称均为空；历史信息仍在订单说明中保留。
  - 控制台 0 error / 0 warning。
- 实际打开 `/admin/cloud-assets/1500332`：
  - 页面标题为“代理详情”，包含已删除状态、生命周期区域和关联订单。
  - 控制台 0 error。
- 实际打开 `/admin/tasks/plans`：
  - 页面标题为“计划”，包含关机计划、删除计划、IP 删除和历史区域。
  - 本轮测试订单生命周期任务对账为 `suspend/done`、`delete/done`、`recycle/done`，失败数 0。
  - 控制台 0 error。

## 压测结果

- 代理列表 150 万资产分组分页 MySQL 采样：
  - 冷缓存 page 1：`4765.82 ms`，20/20。
  - 冷缓存 page 2：`1016.32 ms`，20/20。
  - 冷缓存 page 1000：`1706.28 ms`，20/20。
  - 冷缓存 page 74500：`1091.12 ms`，16/16。
  - 热缓存 page 1：`1956.39 ms`，20/20。
  - 热缓存 page 2：`999.43 ms`，20/20。
  - 热缓存 page 1000：`1702.38 ms`，20/20。
  - 热缓存 page 74500：`1075.15 ms`，16/16。
- 真实页面 `/admin/cloud-assets` 已点击第 2 页和末页，页面首尾数据与数据库/API 对账一致。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_manual_delete_success_finishes_failed_lifecycle_delete_task cloud.tests.CloudServerServicesTestCase.test_failed_lifecycle_and_notice_tasks_wait_retry_window cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_total_counts_distinct_groups_only --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/lifecycle_tasks.py cloud/lifecycle_execution.py cloud/api_asset_snapshots.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

结果：Django 系统检查、4 个聚焦测试、编译检查和空白检查通过。SQLite `db_comment` 警告为已知数据库能力差异。

## 清理

- 已恢复测试前生命周期配置：`cloud_server_shutdown_enabled` 回默认未显式配置，`cloud_server_delete_enabled=1`，`cloud_ip_delete_enabled=1`，三个执行时间均恢复为 `15:00`。
- 本轮真实 AWS 测试实例已删除，固定 IP 已释放，未发现本轮测试资源残留。
- 已删除临时后台账号 `codex_ui_tester`，已关闭 Playwright 浏览器并删除 `.playwright-cli/` 临时目录。
- 已停止本轮后端 `runserver` 测试进程；前端 Vite 为既有开发进程，未处理。

## 红线

- 本轮执行了用户明确授权的真实 AWS Lightsail 创建、关机、删机和固定 IP 释放。
- 本轮未执行链上广播、真实地址充值到账、生产发布、删除业务数据或删除测试库。
- 本轮最终报告不记录完整公网 IP、完整实例名、完整固定 IP 名、完整代理链接、代理 secret、登录密码、云账号密钥或 Telegram session。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险

- 订单详情页仍会向已登录管理员展示历史代理链路和历史公网 IP；这是现有后台展示能力，最终报告已脱敏。后续若要降低后台暴露面，可单独收敛已删除订单详情中的历史代理链路展示。
- 代理列表 page 1 热缓存约 2 秒边缘，深页已降到约 1.1 到 1.7 秒；后续继续关注首屏聚合成本。
