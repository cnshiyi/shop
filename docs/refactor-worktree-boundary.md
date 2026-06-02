# 重构工作区边界

## 2026-06-01 当前边界

当前仓库在云同步/任务中心重构提交之外，已经存在许多早先的已跟踪文件修改。除非后续某一轮明确接管这些修改，否则它们会有意保持未暂存状态。

本轮当前由重构接管的文件：

- `cloud/api.py`
- `cloud/api_monitors.py`
- `cloud/task_center.py`
- `shop/dashboard_urls.py`
- `DEVELOPMENT.md`
- `docs/DATA_FLOW_AND_PERSISTENCE.md`
- `docs/project-overview.md`
- `docs/refactor-version-record.md`
- `docs/refactor-worktree-boundary.md`

本轮不接管的既有脏区：

- `app.py`, `manage.py`, `run.py`, `shop/settings.py`
- broad `bot/*` edits
- broad `core/*` edits
- broad `orders/*` edits
- API 拆分范围之外的许多云厂商、生命周期、服务命令相关文件

前端边界：

- 重构接管的前端文件位于 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src` 下。
- `pnpm-lock.yaml` 在本轮开始前已经是脏文件，继续保持在本轮接管范围之外。

提交纪律：

- 只暂存当前轮次列为重构接管的文件。
- 没有明确的清理轮次时，不要回退或规整既有脏文件。
- 后端和前端提交保持分离。
