# 版本记录

## v0.5.50 - 2026-04-24
- 将 `mark_cloud_server_ip_change_requested` 从 `biz.services.cloud_servers` 收回到 `cloud/services.py`，换 IP 流程继续向真实 cloud 域服务归位。
- 该迁移保留了“当原订单缺失可用套餐关联时，按地区回退匹配可用套餐”的既有容错行为。

### 验证
- `./.venv/bin/python -m py_compile cloud/services.py`
- `DJANGO_TEST_SQLITE=1 ./.venv/bin/python manage.py test biz.tests.CloudServerServicesTestCase.test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing --verbosity 1`

## v0.5.49 - 2026-04-24
- 将一批低风险云服务实现从 `biz.services.cloud_servers` 收回到 `cloud/services.py`：用户重绑、重试初始化、提醒静默、自动续费开关、自动续费读取、到期延期。
- `cloud/services.py` 继续从纯转发层往真实域服务推进，后续可继续迁移续费/换 IP 相关逻辑。

### 验证
- `./.venv/bin/python -m py_compile cloud/services.py`
- `./.venv/bin/python manage.py shell -c "from cloud.services import ...; print('imports ok')"`

## v0.5.48 - 2026-04-24
- 新增 `CloudIpLog` 与 `cloud_ip_log` 表，用于记录云服务器 IP 从创建分配、手动变更、到期、延停、删除到回收的生命周期日志。
- 后端已在 `cloud/provisioning.py`、`cloud/lifecycle.py`、`cloud/api.py` 的关键入口补齐 IP 日志埋点，并新增 `/api/dashboard/cloud-assets/ip-logs/` 查询接口，支持关键字搜索。
- 前端已新增“IP日志”页面与菜单，支持按订单号、用户、IP、实例ID、说明搜索。

### 验证
- `./.venv/bin/python -m py_compile mall/models.py cloud/models.py cloud/services.py cloud/provisioning.py cloud/lifecycle.py cloud/api.py dashboard_api/urls.py`
- `./.venv/bin/python manage.py migrate`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-assets/ip-logs/ ..."`

## v0.5.47 - 2026-04-24
- 将 `update_cloud_asset` 及其依赖 helper 正式迁入 `cloud/api.py`，并补上 `/api/dashboard/cloud-assets/<asset_id>/` 路由，`cloud/api.py` 已不再从 `dashboard_api.views` 反向导入。
- 现在 `cloud/api.py` / `bot/api.py` / `orders/api.py` 都不再直接 `import dashboard_api.views`，`dashboard_api` 进一步收缩为兼容层。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py dashboard_api/urls.py dashboard_api/views.py bot/api.py`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-assets/ ... /api/dashboard/cloud-assets/<id>/ ..."`

## v0.5.46 - 2026-04-24
- 将 `cloud/api.py` 对 `dashboard_api.views` 的通用 helper 依赖切到 `bot.api`，把登录校验、响应封装、区域/状态/用户等公共 helper 向新域兼容层集中。
- 当前 `cloud/api.py` 仅暂留对 `update_cloud_asset` 的旧视图依赖，后续可继续把这段真实逻辑迁出，进一步瘦身 `dashboard_api.views`。

### 验证
- `./.venv/bin/python -m py_compile bot/api.py cloud/api.py dashboard_api/views.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-assets/ ... /api/dashboard/overview/ ..."`

## v0.5.45 - 2026-04-24
- 修正 AWS Lightsail 配置同步口径：不再把 `win`、`ipv6`、`c_/m_/g_` 等变体一并算作常规在售规格，并按套餐名去重，仅保留主套餐档位。
- 修正后 AWS 每地区从 `100` 条压到 `11` 条，避免“配置同步”页把底层 bundle 变体误当成可售规格总数。

### 验证
- `./.venv/bin/python manage.py shell -c "from biz.services.custom import _fetch_aws_bundle_templates; ..."`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-plans/sync/ ..."`

## v0.5.44 - 2026-04-24
- 按最新业务语义恢复“配置同步”：`cloud-plans/sync` 继续保留，但现在只同步 AWS / 阿里云在售规格与价格模板，不再生成或修改人工维护的 `CloudServerPlan`。
- AWS 价格同步已取消单地区限制；本轮实测同步后，人工套餐保持 `0`，价格模板回填到 `1609` 条。
- 前端已恢复“配置同步”页面与路由，用于查看/触发云厂商在售规格同步；“套餐列表”仍保持人工维护。

### 验证
- `./.venv/bin/python -m py_compile biz/services/custom.py cloud/services.py cloud/api.py dashboard_api/views.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-plans/sync/ ..."`
- `pnpm -C apps/web-antd build`

## v0.5.43 - 2026-04-24
- `orders.api` 不再直接从 `dashboard_api.views` 导入公共 helper，先改为复用 `bot.api` 的过渡 helper，进一步收缩旧 API 兼容层。
- 已确认 `/api/dashboard/orders/` 与 `/api/dashboard/recharges/` 继续返回 `200 JSON`。

### 验证
- `./.venv/bin/python -m py_compile orders/api.py`
- `./.venv/bin/python manage.py check`
- `curl -s http://127.0.0.1:8000/api/dashboard/orders/ -H 'Authorization: Bearer session-1'`
- `curl -s http://127.0.0.1:8000/api/dashboard/recharges/ -H 'Authorization: Bearer session-1'`

## v0.5.42 - 2026-04-24
- 云套餐改为人工维护：停用自动套餐同步接口，`cloud-plans/sync` 现在直接返回“已停用自动同步”。
- 删除现有脏套餐数据：已清空 `CloudServerPlan` 22 条、`ServerPrice` 109 条；确认当前无订单和购物车外键引用。
- AWS 资产同步改为默认不限制单一区域；未显式传 `aws_region` 时将按全部可用 Lightsail 地区同步。
- 套餐缓存回源逻辑改为仅依赖人工维护的 `CloudServerPlan`，不再从 `ServerPrice` 反向生成套餐。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py cloud/services.py mall/management/commands/sync_aws_assets.py biz/services/custom.py dashboard_api/views.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-plans/ ..."`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-plans/sync/ ..."`
- `./.venv/bin/python manage.py shell -c "from cloud.models import CloudServerPlan, ServerPrice; ..."`

## v0.5.41 - 2026-04-24
- `bot.api` 继续承接真实实现：用户列表、余额修改、折扣修改、余额明细、商品列表、商品创建、商品更新已不再从 `dashboard_api.views` 直接导入实现。
- 保留 `dashboard_api.views` 中的公共 helper 导入，`dashboard_api` 进一步收缩为兼容/工具层。

### 验证
- `./.venv/bin/python -m py_compile bot/api.py`
- `./.venv/bin/python manage.py check`
- `curl -s http://127.0.0.1:8000/api/dashboard/users/ -H 'Authorization: Bearer session-1'`
- `curl -s http://127.0.0.1:8000/api/dashboard/products/ -H 'Authorization: Bearer session-1'`

## v0.5.40 - 2026-04-24
- `bot.api` 继续承接真实实现：`csrf` 已不再由 `dashboard_api.views` 提供。
- 已验证 `/api/csrf/` 返回 `200 JSON`，并带 `csrftoken` Cookie。

### 验证
- `./.venv/bin/python -m py_compile bot/api.py`
- `./.venv/bin/python manage.py check`
- `curl -i -s http://127.0.0.1:8000/api/csrf/`

## v0.5.39 - 2026-04-24
- `cloud.api` 继续承接真实实现：`_asset_payload` 已不再转发到 `dashboard_api.views`。
- 已验证 `/api/dashboard/cloud-assets/` 在迁移后继续返回 `200 JSON`。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-assets/ ..."`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/cloud-assets/ -H 'Authorization: Bearer session-1'`

## v0.5.38 - 2026-04-24
- `bot.api` 继续承接真实实现：`auth_login`、`auth_logout`、`auth_refresh`、`auth_codes` 已不再由 `dashboard_api.views` 提供。
- `dashboard_api/urls.py` 已把 `auth/*` 全部切到 `bot.api`。
- 已验证空凭据登录继续返回 `401 JSON`，`auth/codes` 与 `auth/refresh` 继续返回 `200 JSON`。

### 验证
- `./.venv/bin/python -m py_compile bot/api.py dashboard_api/urls.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/auth/login ... /api/auth/codes ... /api/auth/refresh ..."`
- `curl -i -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' -d '{}'`
- `curl -i -s http://127.0.0.1:8000/api/auth/codes -H 'Authorization: Bearer session-1'`
- `curl -i -s -X POST http://127.0.0.1:8000/api/auth/refresh -H 'Authorization: Bearer session-1'`

## v0.5.37 - 2026-04-24
- `cloud.api` 继续承接真实实现：`_cloud_order_detail_payload` 已不再转发到 `dashboard_api.views`。
- 已验证 `/api/dashboard/cloud-orders/` 在迁移后继续返回 `200 JSON`。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-orders/ ..."`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/cloud-orders/ -H 'Authorization: Bearer session-1'`

## v0.5.36 - 2026-04-24
- `cloud.api` 继续承接真实实现：`_server_price_payload` 已不再转发到 `dashboard_api.views`。
- 修正一次迁移回归：保持 `ServerPrice` 继续使用 `server_name/server_description` 序列化，避免误读不存在的 `plan_name` 字段。
- 已验证 `/api/dashboard/cloud-pricing/` 在修复后继续返回 `200 JSON`。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-pricing/ ..."`
- `./.venv/bin/python run.py web`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/cloud-pricing/ -H 'Authorization: Bearer session-1'`

## v0.5.35 - 2026-04-24
- `cloud.api` 继续承接真实实现：`_cloud_plan_payload` 已不再转发到 `dashboard_api.views`。
- 已验证 `/api/dashboard/cloud-plans/` 在迁移后继续返回 `200 JSON`。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-plans/ ..."`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/cloud-plans/ -H 'Authorization: Bearer session-1'`

## v0.5.34 - 2026-04-24
- `cloud.api` 继续承接真实实现：`sync_cloud_plans` 已不再转发到 `dashboard_api.views`。
- 已验证 `/api/dashboard/cloud-plans/sync/` 在迁移后继续返回 `200 JSON`，并正确回报区域与价格汇总。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-plans/sync/ ..."`
- `curl -i -s -X POST http://127.0.0.1:8000/api/dashboard/cloud-plans/sync/ -H 'Authorization: Bearer session-1'`

## v0.5.33 - 2026-04-24
- `orders.api` 继续承接真实实现：`_order_payload` 已不再转发到 `dashboard_api.views`。
- 已验证 `/api/dashboard/orders/` 在迁移后继续返回 `200 JSON`。

