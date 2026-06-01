# Refactor Worktree Boundary

## 2026-06-01 current boundary

This repository currently has many pre-existing tracked modifications outside the cloud sync/task-center refactor commits. They are intentionally left unstaged unless a later pass explicitly owns them.

Current active refactor-owned files for this pass:

- `cloud/api.py`
- `cloud/api_monitors.py`
- `cloud/task_center.py`
- `shop/dashboard_urls.py`
- `DEVELOPMENT.md`
- `docs/DATA_FLOW_AND_PERSISTENCE.md`
- `docs/project-overview.md`
- `docs/refactor-version-record.md`
- `docs/refactor-worktree-boundary.md`

Pre-existing dirty areas not owned by this pass:

- `app.py`, `manage.py`, `run.py`, `shop/settings.py`
- broad `bot/*` edits
- broad `core/*` edits
- broad `orders/*` edits
- many cloud provider/lifecycle/service command files outside the API split

Frontend boundary:

- Refactor-owned frontend files are under `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src`.
- `pnpm-lock.yaml` is dirty before this pass and remains outside owned changes.

Commit discipline:

- Stage only files listed as refactor-owned for the current pass.
- Do not revert or normalize the pre-existing dirty files without an explicit cleanup pass.
- Keep backend and frontend commits separate.
