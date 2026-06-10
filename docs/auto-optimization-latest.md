# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 20:14 CST
- 状态：已按用户明确要求调整计划页列开关、动态列宽和搜索入口，并实测搜索接口。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户明确点名计划页：真实状态、订单号列开关默认关闭。
- 用户要求计划页添加搜索入口并实测搜索。
- 用户追加要求列宽改为动态列宽。

## 修改内容

- `cloud/lifecycle_plan_queries.py`
  - 新增计划页关键词过滤 helper，覆盖 IP、资产名、实例 ID、订单号、用户昵称/用户名、备注、云上状态等常用字段。
  - 关机计划、删机计划、服务器删除历史、IP 删除计划和 IP 删除历史的计数与分页均支持关键词过滤。
  - 有关键词时不复用全量计数缓存，避免搜索结果总数错误。
- `bot/api.py`
  - `/api/admin/tasks/plans/` 接收 `keyword` 参数并传入查询层。
  - 搜索响应返回当前 `keyword`，便于前端和排查核对。
- 前端 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src/views/dashboard/tasks/plans.vue`
  - 顶部新增计划页搜索框和重置入口。
  - 搜索时重置各计划表分页到第一页。
  - “真实状态”“订单号”列开关默认关闭。
  - 计划页表格去掉固定列宽，横向滚动改为 `max-content` 动态宽度。
- 前端 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src/api/admin.ts`
  - 计划页 API 参数类型补充 `keyword`。

## 验证

通过：

```bash
git diff --check
uv run python -m py_compile bot/api.py cloud/lifecycle_plan_queries.py
uv run python manage.py check
pnpm -F @vben/web-antd run typecheck
```

实测：

- 使用本地 8000 后端和 5667 前端代理。
- 计划搜索接口用不存在关键词返回所有计划相关计数为 `0`。
- Playwright 浏览器上下文中调用 `/api/admin/user/info` 成功，调用 `/api/admin/tasks/plans/?compact=1&limit=5&keyword=__no_such_lifecycle_plan_keyword__` 成功，响应 `keyword` 匹配且计划计数为 `0`。
- 未输出后台 token、真实订单号、完整 IP 或代理链接。

## 风险和下一步

- 计划页搜索现在会对多个文本字段做 `icontains` 过滤；大数据量下精确命中订单号/IP 更快，模糊备注搜索仍可能较慢。
- Playwright 新会话无法直接通过路由守卫进入页面，但浏览器环境的 API 搜索请求已实测通过。