### 验证
- `./.venv/bin/python -m py_compile orders/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/orders/ ..."`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/orders/ -H 'Authorization: Bearer session-1'`

## v0.5.32 - 2026-04-24
- `orders.api` 继续承接真实实现：`_recharge_detail_payload`、`_apply_recharge_status` 已不再转发到 `dashboard_api.views`。
- 已验证充值详情与状态更新两条路径在不存在订单时稳定返回 `404 JSON`，与旧行为一致。

### 验证
- `./.venv/bin/python -m py_compile orders/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/recharges/999999/ ..."`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/recharges/999999/ -H 'Authorization: Bearer session-1'`
- `curl -i -s -X POST http://127.0.0.1:8000/api/dashboard/recharges/999999/status/ -H 'Authorization: Bearer session-1' -H 'Content-Type: application/json' -d '{"status":"completed"}'`

## v0.5.31 - 2026-04-24
- `cloud.api` 继续承接真实实现：`sync_cloud_assets` 已不再转发到 `dashboard_api.views`。
- 已通过真实同步验证 `/api/dashboard/cloud-assets/sync/` 返回 `200 JSON`，并正确回报两家云厂商同步状态与地区参数。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-assets/sync/ ..."`
- `curl -i -s -X POST http://127.0.0.1:8000/api/dashboard/cloud-assets/sync/ -H 'Authorization: Bearer session-1'`

## v0.5.30 - 2026-04-24
- `cloud.api` 继续承接真实实现：`sync_servers` 与内部 `_apply_server_missing_state` 已不再转发到 `dashboard_api.views`。
- 已通过真实同步验证 `/api/dashboard/servers/sync/` 返回 `200 JSON`，并正确回报 `synced`、`missing` 与地区信息。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/servers/sync/ ..."`
- `curl -i -s -X POST http://127.0.0.1:8000/api/dashboard/servers/sync/ -H 'Authorization: Bearer session-1'`

## v0.5.29 - 2026-04-24
- `cloud.api` 继续承接真实实现：`create_cloud_plan`、`update_cloud_plan`、`delete_cloud_plan` 已不再转发到 `dashboard_api.views`。
- 修正迁移中的一次错误导入：`refresh_custom_plan_cache` 改为从 `cloud.services` 引入。
- 已验证云套餐创建空参数返回 `400 JSON`，删除不存在套餐返回 `404 JSON`。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-plans/create/ ..."`
- `curl -i -s -X POST http://127.0.0.1:8000/api/dashboard/cloud-plans/create/ -H 'Authorization: Bearer session-1' -H 'Content-Type: application/json' -d '{}'`
- `curl -i -s -X POST http://127.0.0.1:8000/api/dashboard/cloud-plans/999999/delete/ -H 'Authorization: Bearer session-1'`

## v0.5.28 - 2026-04-24
- `cloud.api` 继续承接真实实现：`servers_list` 与 `_server_payload` 已不再转发到 `dashboard_api.views`。
- 已验证服务器列表接口在默认去重与 `dedup=0` 两种模式下均返回 `200 JSON`。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/servers/ ..."`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/servers/ -H 'Authorization: Bearer session-1'`

## v0.5.27 - 2026-04-24
- `cloud.api` 继续承接真实实现：`cloud_order_detail`、`update_cloud_order_status` 与内部 `_apply_cloud_order_status` 已不再转发到 `dashboard_api.views`。
- 已验证云订单详情与状态更新两条路径在不存在订单时稳定返回 `404 JSON`，与旧行为一致。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-orders/999999/ ..."`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/cloud-orders/999999/ -H 'Authorization: Bearer session-1'`
- `curl -i -s -X POST http://127.0.0.1:8000/api/dashboard/cloud-orders/999999/status/ -H 'Authorization: Bearer session-1' -H 'Content-Type: application/json' -d '{"status":"completed"}'`

## v0.5.26 - 2026-04-24
- `bot.api` 继续承接真实实现：`verify_cloud_account` 已不再转发到 `dashboard_api.views`。
- 已验证 `/api/dashboard/settings/cloud-accounts/<id>/verify/` 在不存在账号时稳定返回 `404 JSON`，与旧行为一致。

### 验证
- `./.venv/bin/python -m py_compile bot/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/settings/cloud-accounts/999999/verify/ ..."`
- `curl -i -s -X POST http://127.0.0.1:8000/api/dashboard/settings/cloud-accounts/999999/verify/ -H 'Authorization: Bearer session-1'`

## v0.5.25 - 2026-04-24
- `bot.api` 继续承接真实实现：`overview` 已从 `dashboard_api.views` 迁入 `bot` 域。
- `dashboard_api/urls.py` 中 `dashboard/overview/` 已改为走 `bot_api.overview`，并通过 Django 测试客户端与本地 HTTP 双重验证返回 `200 JSON`。

### 验证
- `./.venv/bin/python -m py_compile bot/api.py dashboard_api/urls.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/overview/ ..."`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/overview/ -H 'Authorization: Bearer session-1'`

## v0.5.24 - 2026-04-24
- `cloud.api` 继续承接真实实现：`delete_server` 已从 `dashboard_api.views` 迁入 `cloud` 域。
- `dashboard_api/urls.py` 中 `servers/<id>/delete/` 已改为走 `cloud_api.delete_server`，并验证不存在服务器时稳定返回 `404 JSON`，与旧行为一致。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py dashboard_api/urls.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/servers/999999/delete/ ..."`
- `curl -i -s -X POST http://127.0.0.1:8000/api/dashboard/servers/999999/delete/ -H 'Authorization: Bearer session-1'`

## v0.5.23 - 2026-04-24
- `cloud.api` 继续承接真实实现：`tasks_overview` 已从 `dashboard_api.views` 迁入 `cloud` 域。
- `dashboard_api/urls.py` 中 `tasks/` 与 `task-list/` 已改为走 `cloud_api.tasks_overview`，并通过 Django 测试客户端与本地 HTTP 双重验证返回 `200 JSON`。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py dashboard_api/urls.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/tasks/ ..."`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/tasks/ -H 'Authorization: Bearer session-1'`

## v0.5.22 - 2026-04-24
- `bot.api` 继续承接真实实现：`site_config_groups` 已从 `dashboard_api.views` 迁入 `bot` 域。
- 已验证 `/api/dashboard/settings/site-configs/groups/` 返回 `200 JSON`，配置分组、敏感标记与描述数据保持一致。

### 验证
- `./.venv/bin/python -m py_compile bot/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/settings/site-configs/groups/ ..."`

## v0.5.21 - 2026-04-24
- `cloud.api` 继续承接真实实现：`servers_statistics` 已从 `dashboard_api.views` 迁入 `cloud` 域。
- 已通过 Django 测试客户端与本地 HTTP 验证 `/api/dashboard/servers/statistics/` 返回 `200 JSON`，统计聚合结果正确输出区域、明细与汇总。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/servers/statistics/ ..."`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/servers/statistics/ -H 'Authorization: Bearer session-1'`

## v0.5.20 - 2026-04-24
- `cloud.api` 继续承接真实实现：`cloud_assets_list` 已从 `dashboard_api.views` 迁入 `cloud` 域。
- 已验证云资产列表两条主路径：普通列表与 `grouped=1` 分组列表均返回 `200 JSON`。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ... /api/dashboard/cloud-assets/ ..."`

## v0.5.19 - 2026-04-24
- `cloud.api` 继续承接真实实现：`cloud_orders_list` 已从 `dashboard_api.views` 迁入 `cloud` 域。
- 通过 Django 测试客户端与本地 HTTP 双重验证：`/api/dashboard/cloud-orders/` 返回 `200 JSON`，说明云订单列表入口已在新域稳定工作。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; ..."`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/cloud-orders/ -H 'Authorization: Bearer session-1'`

## v0.5.18 - 2026-04-24
- `orders.api` 继续承接真实实现：`orders_list` 已从 `dashboard_api.views` 迁入 `orders` 域。
- 用 Django 测试客户端与本地重启后的后台接口双重确认：`/api/dashboard/orders/` 与 `/api/dashboard/recharges/` 均返回 `200 JSON`，排除了运行进程热更新滞后的假阴性。

### 验证
- `./.venv/bin/python -m py_compile orders/api.py`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py shell -c "from django.test import Client; c=Client(HTTP_HOST='127.0.0.1', HTTP_AUTHORIZATION='Bearer session-1'); ..."`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/orders/ -H 'Authorization: Bearer session-1'`

## v0.5.17 - 2026-04-24
- `cloud.api` 继续承接真实实现：`cloud_pricing_list`、`cloud_plans_list` 已从 `dashboard_api.views` 迁入 `cloud` 域。
- 云价格与套餐查询接口烟测返回正常，说明 `cloud` 域后台 API 已不再只是空转发壳。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `curl -i -s 'http://127.0.0.1:8000/api/dashboard/cloud-pricing/?provider=aws_lightsail' -H 'Authorization: Bearer session-1'`
- `curl -i -s 'http://127.0.0.1:8000/api/dashboard/cloud-plans/?provider=aws_lightsail' -H 'Authorization: Bearer session-1'`

## v0.5.16 - 2026-04-24
- `bot.api` 继续承接真实实现：`user_info`、`me` 已从 `dashboard_api.views` 挪入 `bot` 域。
- 关键登录态接口烟测继续正常，说明 `dashboard_api` 缩壳过程中主入口还稳。

### 验证
- `./.venv/bin/python -m py_compile bot/api.py`
- `./.venv/bin/python manage.py check`
- `curl -s http://127.0.0.1:8000/api/user/info -H 'Authorization: Bearer session-1'`

## v0.5.15 - 2026-04-24
- `cloud.api` 已开始承接真实实现：`monitors_list` 不再只是从 `dashboard_api.views` 转发。
- 迁移过程中抓到并修复了一个真实回归：缺少 `dashboard_login_required` 会导致接口退回 Django 默认 `302` 登录跳转；现已恢复为 Bearer 会话下的 `200 JSON` 响应。

### 验证
- `./.venv/bin/python -m py_compile cloud/api.py`
- `./.venv/bin/python manage.py check`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/monitors/ -H 'Authorization: Bearer session-1'`

## v0.5.14 - 2026-04-24
- `orders.api` 已开始承接真实实现：`recharges_list`、`recharge_detail`、`update_recharge_status` 不再只是从 `dashboard_api.views` 转发。
- 对应后台充值接口烟测返回正常，`dashboard_api` 继续向兼容壳收缩。

### 验证
- `./.venv/bin/python -m py_compile orders/api.py`
- `./.venv/bin/python manage.py check`
- `curl -i -s http://127.0.0.1:8000/api/dashboard/recharges/ -H 'Authorization: Bearer session-1'`

## v0.5.13 - 2026-04-24
- `bot.api` 不再只是纯转发：已实际接管站点配置列表/初始化/更新，以及云账号列表/新增/更新/删除接口实现。
- `dashboard_api/urls.py` 对应路由已切到 `bot.api` 的真实实现，后台相关接口烟测返回正常。
- 这让 `dashboard_api.views` 进一步退化为兼容层，后续可继续把订单与云资源接口按同样方式搬离。

