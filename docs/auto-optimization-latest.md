# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 22:52 CST
- 状态：完成已删除资产详情敏感字段收敛，并补齐计划页“服务器删除历史记录”独立分页表。
- 本轮范围：已删除资产详情、计划页关机/删机/IP 删除/IP 历史/服务器删除历史、真实浏览器页面验证、数据库数量对账。

## 本轮修复

- 后端仓库 `/Users/a399/Desktop/data/shop`：
  - `cloud/api_assets.py`：删除态 / 终止态资产详情不再返回完整历史公网 IP、`mtproxy_link`、`proxy_links`、备注中的 `tg://proxy`、`socks5://` 或 `secret=`。
  - `cloud/api_asset_edit.py`：资产详情扩展字段加载后再次执行删除态脱敏，覆盖关联订单、历史订单和 IP 日志。
  - `bot/api.py`：生命周期计划页新增 `server_history_items`、`server_history_count` 和 `pagination.server_history`，服务器删除历史作为独立分页表返回。
  - `cloud/tests.py`：新增已删除资产详情脱敏回归测试、服务器删除历史独立分页回归测试。
- 前端仓库 `/Users/a399/Desktop/data/vue-shop-admin`：
  - `apps/web-antd/src/api/admin.ts`：补齐服务器删除历史响应类型和请求分页参数。
  - `apps/web-antd/src/views/dashboard/tasks/plans.vue`：新增“服务器删除历史记录”表格，支持列开关、分页、查看详情。

## 页面实测

- 实际打开 `/admin/cloud-assets/1500332`：
  - 页面标题为“代理详情”。
  - 状态显示已删除。
  - 页面正文不包含 `tg://proxy`、`socks5://`、`secret=`。
  - 页面无加载失败 / 请求失败 / 异常文案。
  - 控制台 error 为 0，warning 为 0。
- 实际打开 `/admin/tasks/plans`：
  - 页面标题为“计划”。
  - 页面包含关机计划、删除计划、服务器删除历史记录、IP 删除计划、IP 删除历史记录。
  - 服务器删除历史记录显示“已加载 50 / 总 20009”。
  - 数据库 `CloudServerOrder(status='deleted')` 数量为 20009，与页面总数一致。
  - 页面无加载失败 / 请求失败 / 异常文案。
  - 控制台 error 为 0，warning 为 0。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_returns_server_delete_history_table cloud.tests.CloudServerServicesTestCase.test_deleted_cloud_asset_detail_masks_proxy_links_and_history_notes cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_ip_delete_history_beyond_loaded_limit --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/api_assets.py cloud/api_asset_edit.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
/Users/a399/.homebrew/bin/pnpm --filter @vben/web-antd typecheck
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

SQLite `db_comment` 警告为已知数据库能力差异，不影响本轮结果。

## 生命周期真机覆盖

- `docs/real-machine-test-report.md` 已记录此前用户授权下的真实创建服务器、关机、删除服务器、固定 IP 释放、机器人点击和支付流程测试，资源 ID、公网 IP、代理链接和密钥均已脱敏。
- 本轮未新增真实云资源创建、关机、删机或释放 IP；本轮重点是修复历史展示面和计划页缺表问题。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、真实支付、链上广播、生产发布、删除业务数据或删除测试库。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险与下一轮

- 继续执行不少于 4 小时的自动巡检目标。
- 计划页服务器删除历史当前按已删除云订单分页；后续应继续把无订单孤儿服务器删除历史并入统一查询层，避免口径再次分叉。
- 继续关注 150 万资产下计划页首屏和代理列表首屏冷加载性能，同时保持翻页与数据库精确对账一致。
