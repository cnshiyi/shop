# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 14:11 CST
- 状态：完成一轮代理列表全标签分页巡检、真实前端点击/跳页测试、数据库口径对账、前端跳页控件补齐和红线扫描。
- 本轮范围：前端代理列表 `/admin/cloud-assets` 的分页能力；后端只更新自动化中文记录，没有改业务代码。

## 修复结论

- 问题：代理列表普通表格分页没有“跳至页”输入，真实页面无法直接跳到深页，不能满足标签压测中的“实际翻页和跳页验证”。
- 修复：在 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src/views/dashboard/cloud-assets/index.vue`：
  - 普通代理表格 `pagination` 增加 `showQuickJumper: true`。
  - 分组分页 `Pagination` 增加 `show-quick-jumper`。
- 修复后真实页面可以在代理列表标签下直接跳到第 `1000` 页。

## 数据库对账

对真实 MySQL 快照表执行只读对账，覆盖：

- `all`
- `unattached_ip`
- `account_disabled`
- `unbound_user`
- `unbound_group`

每个标签验证第 `1` 页、第 `2` 页、第 `1000` 页和末页：

- API `total/page/page_size/total_pages/loaded` 与 `CloudAssetDashboardSnapshot` 查询层一致。
- API 返回 ID 顺序与查询层一致。
- 单页无重复 ID。
- 结果：`0` 个失败。

关键计数：

- 全部可见代理：`2489998`。
- 未附加固定 IP：`100001`。
- 云账号异常：`1145002`。
- 未绑定用户：`100001`。
- 未绑定群组：`100013`。

## 真实前端结果

打开 `http://127.0.0.1:5666/admin/cloud-assets` 后实际操作：

- 首屏代理列表加载成功，表格显示 `20` 行。
- 控制台 `0` error / `0` warning，请求 `0` 个 400/500。
- 页面已出现普通表格跳页输入。

逐个标签点击第 `1` 页并跳到第 `1000` 页，均通过：

- 全部：第 `1000` 页 `20` 行，约 `1.39s`。
- 运行中：第 `1` 页约 `2.94s`，第 `1000` 页约 `1.61s`。
- 即将到期：第 `1` 页约 `0.89s`，第 `1000` 页约 `0.69s`。
- 已过期：第 `1` 页约 `0.88s`，第 `1000` 页约 `0.70s`。
- 未附加固定 IP：第 `1` 页约 `0.59s`，第 `1000` 页约 `0.55s`。
- 异常/待确认：第 `1` 页约 `0.71s`，第 `1000` 页约 `0.62s`。
- 云账号异常：第 `1` 页约 `5.40s`，第 `1000` 页约 `2.44s`。
- 关机计划关闭：第 `1` 页约 `1.07s`，第 `1000` 页约 `0.91s`。
- 未绑定用户：第 `1` 页约 `0.63s`，第 `1000` 页约 `0.75s`。
- 未绑定群组：第 `1` 页约 `0.65s`，第 `1000` 页约 `0.80s`。
- 续费关闭：第 `1` 页约 `1.10s`，第 `1000` 页约 `1.29s`。

所有标签页面校验：

- HTTP `200`。
- API `loaded=20`。
- DOM 表格 `20` 行。
- 页面当前页显示 `1000`。
- 页面总数与接口总数一致。
- 首行/末行 IP 与接口响应一致。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
pnpm -C /Users/a399/Desktop/data/vue-shop-admin -F @vben/web-antd run typecheck
git diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

说明：

- 红线扫描命中项仍是 Telegram 登录账号代码、云账号测试桩和 `CloudServerOrder.ip_recycle_at` 同步语句，不是旧到期事实回流。
- 本轮临时后台 session 和浏览器 storage state 已删除，对应临时用户已清理。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接到仓库文件。

## 观察项

- 代理列表按钮“全部”显示 `2500003`，表格默认折叠已删除/隐藏资产后显示 `2489998`，这是当前 UI 的“总快照数”和“可见列表数”口径差异。后续应统一显示口径或加明确标签，避免误解为丢数据。
- `account_disabled` 标签仍是最慢路径，第 `1` 页真实页面约 `5.40s`，第 `1000` 页约 `2.44s`，下一轮优先继续优化该标签的计数和分页热路径。

## 下一步

- 优先优化代理列表 `account_disabled` 标签，目标把第 `1` 页和深页稳定降到 `2s` 内，同时保持数据库精确对账。
- 继续巡检分组视图深页跳页，确认海量用户/群组分组下无重复、无丢组。
- 继续做机器人全功能真实账号巡检和多任务高并发覆盖。