### 验证
- `./.venv/bin/python -m py_compile bot/api.py dashboard_api/urls.py`
- `./.venv/bin/python manage.py check`
- `curl -s http://127.0.0.1:8000/api/dashboard/settings/site-configs/ -H 'Authorization: Bearer session-1'`
- `curl -s http://127.0.0.1:8000/api/dashboard/settings/cloud-accounts/ -H 'Authorization: Bearer session-1'`

## v0.5.12 - 2026-04-24
- `dashboard_api/urls.py` 已开始真正按领域路由：用户/商品/配置入口改走 `bot.api`，订单/充值入口改走 `orders.api`，云资源/监控/套餐入口改走 `cloud.api`。
- 补充 `docs/installed-apps-cutover-plan.md`，明确了当前不能直接删旧 app 的原因，以及后续 `INSTALLED_APPS` 收口顺序。
- `accounts/management/commands/backfill_usernames_to_users.py` 已切到 `bot.models.TelegramUser`，继续减少旧目录直接暴露。

### 验证
- `./.venv/bin/python -m py_compile dashboard_api/urls.py bot/api.py accounts/management/commands/backfill_usernames_to_users.py`
- `./.venv/bin/python manage.py check`
- `curl -s http://127.0.0.1:8000/api/user/info -H 'Authorization: Bearer session-1'`

## v0.5.11 - 2026-04-24
- 完成表名迁移批次 D/E：`address_monitors` → `cloud_address_monitor`、`daily_address_stats` → `cloud_address_stat_daily`、`resource_snapshots` → `cloud_resource_snapshot`、`configs` → `core_site_config`、`cloud_account_configs` → `core_cloud_account`、`external_sync_logs` → `core_sync_log`。
- 现网库已完成整套目标表名收口：`bot / orders / cloud / core` 目标表名均已存在，并通过 `information_schema.tables` 核验。
- 余额流水记账链进一步从旧目录抽离：新增 `orders/ledger.py`，`biz.services.*` 与 `tron/scanner.py` 改为通过 `orders` 域记账；`accounts/services.py` 降级为兼容壳。

### 验证
- `./.venv/bin/python manage.py migrate`
- `./.venv/bin/python manage.py check`
- `./.venv/bin/python manage.py makemigrations --check --dry-run`
- `DJANGO_TEST_SQLITE=1 ./.venv/bin/python manage.py test biz.tests --verbosity 1`
- `information_schema.tables` 核验目标表名全集存在

## v0.5.10 - 2026-04-24
- 完成表名迁移批次 C：`cloud_server_plans` → `cloud_plan`、`server_prices` → `cloud_price`、`cloud_server_orders` → `cloud_order`、`cloud_assets` → `cloud_asset`、`servers` → `cloud_server`。
- `mall.0026_alter_cloudasset_table_alter_cloudserverorder_table_and_more` 已应用到当前数据库 `a`，并通过 `information_schema.tables` 核验确认新表名生效。
- 云资源主链保持可用：Django `check` 正常、SQLite 单测通过、后台登录信息接口正常。

### 验证
- `./.venv/bin/python manage.py migrate`
- `./.venv/bin/python manage.py check`
- `DJANGO_TEST_SQLITE=1 ./.venv/bin/python manage.py test biz.tests --verbosity 1`
- `curl -s http://127.0.0.1:8000/api/user/info -H 'Authorization: Bearer session-1'`
- `information_schema.tables` 核验：`cloud_plan` / `cloud_price` / `cloud_order` / `cloud_asset` / `cloud_server` 存在

## v0.5.09 - 2026-04-24
- 完成表名迁移批次 B：`products` → `order_product`、`cart_items` → `order_cart_item`、`orders` → `order_order`。
- `mall.0025_alter_cartitem_table_alter_order_table_and_more` 已应用到当前数据库 `a`，并确认新表名存在、旧表名不存在。
- 商品/订单主链维持可用：Django 迁移完成后，后台登录信息接口正常，SQLite 测试回归通过。

### 验证
- `./.venv/bin/python manage.py migrate`
- `DJANGO_TEST_SQLITE=1 ./.venv/bin/python manage.py test biz.tests --verbosity 1`
- `curl -s http://127.0.0.1:8000/api/user/info -H 'Authorization: Bearer session-1'`
- 数据库表核验：`order_product` / `order_cart_item` / `order_order` 存在

## v0.5.08 - 2026-04-24
- `TelegramUsername` 已从 Django 状态中安全下线：删除运行时代码模型定义，新增 `accounts.0009_deprecate_telegramusername_state`，仅移除状态、不直接删除数据库表，避免历史链路受影响。
- 完成表名迁移批次 A：`users` → `bot_user`、`balance_ledgers` → `order_balance_ledger`、`recharges` → `order_recharge`。
- 现网数据库已完成改表名并通过接口 smoke test；`dashboard_api` 登录信息接口仍正常。
- 识别出一个测试环境限制：`DJANGO_TEST_REUSE_DB=1` 复用现库时，会在改表名后于测试启动阶段重复执行迁移；当前改用 SQLite 测试 + 现网库检查作为本阶段验证手段，后续单独收敛测试策略。

### 验证
- `./.venv/bin/python manage.py migrate`
- `./.venv/bin/python manage.py makemigrations --check --dry-run`
- `./.venv/bin/python manage.py check`
- `DJANGO_TEST_SQLITE=1 ./.venv/bin/python manage.py test biz.tests --verbosity 1`
- `curl -s http://127.0.0.1:8000/api/user/info -H 'Authorization: Bearer session-1'`
- 数据库表核验：`bot_user` / `order_balance_ledger` / `order_recharge` 存在，旧表名不存在

## v0.5.07 - 2026-04-24
- 继续推进后端域重构：新增 `bot.services`、`orders.services`、`orders.runtime`、`cloud.cache` 作为过渡入口，把机器人主链逐步从 `biz`、`tron`、`monitoring` 旧路径剥离。
- `bot/runner.py`、`bot/handlers.py`、`tron/scanner.py`、`tron/resource_checker.py` 已开始改走 `bot / orders / cloud` 域入口，降低后续目录收敛时的联动改动面。
- 清理后台和运行时对 `telegramusernames` 的依赖：`dashboard_api` 已移除相关 `prefetch_related`，兼容壳不再继续导出 `TelegramUsername`。
- 管理命令和测试入口已开始切到新域模型：多处 `mall.models` / `accounts.models` 直接导入已替换为 `cloud.models` / `bot.models`。
- 修复更换 IP 场景中新旧订单到期时间不一致的问题，统一使用迁移截止时间，相关测试重新跑通。

### 验证
- `./.venv/bin/python -m py_compile bot/services.py orders/services.py bot/handlers.py cloud/provisioning.py tron/scanner.py`
- `DJANGO_TEST_REUSE_DB=1 ./.venv/bin/python manage.py test biz.tests --keepdb --noinput --verbosity 1`
- `DJANGO_TEST_SQLITE=1 ./.venv/bin/python manage.py test biz.tests --verbosity 1`
- `curl -s http://127.0.0.1:8000/api/user/info -H 'Authorization: Bearer session-1'`

## v0.5.06 - 2026-04-24
- 明确前后端仓库边界：当前 `shop` 仓库仅维护 Django 后端与接口，不再把 `dashboard_web/` 视为真实前端工程。
- 真实前端位置已确认并固定为 `C:\Users\Administrator\Desktop\vue-vben-admin`，当前实际使用页面位于 `C:\Users\Administrator\Desktop\vue-vben-admin\apps\web-antd`。
- 删除后端中直接托管前端产物的代码：移除 `core.views.admin_spa` 与 `shop.urls` 中 `/admin` 的 SPA 路由，避免继续把前端部署/访问逻辑耦合在当前后端仓库。
- 更新说明文档，避免后续继续在后端仓库误判前端位置。

### 验证
- 通过目录复查确认 `shop\dashboard_web` 仅有说明文件，真实前端页面位于 `vue-vben-admin\apps\web-antd`

## v0.5.05 - 2026-04-22
- 按需求把“同步服务器价格表”拆成独立链路：新增 `mall.ServerPrice` 独立表（`server_prices`），不再与套餐价格共用同一张业务表。
- `biz.services.custom.ensure_cloud_server_plans` 现会先同步 `ServerPrice`，再刷新套餐模板；地区缓存与套餐缓存的回源逻辑改为优先读取独立服务器价格表。
- `GET /api/dashboard/cloud-pricing/` 与 `POST /api/dashboard/cloud-plans/sync/` 的价格统计已切到 `ServerPrice`，返回字段保持兼容，前端无需改字段名即可继续使用。

### 验证
- 待执行 `C:\Users\Administrator\Desktop\shop\.venv\Scripts\python.exe manage.py migrate`
- 待执行 `C:\Users\Administrator\Desktop\shop\.venv\Scripts\python.exe manage.py check`

## v0.5.04 - 2026-04-21
- 新增数据库持久化补全：`DailyAddressStat`、`ResourceSnapshot`、`ExternalSyncLog`，并将监控每日统计、资源快照、部分外部采集日志接入数据库。
- 新增 `docs/DB_NAMING_CONVENTIONS.md`，统一数据库对象命名规范：新表/新字段严格采用小写蛇形命名，保留历史表兼容，不为纯命名整理强改旧表名。
- 多账户扩展预留继续收口到 `account_scope` / `account_key` 与 `CloudAccountConfig` 关联方案。

### 验证
- `uv run python manage.py check`

## v0.5.03 - 2026-04-19
- 机器人云服务器支付链路相关文案改为从 `SiteConfig` 读取，后台“系统设置”可直接维护数量页标题/提示、支付页标题/提示、钱包币种页、后台处理中提示、余额不足提示、支付说明、端口提示等文案。
- 保持原有回调协议与异步建单/异步钱包支付逻辑不变，仅将支付页展示文案配置化，便于运营后台即时调整。

### 验证
- `C:\Users\Administrator\Desktop\shop\.venv\Scripts\python.exe -m py_compile C:\Users\Administrator\Desktop\shop\bot\handlers.py C:\Users\Administrator\Desktop\shop\bot\keyboards.py`

