# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 12:50 CST
- 状态：已完成代理列表 10 万资产三视图、自动续费开关和列开关压测，并修复列全关渲染错误。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户要求代理列表继续压测 10 万级数据，三种视图都要实际打开前端查看。
- 本轮追加要求压测列开关，必须覆盖每个视图的全关、全开恢复。
- 压测必须使用独立测试库，不能污染默认本地业务库。

## 修复内容

- `apps/web-antd/src/views/dashboard/cloud-assets/index.vue`
  - 平铺表格和分组表格在所有列关闭时不再挂载空列 `Table`。
  - 全部列关闭后显示“已关闭全部显示列”的空状态。
  - 保留已有列开关、分页和数据查询逻辑，不改变后端分页口径。

## 验证

通过：

```bash
pnpm typecheck
uv run python manage.py check
```

真实浏览器压测使用独立 SQLite 库 `.stress/cloud_assets_100k.sqlite3`，数据量：

- `CloudAsset`：100000
- `CloudAssetDashboardSnapshot`：100000
- `TelegramUser`：1000
- `TelegramGroupFilter`：200
- `CloudServerOrder`：1000

压测结果：

- IP 视图：8 个列开关，全关显示空状态，全开表头完整恢复。
- 操作视图：23 个列开关，全关显示空状态，全开表头完整恢复。
- 云资源视图：11 个列开关，全关显示空状态，全开表头完整恢复。
- 列开关压测结果文件：`output/playwright/cloud-assets-column-switches-fixed-result.json`
- 截图：
  - `output/playwright/cloud-assets-columns-ip-all-off-fixed.png`
  - `output/playwright/cloud-assets-columns-ip-all-on-fixed.png`
  - `output/playwright/cloud-assets-columns-ops-all-off-fixed.png`
  - `output/playwright/cloud-assets-columns-ops-all-on-fixed.png`
  - `output/playwright/cloud-assets-columns-cloud-all-off-fixed.png`
  - `output/playwright/cloud-assets-columns-cloud-all-on-fixed.png`

## 结论

- 代理列表列开关在 10 万资产压测库下可以完整关闭和恢复。
- 全部列关闭不再触发表格空列渲染异常。
- 三种视图的表头恢复结果与关闭前一致，未发现列丢失或列错位。