## v0.5.02 - 2026-04-19
- 套餐设置补齐后台编辑能力：`CloudServerPlan` / `ServerPrice` 新增 `cost_price`（进货价），dashboard 套餐页支持编辑套餐名、套餐描述、进货价、出售价、排序与启用状态。
- 修复 `POST /api/dashboard/servers/sync/` 中阿里云区域变量未定义导致同步按钮不可用的问题，并让前端同步请求显式携带 `region`。
- dashboard 新增“系统设置”页，补齐 `SiteConfig` / `CloudAccountConfig` 的可视化查看能力，敏感配置展示真实值输入与脱敏预览。
- 套餐列表按需求收敛阿里云地区：dashboard 套餐/价格接口在 `provider=aliyun_simple` 且未指定 `region_code` 时，仅返回 `中国香港` 与 `新加坡`。
- 机器人套餐选择页改为一行 3 个，移除套餐卡片内“加入购物车”；在数量选择页增加“加入购物车”“去购物车支付”；购物车支付按钮调整为一行两个。
- 钱包余额不足提示统一为“余额不足，请先充值”，并增加“去钱包充值”按钮直达充值入口。

### 验证
- 前端已跑到构建阶段并定位缺失导出/接口问题，当前继续收尾验证。
- Django 本地命令依赖当前 shell 未激活虚拟环境，需在 `.venv` 环境下执行 `manage.py check` / 迁移检查。

## v0.5.01 - 2026-04-19
- 补齐后台默认管理员初始化：新增 `ensure_dashboard_admin` 管理命令，支持通过 `DASHBOARD_ADMIN_USERNAME` / `DASHBOARD_ADMIN_PASSWORD` 自动创建或修复 dashboard 登录账号，避免数据库为空时前端登录一直返回 `401`。
- 开发环境默认提供后台初始账号：`admin / Admin@123456`。

### 验证
- `python manage.py ensure_dashboard_admin`
- 后台用户表存在可登录 `staff/superuser`

## v0.5.00 - 2026-04-19
- 修复 `dashboard_api` 兼容登录态接口仍误用 Django 原生 `@login_required` 的问题，避免 `/api/dashboard/user/info`、`/auth/logout`、`/auth/refresh`、`/auth/codes` 在前后端分离场景下返回 `302` 跳转而不是 JSON `401` / 正常响应。

### 验证
- `manage.py check` 通过
- 前端 `vue-tsc --noEmit` 通过
- `/api/dashboard/user/info` 改为走 `dashboard_login_required`

## v0.4.99 - 2026-04-19
- 调整 `biz.tests.CloudServerServicesTestCase` 的换 IP 场景数据，显式补齐旧订单服务开始/到期时间，确保测试真实覆盖“新服务器到期时间继承旧服务器”的业务要求。

### 验证
- 通过 `DJANGO_TEST_REUSE_DB=1` 模式执行 `manage.py test biz.tests.CloudServerServicesTestCase --keepdb`

## v0.4.98 - 2026-04-19
- 为 Django 测试补充数据库兜底配置：支持通过 `MYSQL_TEST_DATABASE` 指定测试库，或通过 `DJANGO_TEST_REUSE_DB=1` 在本地无建库权限时复用当前库执行回归测试。

### 验证
- 通过 `DJANGO_TEST_REUSE_DB=1 C:\Users\Administrator\Desktop\shop\.venv\Scripts\python.exe manage.py test biz.tests.CloudServerServicesTestCase --keepdb`

## v0.4.97 - 2026-04-19
- 为 `biz.services.cloud_servers` 补充正式 Django 单测，覆盖“已删除/IP 已清空订单禁止续费”与“历史订单缺少 `plan_id` 时换 IP 自动回填套餐”两条关键回归场景。

### 验证
- 通过 `C:\Users\Administrator\Desktop\shop\.venv\Scripts\python.exe manage.py test biz.tests.CloudServerServicesTestCase`

## v0.4.96 - 2026-04-19
- 修复更换 IP 的兼容兜底：当历史云订单缺少 `plan_id` 时，会按 `provider + region_code + plan_name` 自动回填匹配可用套餐后再创建新服务器订单，避免换 IP 流程因 `CloudServerOrder.plan` 非空约束失败。

### 验证
- 通过 `C:\Users\Administrator\Desktop\shop\.venv\Scripts\python.exe manage.py shell -c "...mark_cloud_server_ip_change_requested(...)..."` 复现并修复历史订单缺 `plan_id` 的换 IP 创建失败问题

## v0.4.96 - 2026-04-18
- 收口 AWS Lightsail 新机全流程验收：在全新测试机上真实跑通 `创建实例 -> 默认 key 登录 ubuntu -> 设置 root/ubuntu 密码 -> password + keyboard-interactive 复登 root -> 在密码登录后的 root 会话继续安装 -> BBR 生效 -> MTProxy 安装成功`
- 修复 `cloud/bootstrap.py` 中 MTProxy 启动命令提取逻辑，避免 `run-command.sh` 被错误截成 `/mtg` 导致 `No such file or directory`
- 调整 mtg stats 管理端口为 `18888`，规避 `127.0.0.1:8888` 被遗留进程占用时造成的 `cannot initialize stats server` 启动失败
- 增加 MTProxy 安装前的遗留进程清理逻辑，避免旧的 `mtg/mtproto-proxy` 进程与新 systemd 服务互相抢占端口
- 为 `run-command.sh` 与 Secret 解析补充多级兜底：当 `ps` 无法稳定提取运行命令时，回退读取 `/home/mtproxy/config` 构造启动命令，并从 `config` / `run-command.sh` / 进程参数中解析 Secret
- 真实复验通过的新机样本包括 `flowtest-124444`（`13.250.133.199`）与 `flowtest-131325`（`13.250.251.177`）；最终复验确认 `mtproxy.service` 为 `active`、端口 `9528` 正常监听、分享链接成功产出

### 验证
- 通过 `tmp/create_flow_test_instance.py` 真实创建 AWS Lightsail 测试机并获取默认登录信息
- 通过 `tmp/run_full_flow_new_machine.py` 在 `13.250.133.199` 复验 `设密码 -> 密码复登 root -> 密码登录后安装`
- 通过 `tmp/run_full_flow_new_machine_2.py` 在 `13.250.251.177` 复验完整链路，确认 `systemctl is-active mtproxy.service` 返回 `active`
- 通过远端复验确认 `ss -lntup | grep 9528` 可见 `mtg` 监听，且 `ps -ef | grep -iE "/mtg | mtg run |mtproto-proxy"` 能看到稳定运行进程

## v0.4.95 - 2026-04-16
- 增强 `POST /api/dashboard/servers/sync/`：同步阿里云与 AWS 后，会对账当前区域内已存在的 `servers` 记录；若云平台已不存在，则自动将 `servers.is_active` 置为 `False`，并写入“云平台同步未发现该服务器，已标记为不存在”备注
- 调整云厂商状态同步规则：`sync_aliyun_assets` / `sync_aws_assets` 现在按真实云状态更新 `is_active`，仅 `running` / `starting` / `pending` 视为正常状态，关机、停机、删除、过期、终止等状态会自动标记为非正常
- 调整 `GET /api/dashboard/servers/` 排序：按服务器真实状态排序，正常状态优先，再按 `expires_at`、`updated_at` 排序，非正常状态服务器自动排到后面
- 服务器同步进一步收口“云平台不存在”状态：`Server.provider_status` 不再写 `missing/deleted` 英文，统一直接写中文 `已删除`；并在 `sync_servers` 链路中自动修正同地域历史旧数据里残留的 `provider_status='missing'` 记录，避免前端继续看到英文旧值

### 验证
- 通过 `C:\Users\Administrator\Desktop\shop\.venv\Scripts\python.exe manage.py check`

## v0.4.94 - 2026-04-15
- 跑通当前关键验证链路：Django `check`、迁移状态检查、Vben `typecheck`
- 确认 `accounts` 与 `mall` 的新迁移已处于已应用状态，覆盖多用户名、服务器表、云资产价格/币种等改动
- 新增机器人云套餐购物车真实结算链路：购物车中的云套餐现在可直接创建云订单并进入支付/端口选择流程。
- 新增 `mall.CartItem` 正式迁移，并补齐购物车模型落库。
- 调整云服务器生命周期基线：到期前 5 天开始提醒，默认提醒静默 3 天，宽限改为 5 天，删机后 IP 保留改为 15 天。
- 新增云服务器提醒按钮：支持在提醒消息中“关闭提醒 3 天”和“延期 5 天”。
- 新增敏感配置加密层：`core.SiteConfig` 支持敏感项加密存库，后台显示脱敏值。
- 生命周期调度补充迁移到期处理：更换 IP 后旧服务器达到 5 天迁移截止时，会自动删除旧实例并通知用户。
- 后台列表继续调整：服务器列表 API 去掉历史 IP 字段并前置状态字段；代理列表增加同步接口并补充剩余天数倒计时字段。
- 修复更换 IP 新订单创建缺少 `plan_id` 的真实错误，避免因 `CloudServerOrder.plan` 非空约束导致换 IP 流程失败。
- 新增 dashboard 云套餐/价格接口：`GET /api/dashboard/cloud-plans/`、`GET /api/dashboard/cloud-pricing/`、`POST /api/dashboard/cloud-plans/sync/`，用于后台查看套餐列表、价格模板和触发刷新。
- 云套餐列表读取上限从每地区 `6` 个提升到 `9` 个，机器人套餐按钮与文案同步扩展为 `套餐一` 到 `套餐九`。
- 默认套餐模板统一补齐为 `9` 档：AWS 与阿里云在上游未返回足够套餐时，会回退到本地 `9` 套模板并同步入库。
- 新增 dashboard 套餐设置页，接入 `/cloud-plans/`、`/cloud-pricing/`、`/cloud-plans/sync/` 接口，支持查看套餐列表、价格列表和手动同步。
- 调整 dashboard 服务器列表：删除历史 IP 列影响后的表头顺序，状态列前移，并在状态下展示倒计时/云厂商原始状态。
- 调整 dashboard 代理列表：保留同步按钮，原始状态统一改为倒计时优先展示，不足时回退显示云厂商状态。
- 复核云服务器生命周期链路：到期前 5 天提醒、关闭提醒 3 天、延期 5 天、删机前 1 天提醒、IP 删除前 1 天提醒、换 IP 后 5 天迁移截止删除旧机。
- 完善敏感配置后台管理：补齐机器人 Token、M 账号 Token、MySQL/Redis/数据库连接、收款地址等配置项说明与后台分组，沿用数据库加密存储。

### 验证
- 通过 `./.venv/Scripts/python.exe manage.py check`
- 通过 `./.venv/Scripts/python.exe manage.py showmigrations accounts mall`
- 通过 `pnpm -C C:\Users\Administrator\Desktop\vue-vben-admin --filter @vben/web-antd typecheck`

## v0.4.93 - 2026-04-15
- 重构 Telegram 多用户名存储：主数据改为真实写入 `users.username`，使用逗号保存全部用户名，并保留 `telegram_usernames` 兼容索引
- 新增 `backfill_usernames_to_users` 回填命令，修复历史多用户名未真实写入 `user` 表的问题
- 收紧后台编辑接口权限：余额修改、云资产编辑要求登录且具备 `staff/superuser`
- 补齐服务器同步链路：阿里云同步同时写入 `cloud_assets` 与 `servers`，并直接写入实际到期时间
- 验证要求更新：迁移、回填、Django check、云资产/API 编辑、前端 typecheck


### 新增
- 增强 `GET /api/dashboard/cloud-assets/`，默认返回精简字段，并支持 `grouped=1` 按用户分组展示云资产
- 新增 `POST /api/dashboard/cloud-assets/<id>/`，支持更新资产用户绑定、到期时间、代理链接、备注、启用状态，并同步关联订单价格/到期时间/用户
- 增强 Vben `云资产列表` 页面，支持按用户分组展示服务器与 `MTProxy`，并提供资产编辑弹窗

### 调整
- 更新机器人用户同步逻辑，保留 Telegram 历史用户名，仅切换当前主用户名标记
- 增强 Django Admin 云资产管理，列表与搜索更偏向用户、价格、代理链接和到期时间，并支持资产页回写订单价格
- 增强阿里云同步命令，解析 `ExpiredTime` / `ExpireTime` / `ExpirationTime` / `EndTime` 并写入实际到期时间
- 增强手工资产录入命令，新增 `--user` 参数，支持按后台用户 ID、Telegram 用户 ID 或用户名绑定用户

### 验证
- 通过 `python manage.py check`
- 通过 `python manage.py showmigrations accounts mall`
- 通过 `pnpm --filter @vben/web-antd typecheck`

## v0.4.91 - 2026-04-15

### 新增
- 新增 `accounts.TelegramUsername` 模型，用于持久化 Telegram 多个当前用户名，并在后台提供 inline 编辑入口
- 新增统一云资产模型 `mall.CloudAsset`，用于同时记录云服务器与 `MTProxy` 资产，支持绑定用户/订单留空、记录实际到期时间
- 新增 Django API：`POST /api/dashboard/users/<id>/balance/` 与 `GET /api/dashboard/cloud-assets/`
- 新增管理命令：`python manage.py sync_aliyun_assets --region <region>` 与 `python manage.py upsert_cloud_asset ...`

### 调整
- 更新用户同步逻辑，创建/更新 `TelegramUser` 时同步保存全部用户名到独立关联表
- 更新云服务器开通流程，创建成功后自动把服务器资产与 `MTProxy` 资产写入统一资产表
- 更新 Vben `用户列表` 页面，支持直接弹窗修改 `USDT/TRX` 余额
- 新增 Vben `云资产列表` 页面，用于查看统一资产表中的服务器与 `MTProxy` 记录
- 补充统一云资产后台管理入口，为 AWS 手工录入与后续绑定用户/订单提供可视化编辑面板

## v0.4.90 - 2026-04-15

### 调整
- 按联调需要执行本地 OpenClaw gateway 重启，恢复控制面与网关联通状态

## v0.4.89 - 2026-04-15

### 调整
- 将 Telegram 多用户名语义正式改为当前用户名集合，接口字段统一为 `usernames` / `primary_username` / `username_label`
- 更新 Vben 用户列表，展示名、多个用户名标签、主用户名与余额信息分开展示

## v0.4.88 - 2026-04-15

### 调整
- 因联调卡顿，重启本地 Django 8000 与 Vben 5666 开发服务，恢复前后端联调状态

## v0.4.87 - 2026-04-15

### 调整
- 增强 `/api/dashboard/users/` 返回结构，新增 `display_name`、`primary_username`、`username_history`、`username_label`
- 兼容 Telegram 用户名变更或手工存储多个用户名的展示场景，优先用昵称，其次用首个用户名

## v0.4.86 - 2026-04-15

### 调整
- 移除 Vben 侧栏中的 `Django Admin` 入口，继续收敛为纯业务菜单
- 清理顶部默认通知、文档、GitHub、问答等示例项，仅保留精简后的用户下拉与退出能力

## v0.4.85 - 2026-04-15

### 调整
- 将 Vben 默认首页从分析页切换为工作台
- 新增 `Django Admin` 一级菜单入口，并提供打开原生 Django 后台的跳转页
- 后端登录返回的 `homePath` 同步切换为 `/workspace`

## v0.4.84 - 2026-04-15

### 调整
- 移除 `vue-vben-admin` 中原有的项目示例与演示菜单，仅保留当前业务后台相关路由
- 限制 `apps/web-antd` 只加载 `dashboard.ts` 业务路由模块，进一步收敛侧栏结构

## v0.4.83 - 2026-04-15

### 调整
- 移除原有 `dashboard` 分组导航，将数据概览、工作台、用户列表、云订单列表、充值列表改为一级菜单
- 保留页面访问路径不变，仅调整左侧导航结构，便于直接进入核心业务页

## v0.4.82 - 2026-04-15

### 调整
- 将 Vben `analytics` 分析页改为读取 `shop` 后端真实概览数据
- 分析页现展示真实业务指标、处理进度、最近云订单、最近充值与待处理数量

## v0.4.81 - 2026-04-14

### 调整
- 为方案 B 联调阶段放宽 `dashboard_api` 的登录/登出/刷新接口 CSRF 校验，避免 Vben 登录被 Django 默认 CSRF 拦截
- 保持 Django Session 鉴权方案不变，优先确保本地前后端联调可用

## v0.4.80 - 2026-04-14

### 新增
- 新增 Django 列表接口：`/api/dashboard/users/`、`/api/dashboard/cloud-orders/`、`/api/dashboard/recharges/`
- 为 Vben 新增三个真实业务页：用户列表、云订单列表、充值列表

### 调整
- 扩展 Vben `dashboard` 路由，挂载真实业务列表页面
- 继续以 Django 真实数据替换默认示例内容，推进方案 B 联调

## v0.4.79 - 2026-04-14

### 新增
- 为 `vue-vben-admin/apps/web-antd` 新增 `src/api/dashboard.ts`，用于请求 Django 工作台总览接口

### 调整
- 将 Vben `workspace` 工作台页面改为读取 `shop` 后端的真实概览数据
- 工作台页面现展示真实指标卡、最近云订单、最近充值、待处理事项与快捷导航

## v0.4.78 - 2026-04-14

### 新增
- 为 Vben Admin 前端对接新增兼容接口：`/api/dashboard/auth/login`、`/api/dashboard/auth/logout`、`/api/dashboard/auth/refresh`、`/api/dashboard/auth/codes`、`/api/dashboard/user/info`
- 保留已有 `/api/dashboard/dashboard/me/` 与 `/api/dashboard/dashboard/overview/` 作为业务工作台数据接口

### 调整
- 将 `vue-vben-admin/apps/web-antd` 开发代理改为转发到本地 Django：`http://127.0.0.1:8000/api/dashboard`
- 关闭 `web-antd` 开发环境的 Nitro Mock，改为直接请求真实 Django 后端

## v0.4.77 - 2026-04-14

### 新增
- 新增 `dashboard_api/`，作为方案 B（前后端分离后台）的 Django API 起步骨架
- 新增 `/api/dashboard/me/` 与 `/api/dashboard/overview/` 两个基础接口
- 新增 `dashboard_web/README.md`，用于承接后续 Vue / Vben Admin 前端接入说明

### 调整
- 将 `dashboard_api` 注册到 `INSTALLED_APPS`
- 在 `shop/urls.py` 中挂载 `/api/dashboard/` 路由，为独立后台前端做准备

## v0.4.76 - 2026-04-14

### 调整
- 后台补齐可见导航栏，并在窄屏下改为顶部按钮触发的抽屉式侧栏
- 首页工作台改为真正响应式布局，卡片区会随屏幕宽度自动折叠为两列或单列
- 继续美化后台整体层级，统一卡片、按钮、阴影和信息区块样式

### 修复
- 修复此前后台在移动端下导航与首页卡片不够友好的问题

## v0.4.75 - 2026-04-14

### 调整
- 继续细化后台参考图风格，增强列表页、筛选器、分页、表单行与删除按钮的卡片化视觉
- 后台内容区进一步统一白色卡片、圆角边框、浅阴影与蓝色主操作按钮层级

### 修复
- 修复部分 Django Admin 表单页仍保留原生边框与视觉密度不统一的问题

## v0.4.74 - 2026-04-13

### 调整
- 后台主题继续向参考图靠拢，整体改为更明显的 `浅色顶栏 + 深色左侧导航 + 白色卡片内容区` 布局
- 左侧导航新增品牌区与更强的激活态，首页工作台卡片改为 `总览横幅 / 待处理提醒 / 业务指标 / 快捷入口 / 说明面板` 分区
- 表格、筛选器、按钮、输入框、消息提示统一使用更圆润圆角、浅阴影和更接近运营面板的视觉层级

### 修复
- 继续弱化 Django Admin 原生页面的“列表页感”，让首页与内容页更统一接近仪表盘样式

## v0.1.0 - 2026-04-11

### 新增
- 初始化 `Django + aiogram + MySQL + Redis` 项目骨架
- 新增 `run.py` 一键启动入口
- 新增商品、订单、充值、用户、地址监控等基础模型与后台管理
- 新增 `aiogram` 机器人主流程：购买商品、我的订单、充值余额、充值记录、地址监控、个人中心
- 新增 TRON 转账扫描器，用于监控转账与自动匹配订单/充值
- 新增 TRON 资源巡检器，用于监控可用能量与带宽变化
- 新增 Redis 缓存层，用于配置缓存、监控地址缓存、FSM 状态存储

### 调整
- 主菜单改为 `🛒 购买商品` 与 `👤 个人中心`
- 将 `我的订单 / 充值余额 / 充值记录 / 地址监控` 收拢到个人中心
- 个人中心按钮改为一行两个
- 地址监控新增 `监控转账`、`监控资源` 两个开关

### 修复
- 修复 Telegram 按钮回调过长导致的 `BUTTON_DATA_INVALID`
- 修复开发期多实例冲突与若干启动问题

## v0.1.1 - 2026-04-11

### 调整
- 优化转账通知卡片样式，加入表情与代码标签
- 优化日志策略，改为 10 分钟摘要 + 命中时详细日志
- 增加监控说明文案

### 修复
- 修复手续费显示为 `未知` 的问题，改为链上查询真实手续费信息
- 修复资源通知详情缓存与短回调机制

## v0.2.0 - 2026-04-11

### 新增
- 新增 Redis 每日临时统计
- 新增资源通知详情按钮
- 新增交易详情按钮
- 新增真实的每日 `收入 / 支出 / 利润` 统计逻辑

### 调整
- 每日统计按日期分桶，自动在次日切换，相当于每天 0 点清零
- 利润计算调整为 `收入 - 支出`
- 允许负利润展示，例如 `-100 USDT`

### 修复
- 修复今日收入 / 支出 / 利润曾按单笔假值展示的问题
- 修复转出仅计入统计、不发通知的问题，现在转出会发送 `🔴 支出提醒`
- 修复资源监控导入异常：补回 `tron.resource_checker.set_bot(...)`
- 修复 `tron.scanner` 中支出提醒分支漏定义 `from_addr` 导致的扫块异常

## v0.2.1 - 2026-04-11

### 新增
- 新增 `biz/` 业务聚合层，统一导出用户、商品、订单、充值、监控等核心模型

### 调整
- `tron/` 与 `bot/` 业务代码开始改为优先从 `biz.models` 引用业务模型
- `shop/` 继续保持为 Django 项目配置目录，不与业务目录混用
- 为后续将 `users / shopbiz / payments / monitors` 逐步收拢到统一业务层做准备

### 修复
- 继续控制目录复杂度，避免后续功能增多后结构混乱

## v0.2.2 - 2026-04-11

### 新增
- 新增 `ARCHITECTURE.md`，明确后续目录收敛与分层迁移方案

### 调整
- 明确采用 `shop / core / biz / bot / tron` 的长期结构方向
- 继续按分阶段迁移方式收拢目录，避免一次性合并导致迁移链与模型引用混乱

### 修复
- 降低后续目录扩张失控的风险，为继续开发预留稳定结构

## v0.2.3 - 2026-04-11

### 新增
- 新增 `mall/` 命名层，作为 `shopbiz/` 的对外替代命名

### 调整
- `biz.models` 现改为通过 `mall.models` 导出商品与订单模型
- 项目结构开始对外统一使用更清晰的 `mall` 命名，减少 `shop / shopbiz` 混淆
- 当前仍保留 `shopbiz/` 作为兼容层，避免直接破坏迁移与历史依赖

### 修复
- 降低 `shop` 与 `shopbiz` 命名过近带来的理解成本

## v0.2.4 - 2026-04-11

### 新增
- 新增 `accounts/` 用户账户命名层
- 新增 `finance/` 财务命名层
- 新增 `monitoring/` 监控命名层

### 调整
- `biz.models` 改为统一通过 `accounts / mall / finance / monitoring` 聚合业务模型
- 项目开始对外统一使用更直观的业务命名，进一步降低旧 app 目录带来的混乱

### 修复
- 修复 `biz.models` 误写为自引用导入的问题

## v0.2.5 - 2026-04-11

### 调整
- 为旧业务 app 增加“兼容层”标识，便于在 Django Admin 与工程结构中区分新命名层和旧目录
- 继续明确 `accounts / mall / finance / monitoring` 为未来新增功能的首选命名入口

### 修复
- 降低后台与目录阅读时对旧目录用途的误解

## v0.2.6 - 2026-04-11

### 新增
- 新增 `core/cache.py`，承载 Redis 连接、配置缓存、每日统计等公共缓存能力
- 新增 `monitoring/cache.py`，承载地址监控缓存逻辑

### 调整
- 将原 `tron/cache.py` 中的公共缓存逻辑拆分到 `core` 与 `monitoring`
- `tron/` 目录进一步收敛为链扫描与资源监控职责

### 修复
- 降低 `tron/cache.py` 作为公共缓存入口带来的职责混杂问题

## v0.2.7 - 2026-04-11

### 调整
- 将业务模型定义正式迁入 `accounts / mall / finance / monitoring`
- 旧目录 `users / shopbiz / payments / monitors` 改为仅保留兼容导出
- 新旧结构开始从“命名别名”进入“模型主定义迁移”阶段

### 修复
- 为最终删除旧目录提前完成模型主入口切换

## v0.2.8 - 2026-04-11

### 调整
- 将 Django Admin 注册入口迁入 `accounts / mall / finance / monitoring`
- 旧目录的 `admin.py` 改为空兼容壳，避免重复注册模型

### 修复
- 为后续移除旧目录提前完成后台入口迁移

## v0.2.9 - 2026-04-11

### 调整
- `INSTALLED_APPS` 已切换为新结构主入口：`accounts / mall / finance / monitoring`
- 旧 app 不再作为 Django 主注册入口加载，进一步逼近最终删除目标

### 修复
- 降低旧目录继续作为运行时主入口带来的结构混乱

## v0.3.0 - 2026-04-11

### 调整
- 删除旧业务目录中的运行时代码文件，仅保留历史迁移内容
- `users / shopbiz / payments / monitors` 不再承担运行时职责

### 修复
- 进一步逼近“删除旧目录、仅保留新结构”的目标，同时保留 Django 迁移历史安全边界

## v0.3.1 - 2026-04-11

### 调整
- 直接删除旧业务目录 `users / shopbiz / payments / monitors`
- 项目目录仅保留新结构与正在使用的模块

### 修复
- 完成旧业务目录物理删除，减少后续维护噪音与误用风险

## v0.3.2 - 2026-04-11

### 调整
- 删除 `tron/cache.py` 兼容壳文件
- 缓存职责彻底固定为 `core/cache.py` 与 `monitoring/cache.py`

### 修复
- 完成 `tron` 目录缓存兼容层清理，避免再次混入公共缓存职责

## v0.3.3 - 2026-04-11

### 修复
- 修复 `tron/scanner.py` 缺失 `parse_usdt_transfer` / `parse_trx_transfer` 导入导致的扫块异常

## v0.3.4 - 2026-04-11

### 新增
- 新增 `biz/services/` 二级目录，承载统一业务编排与查询入口
- 按职责拆分为 `users.py`、`commerce.py`、`payments.py`、`monitoring.py`

### 调整
- `bot/services.py` 改为兼容导出层，实际业务逻辑迁入 `biz/services/`

### 修复
- 减少机器人层直接承载业务逻辑，进一步稳定目录职责边界

## v0.3.5 - 2026-04-11

### 调整
- 删除 `bot/services.py` 兼容层
- `bot/handlers.py` 直接依赖 `biz.services`

### 修复
- 完成机器人层业务兼容壳清理，减少重复入口

## v0.3.6 - 2026-04-11

### 新增
- 新增 `core/formatters.py` 作为公共格式化工具入口
- 新增 `biz/services/rates.py` 承载汇率与换算逻辑

### 调整
- `bot/handlers.py` 改为直接依赖 `biz.services` 与 `core.formatters`
- `bot/handlers.py` 中订单详情查询改为走 `get_order(...)`，移除直接 ORM 查询
- 删除 `bot/utils.py` 与 `bot/exchange.py` 兼容层文件

### 修复
- 继续收缩机器人层中的业务与工具逻辑，减少职责混杂

## v0.3.7 - 2026-04-11

### 调整
- 将地址监控一级键盘改为一行两个按钮
- 将地址监控二级键盘改为优先一行两个按钮显示

### 修复
- 优化地址监控菜单排版，减少纵向过长问题

## v0.3.8 - 2026-04-11

### 调整
- 首页主键盘移除 `🛒 购买商品`
- 首页新增 `✨ 订阅`、`🛠 定制`、`🔎 查询` 三个入口按钮
- `✨ 订阅` 当前接入原商品购买列表，作为新的首页购买入口

### 修复
- 让首页入口更贴近业务表达，同时保留原购买流程可用

## v0.3.9 - 2026-04-11

### 新增
- 新增云服务器套餐模型 `CloudServerPlan` 与订单模型 `CloudServerOrder`
- 定制入口支持按地区展示套餐价格，并生成独立云服务器订单
- 扫链支付匹配新增云服务器订单到账处理，付款后自动进入创建流程状态

### 调整
- 定制流程改为：选择地区 → 查看价格表 → 生成订单 → 监控到账 → 进入创建流程
- 地区过滤规则改为：阿里云仅显示香港和 AWS 不具备的海外地区，不展示中国内地区

### 修复
- 将定制能力接入现有支付监控链路，避免手工确认到账

## v0.4.0 - 2026-04-11

### 新增
- 新增 `cloud/` 目录，封装 `AWS 光帆服务器` 与 `阿里云轻量云` 创建接口预留层
- 新增云服务器订单实例字段：`instance_id / public_ip / login_user / login_password / image_name`
- 新增 AK/SK 预留环境变量与默认镜像配置 `DEFAULT_SERVER_IMAGE=debian`

### 调整
- 云服务器订单到账后，扫描器会进一步触发创建流程封装
- AWS 登录方式按密码登录方案预留接口

### 修复
- 为后续接入真实云 API 打通订单到账 → 创建回传 的结构化链路

## v0.4.1 - 2026-04-11

### 新增
- 新增 `cloud/bootstrap.py`，预留 Debian 创建后初始化与 BBR 加速脚本
- 云服务器创建成功后，结构上会继续进入 BBR 初始化步骤

### 调整
- 默认云服务器初始化流程扩展为：创建实例 → 回写凭据 → 执行 BBR 初始化

### 修复
- 为后续自动装机与网络加速准备统一 bootstrap 入口

## v0.4.2 - 2026-04-11

### 新增
- BBR 初始化改为支持真实 SSH 密码登录执行
- 新增 `paramiko` 依赖用于创建后远程执行 Debian 初始化脚本

### 调整
- 云服务器创建成功后会尝试自动通过 SSH 执行 BBR 安装脚本

### 修复
- 打通了创建实例后的远程初始化执行入口，不再只是占位逻辑

## v0.4.3 - 2026-04-11

### 新增
- Debian 初始化链路新增 MTProxy 安装脚本
- 创建成功后的自动化步骤扩展为：安装 BBR 后继续安装 MTProxy

### 调整
- `cloud/provisioning.py` 现在会串行执行 `install_bbr(...)` 和 `install_mtproxy(...)`

### 修复
- 为后续一键交付代理服务器补齐基础软件安装步骤

## v0.4.4 - 2026-04-11

### 调整
- MTProxy 默认安装目录改为 `/home/mtproxy1`
- MTProxy 默认端口改为非默认端口 `8443`

### 修复
- 避免继续使用默认目录与默认端口

## v0.4.5 - 2026-04-11

### 调整
- MTProxy 默认端口改为 `9528`
- Debian 初始化脚本增加 MTProxy 端口放行逻辑（`ufw allow 9528/tcp` 与 `ufw allow 9528/udp`）

### 修复
- 避免继续使用 8443，并在初始化阶段同步放行指定端口

## v0.4.6 - 2026-04-11

### 调整
- MTProxy 安装目录改回默认目录 `/home/mtproxy`
- 云服务器订单新增 `mtproxy_port` 字段，支持付款后选择默认端口或输入自定义端口
- 默认端口说明改为 `9528`

### 修复
- 安装目录恢复默认，同时把端口选择从固定值升级为按订单可配置

## v0.4.7 - 2026-04-12

### 调整
- 将 FSM 存储初始化从 `bot/handlers.py` 抽离到 `bot/fsm.py`
- 统一封装 Redis FSM / Memory 回退 / Redis 连接复用与关闭清理

### 修复
- 让 `handlers.py` 只保留交互注册职责，降低后续状态扩展复杂度

## v0.4.8 - 2026-04-12

### 调整
- 将 `MonitorStates`、`RechargeStates`、`CustomServerStates` 拆分到 `bot/states/` 目录
- 新增 `bot/states/__init__.py` 作为统一状态调度出口

### 修复
- 让状态定义与处理器逻辑解耦，便于后续继续扩展更多流程状态

## v0.4.9 - 2026-04-12

### 调整
- 云服务器实例名规则改为 `时间戳-用户ID-金额`
- 新增统一实例名生成函数，供 AWS / 阿里云创建流程复用

### 修复
- 让后续真实云创建接口的命名规则与订单信息保持一致

## v0.4.10 - 2026-04-12

### 调整
- 云服务器下单、到账、创建成功通知中展示服务器名
- MTProxy 安装完成后尝试提取 secret，并生成 `tg://proxy` 与 `https://t.me/proxy` 链接回传给用户
- AWS Lightsail 创建目标补充固定公网 IP 要求，后续真实 API 接入时必须申请并绑定 Static IP

### 修复
- 创建成功通知补充 MTProxy 链接信息，避免用户还要手动拼接代理链接

## v0.4.11 - 2026-04-12

### 调整
- 用户侧不再展示云服务器名、登录账号、公网 IP、登录密码
- 云服务器创建完成后仅向用户发送 MTProxy 链接与必要说明

### 修复
- 避免向终端用户暴露服务器管理信息，只保留代理使用信息

## v0.4.12 - 2026-04-12

### 调整
- 接入 AWS Lightsail 最小可用创建流程：创建 Debian 实例、设置 `admin` 密码登录、申请并绑定 Static IP
- 运行期通过 `.env` 读取 AWS AK/SK，并可直接调用真实 Lightsail API

### 验证
- 已实际调用 AWS Lightsail 创建测试实例并成功返回固定公网 IP

## v0.4.13 - 2026-04-12

### 调整
- 修复 Debian BBR 初始化脚本的 `sudo`、`sysctl` 与 heredoc 兼容问题
- 修复 MTProxy 安装脚本在非 root 登录下的目录权限问题
- 实测 AWS 测试实例上 BBR 与 MTProxy 安装成功，并成功生成代理链接

### 修复
- 让 `admin` 用户密码登录场景下也能完成 BBR 与 MTProxy 自动安装

## v0.4.14 - 2026-04-12

### 调整
- 删除 AWS 测试实例与其 Static IP，停止继续计费
- 为云服务器订单补充生命周期字段：服务器名、MTProxy 链接/密钥、服务开始/到期、3天续费宽限、3天关机删除、IP 保留 10 天、最近绑定用户等
- 管理后台列表增加服务器名、IP、服务到期、IP 保留到期等关键字段

### 规划落地
- 默认有效期 31 天
- 到期未续费先保留 3 天，再关机 3 天后删机
- 删机后 IP 继续保留 10 天，只要 IP 仍在就允许续费
- 预留后台更换绑定用户与后续更换 IP 的数据基础

## v0.4.15 - 2026-04-12

### 调整
- 新增云服务器生命周期调度器 `cloud/lifecycle.py`，每 10 分钟检查到期、关机、删机、IP 保留到期
- 新增云服务器续费、改绑用户、更换 IP 请求的服务层接口预留

### 修复
- 为后续后台管理改绑用户、续费与 IP 替换提供统一服务入口

## v0.4.16 - 2026-04-12

### 调整
- 新增用户侧云服务器查询列表与详情页
- 新增续费 31 天下单入口，到账后自动顺延到期时间
- 新增用户侧更换 IP 请求入口

### 修复
- 只要 IP 仍处于保留期，用户即可继续发起续费恢复服务

## v0.4.17 - 2026-04-12

### 调整
- 后台编辑云服务器订单时，如修改 `public_ip` 或 `mtproxy_port`，会自动重建 MTProxy 链接
- 后台新增“重发 MTProxy 链接给用户”批量动作
- 更换 IP 后可以直接重新下发新链接给绑定用户

## v0.4.18 - 2026-04-12

### 修复
- 修复首页 `🛠 定制` 按钮仍停留在占位文案的问题，现已直接进入地区选择
- 修复云服务器查询回调插入后影响商品详情回调绑定的问题

## v0.4.19 - 2026-04-12

### 调整
- 后台支持直接改绑云服务器订单用户，并写入操作记录
- 后台支持手动续费/恢复 31 天
- 后台支持手动恢复订单状态为 `已创建`
- 后台更换 IP/端口时会明确记录“旧链接失效，新链接已生成”

## v0.4.20 - 2026-04-12

### 修复
- 定制地区列表改为优先通过云厂商 API 同步真实可用地区，不再完全依赖手写静态地区表
- AWS 地区通过 Lightsail `get_regions` 实时同步；阿里云在未配置凭证时暂不展示

## v0.4.21 - 2026-04-12

### 调整
- AWS 套餐价格改为读取 Lightsail 实际套餐价格后，按 `实际价格 * 2 + 5` 自动计算展示价
- 套餐同步时会更新已有套餐价格，不再只在首次创建时写入

## v0.4.22 - 2026-04-12

### 调整
- 定制地区按钮改为每行 3 个
- AWS 套餐列表排除最小套餐 `Nano`
- 套餐按钮改为通用命名 `套餐一 / 套餐二 / ...`，每行 3 个，最多两行展示 6 个
- 套餐说明文案去掉“光帆服务器”字样，改为更中性的规格展示

## v0.4.23 - 2026-04-12

### 调整
- 补全 AWS 地区中文映射，地区按钮尽量全部以中文展示
- AWS 套餐扩展为 6 个档位：`Micro / Small / Medium / Large / Xlarge / 2Xlarge`
- 定制套餐页固定展示 6 个套餐按钮（两行，每行 3 个）

## v0.4.24 - 2026-04-12

### 调整
- 定制套餐说明文案全部改为中文化展示
- 套餐详情改为中文字段：算力档位 / 内存 / 硬盘 / 流量 / 价格
- AWS 档位标识改为中文表述：微型 / 小型 / 中型 / 大型 / 超大型 / 双倍超大型

## v0.4.25 - 2026-04-12

### 调整
- 套餐说明中的“算力档位”改为显示实际 `CPU` 核数
- 套餐文案中隐藏“流量”字段
- AWS 套餐同步已接入 `cpuCount`，因此 CPU 核数可从 API 直接获取

## v0.4.26 - 2026-04-12

### 修复
- 修正 AWS 套餐同步模板字段拆包错误
- 定制套餐说明现使用 API 返回的 `cpuCount` 显示 CPU 核数

## v0.4.27 - 2026-04-12

### 调整
- 定制地区页改为“热门地区 + 更多地区”结构
- 默认先显示 6 个热门地区，两行每行 3 个
- 点击“更多地区”后再展开完整地区列表，并支持收起

## v0.4.28 - 2026-04-12

### 调整
- 热门地区排序改为优先 `新加坡`、`香港`

## v0.4.29 - 2026-04-12

### 调整
- 热门地区由 6 个改为 5 个，并保留“更多地区”入口
- 热门地区除指定的 `新加坡`、`香港` 外，其余优先从亚太地区中补足
- “更多地区”页改为展示剩余地区，不再重复热门地区

## v0.4.30 - 2026-04-12

### 调整
- 热门地区第五位改为美国，替换雅加达
- 热门地区固定优先：新加坡、香港、东京、首尔、美国

## v0.4.31 - 2026-04-12

### 调整
- 热门地区页改为两排展示：第一排 3 个地区，第二排 2 个地区 + 更多地区
- 更多地区页移除“返回主菜单”按钮
- “收起地区”按钮改为“返回”，并回到上级热门地区菜单

## v0.4.32 - 2026-04-12

### 修复
- 香港等阿里云地区也补齐为 6 个套餐
- 未配置阿里云 API 凭证时，会基于已有启用地区补齐 6 档套餐，避免套餐页不足 6 个按钮

## v0.4.33 - 2026-04-12

### 调整
- 阿里云轻量云地区同步切换到新域名 `swas.cn-hangzhou.aliyuncs.com`
- 阿里云地区列表改为从真实 API 拉取
- 阿里云套餐档位改为从真实 `ListPlans` 接口同步，并自动换算销售价与带宽/CPU/硬盘配置

## v0.4.34 - 2026-04-12

### 调整
- 阿里云轻量云创建流程改为真实 `CreateInstances / ListImages / ListPlans / UpdateInstanceAttribute` 接口实现
- 实测创建时返回阿里云账号侧校验错误 `NO_REAL_REGISTER_AUTHENTICATION`
- 当前阻塞点已确认不是代码参数错误，而是账号未完成阿里云要求的实名认证校验

## v0.4.35 - 2026-04-12

### 调整
- 阿里云创建时按目标地区切换到对应区域端点，例如香港使用 `swas.cn-hongkong.aliyuncs.com`
- 实测香港创建已越过实名认证报错，当前返回库存不足 `NotEnoughStock`
- 这表明香港海外区创建链路已打通到真实下单阶段，当前阻塞为阿里云库存而非鉴权

## v0.4.36 - 2026-04-12

### 调整
- 香港创建重试逻辑增加按套餐类型优先级分层尝试
- 创建诊断信息现在会记录每档套餐的失败结果，便于定位是库存问题还是阿里云服务端异常
- 香港最新实测仍返回 `InternalError`，说明问题更偏向阿里云香港区服务端而非单一套餐档位

## v0.4.37 - 2026-04-12

### 调整
- 阿里云创建链路改为更贴近轻量云流程：创建实例后单独执行 `ResetSystem` 下发系统镜像与登录密码
- 实例名与密码下发拆分处理，不再直接沿用 AWS 式“创建后立即改密码”的思路
- 阿里云初始化逻辑开始与 AWS 明确分离，便于后续继续针对香港区做专用适配

## v0.4.38 - 2026-04-12

### 调整
- 阿里云创建后新增 `停机 → ResetSystem → 启动` 适配流程，进一步贴近轻量云实例初始化方式
- 香港最新实测返回 `IncorrectInstanceStatus`，说明实例创建后的状态切换时序仍需针对阿里云单独适配
- 这进一步确认阿里云创建方法与 AWS 不同，后续需要继续按实例状态机细化等待与切换

## v0.4.39 - 2026-04-12

### 调整
- 阿里云创建流程取消“停机 → ResetSystem → 启动”链路
- 改回在线创建后直接设置实例名与密码，更符合当前阿里云轻量云使用方式
- 阿里云实例的到期控制继续使用云厂商自带到期时间，不引入额外停机步骤

## v0.4.40 - 2026-04-12

### 调整
- `shop` 的阿里云建机逻辑已对齐 `mtproxy-py` 的原生 SWAS 创建方式
- 现在使用 `create_instances_with_options`、`list_instances_with_options`、`update_instance_attribute_with_options` 和统一 `RuntimeOptions`
- 创建阶段改为仅负责建机、等待实例可见/运行、设置实例名；密码初始化留给后续 SSH/重装链路处理

## v0.4.41 - 2026-04-12

### 调整
- 阿里云建机链路继续对齐 `mtproxy-py`，新增 keypair 预备与 `ResetSystem` 下发 root 密码步骤
- 创建成功后会等待实例重新可见并再次进入 `Running`，以便后续 SSH/BBR/MTProxy 安装直接复用
- 这一步专门用于解决香港实例默认仅允许 `publickey`、密码 SSH 不可用的问题

## v0.4.42 - 2026-04-12

### 调整
- 香港阿里云后续安装链路新增 SSH 22 端口就绪等待
- 当实例尚未开放 SSH 或系统仍在初始化时，会明确返回端口未就绪原因，而不是直接报 Paramiko 超时
- 这一步用于区分“密码错误/认证失败”和“22 端口根本没开放”的问题

## v0.4.43 - 2026-04-12

### 调整
- 云服务器自动开通链路已按最新阿里云香港实测结果回接
- 仅当 `建机 + BBR + MTProxy` 都成功时，订单才会标记为 `completed`
- 若 BBR 或 MTProxy 任一步失败，订单会保留失败说明并标记为 `failed`，避免错误地向用户发送成功通知

## v0.4.44 - 2026-04-12

### 调整
- 自动开通链路新增“SSH 密码登录就绪等待”，不再只判断 22 端口是否开放
- 阿里云 `ResetSystem` 后会继续等待密码登录真正生效，再执行 BBR 和 MTProxy 安装
- 这用于修复自动开通里实例已创建但密码尚未生效、导致过早判定失败的问题

## v0.4.45 - 2026-04-12

### 修复
- 自动开通长时间执行后保存订单结果前会主动刷新 Django 数据库连接
- 修复 `建机 + BBR + MTProxy` 已完成但 MySQL 连接超时，导致最终订单无法写回的问题
- 这可避免长链路云开通任务因数据库空闲连接断开而误报失败

## v0.4.46 - 2026-04-12

### 修复
- Redis FSM 存储新增断线容错，遇到 Redis 写 socket 失败时会回退到内存态而不是直接把 bot 更新处理打崩
- Redis 连接增加 `socket_keepalive`、`health_check_interval` 和超时重试，降低 Windows 环境下偶发断链影响
- 这可缓解 `redis.exceptions.ConnectionError: Error 22 while writing to socket` 导致的 bot 状态读取失败

## v0.4.47 - 2026-04-12

### 修复
- 修复 Redis FSM 容错包装里 `super(): no arguments` 的调用错误
- 现在 Redis 存储会正确调用 `RedisStorage` 基类方法，再按需回退内存态
- 避免 bot 每次取状态都刷出误报警告

## v0.4.48 - 2026-04-12

### 调整
- 地区路由规则已固定：`香港` 只走阿里云，其他地区只走 AWS
- 地区列表不再把阿里云非香港区域混入用户选择页
- 套餐查询也按地区强制选择云厂商，避免同地区多云混杂导致按钮/列表错乱

## v0.4.49 - 2026-04-12

### 调整
- 定制首页热门地区按钮文案收敛为：`新加坡 / 香港 / 东京 / 首尔 / 美国 / 更多`
- 香港地区在地区列表里强制优先显示阿里云版本，不再被 AWS 香港覆盖
- 首页“更多地区”按钮文案按最新要求简化为“更多”

## v0.4.50 - 2026-04-12

### 修复
- 修复阿里云地区逐个同步时互相覆盖的问题，避免 `cn-hongkong` 在后续地区同步中被错误停用
- 现在阿里云地区会先按完整地区集合处理，再逐区刷新套餐，香港入口可稳定保留
- 这同时修复了定制页看不到香港、点击地区后可选套餐异常的问题

## v0.4.51 - 2026-04-12

### 修复
- TRON 扫块任务改为内部锁跳过重叠执行，避免 APScheduler 因 `max_instances=1` 持续刷跳过警告
- APScheduler 日志级别进一步收敛到错误级别，保留真正异常，隐藏正常重叠跳过噪声
- 扫块仍保持 2 秒触发频率，但上一轮未结束时会静默等待下一轮

## v0.4.52 - 2026-04-12

### 调整
- 定制订单创建页收敛为仅显示 `支付金额`、`支付地址` 与返回按钮，不再在下单时提前显示端口设置按钮
- 云服务器定制现已支持 `USDT` 与 `TRX` 地址支付，`TRX` 金额按实时汇率从 USDT 价格换算
- 端口选择改为支付成功后再提示，符合“先支付、后选端口、再开通”的流程

## v0.4.53 - 2026-04-12

### 调整
- 云服务器定制新增钱包支付，支持 `钱包 USDT` 与 `钱包 TRX`
- 钱包支付成功后会直接进入“选择端口”步骤，不再要求链上转账
- 地址支付与钱包支付现在并列展示，用户可自行选择支付方式与币种

## v0.4.54 - 2026-04-12

### 调整
- 云服务器下单新增购买数量，支持快捷数量 `1/2/3/4/5` 与自定义输入
- 地址订单创建后文案改为“订单 5 分钟有效”，并明确提示系统已开始自动监控该订单地址到账
- 订单页底部按钮改为 `钱包支付`，点入后再选择 `USDT/TRX` 钱包支付方式

### 数据
- `CloudServerOrder` 新增 `quantity` 字段，用于记录云服务器购买数量

## v0.4.55 - 2026-04-12

### 调整
- 地址订单详情现会显示地区、套餐、数量，并同时展示 `USDT` 与 `TRX` 支付金额
- 云服务器地址订单创建后会同时监控 `USDT/TRX` 到账，任一匹配成功都会直接进入后续流程
- 钱包支付成功提示也补充订单详情，保持地址支付与钱包支付展示一致

## v0.4.56 - 2026-04-12

### 调整
- 定制地区与套餐列表已增加 Redis 缓存，优先走缓存，不再每次都直接打云厂商 API
- 启动时会预热定制缓存，并每 10 分钟定时刷新一次
- 这样可明显降低 `🛠 定制` 首屏和地区切换的 API 延迟

## v0.4.57 - 2026-04-12

### 调整
- 输入/选择数量后，定制流程现在直接进入订单详情页，不再额外停留在支付方式选择页
- 定制缓存已增加简洁日志：命中、回源、定时刷新都会输出摘要
- 保持低噪音风格，只记录地区数、套餐数和地区代码等关键摘要

## v0.4.58 - 2026-04-12

### 修复
- 修复定制缓存日志缺少 `logger` 导致的 `NameError`

### 调整
- 监控缓存日志改为摘要模式：启动时记录一次，周期同步不再每分钟刷屏
- Redis 降级告警增加节流，短时间内重复异常不再持续刷日志
- 保留关键业务日志，继续压低技术噪音日志

## v0.4.59 - 2026-04-12

### 调整
- 为云服务器下单、钱包支付、端口确认、创建开始/成功/失败补充摘要业务日志
- 日志只记录关键业务节点，避免重复技术明细刷屏

## v0.4.60 - 2026-04-12

### 修复
- 修复订单页点击 `钱包支付` 时缺少 `asyncio` 导致的 `NameError`

### 调整
- 下调 `aiogram.event` 日志级别，避免正常 handled update 持续刷屏

## v0.4.61 - 2026-04-12

### 调整
- 完善 Django Admin 后台页：补充排序、搜索、筛选、只读字段、分页和批量动作
- 用户页增加快捷充值动作与订单数量展示
- 充值页增加手动完成并入账动作
- 监控页增加启用/停用批量动作
- 云服务器页增加数量、端口与更多只读运维字段展示

## v0.4.62 - 2026-04-12

### 调整
- 优化 Django Admin 首页标题与中文站点名称
- 调整后台应用分组显示名称，按运营使用习惯排序为：用户、商品与云服务器、充值与资金、地址监控

## v0.4.63 - 2026-04-12

### 调整
- 后台详情页补充字段分组，让用户、配置、商品、订单、充值、监控编辑页更接近完整运营后台
- 配置页增加常用配置说明与初始化常用配置动作
- 用户页增加扣款类批量动作，方便后台余额校正

## v0.4.64 - 2026-04-12

### 调整
- 云服务器订单列表页增加状态徽标和服务状态摘要，更适合后台快速排查与运营查看
- 后台列表页现在可以更直观看到待支付、创建中、已创建、失败、到期等状态

## v0.4.65 - 2026-04-12

### 调整
- 新增后台顶部快捷导航栏，支持一键跳转用户、云服务器订单、充值记录、地址监控、商品、云套餐、系统配置
- 后台首页和详情页现在都有更接近运营后台的常用入口

## v0.4.66 - 2026-04-12

### 调整
- 后台快捷导航从顶部改为左侧导航栏，便于持续切换各管理页
- 进一步贴近运营后台的使用习惯

## v0.4.67 - 2026-04-12

### 调整
- 后台导航改为左侧固定导航栏
- 实测验证后台关键管理路由均可达，未登录状态正常跳转登录页

## v0.4.68 - 2026-04-12

### 修复
- 修复后台用户列表页 `TelegramUser` 预取关联名错误导致的 AttributeError
- 将用户订单统计改为使用 Django 默认反向关联名 `order_set` 和 `cloudserverorder_set`

## v0.4.69 - 2026-04-12

### 调整
- 美化 Django Admin 页面样式：统一背景、卡片、按钮、表单圆角和阴影
- 左侧导航增加当前页高亮
- 后台整体视觉更接近正式运营后台风格

## v0.4.70 - 2026-04-12

### 调整
- 后台视觉进一步调整为接近 EleAdmin 风格：白色顶栏、深色左侧导航、白色卡片内容区
- 优化导航命名为工作台式展示，整体更接近现代后台面板

## v0.4.71 - 2026-04-12

### 调整
- 为后台首页新增工作台卡片区和运营快捷入口
- 首页现在更接近 dashboard 面板，而不是单纯模型列表

## v0.4.72 - 2026-04-12

### 调整
- 后台首页卡片升级为真实数据统计卡片
- 新增后台模板标签 `admin_metric`，用于展示用户、云服务器、充值、监控、订阅订单等实时数量

## v0.4.73 - 2026-04-13

### 调整
- 后台首页新增第二排运营看板：今日收入、今日支出、今日利润、待开通云订单
- 首页真实数据面板进一步接近完整运营后台首页

## 当前通知逻辑
- 转入：`🟢 收入提醒`
- 转出：`🔴 支出提醒`
- 资源增加：`⚡ 资源变动提醒`
- 所有通知都支持详情按钮
