# Refactor Version Record

## 2026-06-01 task-center-and-monitor-split

### Scope

This pass kept splitting the oversized cloud API surface, added a unified task center API, and moved monitor/IP-log endpoints into a dedicated module.

### Runtime Changes

- Added `cloud/api_monitors.py` for cloud IP logs and address monitor APIs.
- Added `cloud/task_center.py` for a unified backend task center overview.
- `cloud/api.py` now re-exports the monitor APIs and task center API for URL compatibility.
- Added `GET /admin/tasks/center/` and kept `GET /admin/tasks/` as the legacy task list.
- Added a refactor worktree boundary document so future passes can distinguish owned edits from existing dirty files.

### Frontend Changes

- Upgraded `/admin/tasks` into a task center page with health cards and a searchable task table.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/api_monitors.py cloud/task_center.py shop/dashboard_urls.py
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase --keepdb --noinput --verbosity 1
```

Frontend validation passed in `/Users/a399/Desktop/data/vue-shop-admin`:

```bash
./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json
```

## 2026-06-01 cloud-sync-runtime-split

### Scope

This refactor split cloud asset sync execution out of `cloud/api.py` and made sync jobs easier to operate, observe, and clean up.

### Runtime Changes

- Added `cloud/sync_jobs.py` as the cloud asset sync job runtime module.
- `cloud/api.py` now keeps cloud asset/order/dashboard API logic and re-exports sync job endpoints for existing dashboard URL aggregation.
- `process_cloud_asset_sync_jobs` imports execution helpers from `cloud.sync_jobs`, no longer from `cloud.api`.
- Bulk sync job subtasks now run serially instead of using a thread pool, so progress updates, event ordering, heartbeat, and cancellation are deterministic.
- Added `cloud_asset_sync_jobs_metrics` API at `cloud-assets/sync-jobs/metrics/`.
- `cloud_assets_sync_status` now embeds the same metrics summary used by the frontend.
- Added `prune_cloud_sync_job_events` for event-table cleanup by age and per-job retention.

### Frontend Changes

- Added `/admin/cloud-sync-jobs/:id` as a dedicated sync job detail page in `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd`.
- Proxy list sync drawer now shows task metrics and links each job row to the detail page.
- Frontend API types now include `DashboardCloudAssetSyncJobsMetrics`.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/sync_jobs.py cloud/management/commands/process_cloud_asset_sync_jobs.py cloud/management/commands/prune_cloud_sync_job_events.py shop/dashboard_urls.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_runs_enabled_accounts_and_merges_results cloud.tests.CloudServerServicesTestCase.test_cloud_asset_sync_jobs_metrics_returns_operational_summary cloud.tests.CloudServerServicesTestCase.test_cancel_queued_cloud_asset_sync_job_marks_terminal_and_events cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_with_selected_assets_uses_asset_scoped_tasks cloud.tests.CloudServerServicesTestCase.test_process_cloud_asset_sync_jobs_worker_processes_queued_job --keepdb --noinput --verbosity 1
```

Frontend validation passed in `/Users/a399/Desktop/data/vue-shop-admin`:

```bash
./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json
```

## 2026-06-01 cloud-asset-lifecycle-refactor

### Scope

This version is an aggressive backend refactor around cloud asset lifecycle, table ownership, and runtime dependency cleanup.

### Database Changes

- `cloud_server` physical table was removed.
- Historical server data was migrated into `cloud_asset`.
- `cloud_asset` is now the only cloud resource fact table.
- `CloudIpLog.server` / `cloud_ip_log.server_id` was removed.
- Django migration chain:
  - `0037_server_table_to_cloud_asset`
  - `0038_drop_server_model_and_iplog_server`

### Runtime Model Direction

- `CloudAsset(kind='server')` is the canonical server asset record.
- `CloudServerOrder` is business context for purchase, renewal, migration, rebuild, deletion, and audit.
- `Server` is no longer a Django model. A small import compatibility facade remains in `cloud.models` so older scripts/tests do not fail immediately on import, but new runtime code should not use it.

### Lifecycle Refactor

- Added `cloud/lifecycle_schedule.py`:
  - central lifecycle time calculation
  - order schedule fields
  - orphan asset delete time
  - unattached static IP release time
  - runtime config helpers
- Added `cloud/lifecycle_execution.py`:
  - scheduled/manual shutdown
  - delete order
  - delete migrated/replaced order
  - delete orphan asset
  - release retained static IP
  - release unattached static IP
  - cloud API timeout handling
- `cloud/lifecycle.py` now scans due work and dispatches to execution helpers.

### Runtime Dependency Cleanup

- `cloud/services.py` now writes primary record updates to `CloudAsset`.
- `cloud/provisioning.py` no longer creates/upserts `Server` rows; provisioning writes `CloudAsset`.
- `cloud/api.py` keeps server endpoint names for compatibility but queries `CloudAsset(kind='server')`.
- `bot/api.py` no longer syncs notes to `Server`.
- `record_cloud_ip_log` records asset/order context only.

### Documentation Updated

- `ARCHITECTURE.md`
- `docs/DATA_FLOW_AND_PERSISTENCE.md`
- `docs/DB_NAMING_CONVENTIONS.md`
- `docs/refactor-mapping.md`
- `docs/table-rename-plan.md`
- `docs/project-overview.md`

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/models.py cloud/services.py cloud/lifecycle.py cloud/provisioning.py cloud/api.py bot/api.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check --verbosity 2
uv run python manage.py migrate --plan
uv run python manage.py migrate cloud 0038
```

Local database probe after migration:

- `cloud_server_exists`: `False`
- `cloud_ip_log.server_id`: removed
- Django registered `cloud.Server` model: `None`

### Known Follow-up

- Some tests and compatibility management commands still reference the `Server` facade.
- `sync_aws_assets.py` and `sync_aliyun_assets.py` still need a deeper pass to rename local variables and remove old wording, although the `Server` facade currently routes writes to `CloudAsset`.
- Full Django tests are still blocked locally by MySQL test database permission:

```sql
GRANT ALL PRIVILEGES ON test_a.* TO 'a'@'localhost';
FLUSH PRIVILEGES;
```

## 2026-06-01 cloud-asset-runtime-cleanup

### Scope

Second refactor pass after the table migration. This pass removes the `Server` compatibility facade from `cloud.models`, moves old command/test compatibility to an explicit command-side wrapper, and adds indexes/state helpers.

### Runtime Changes

- Removed `Server` from `cloud.models` and `__all__`.
- Added `cloud/server_records.py` as an explicit compatibility wrapper over `CloudAsset(kind='server')` for legacy commands and tests.
- Updated sync and maintenance commands to import `Server` from `cloud.server_records`, not from `cloud.models`.
- Added `cloud/lifecycle_state.py` for order-status to asset-status mapping.
- `cloud/api.py` now uses `primary_record_updates_for_order_status` from `cloud.lifecycle_state`.

### Database Changes

- Added `0039_cloud_asset_indexes`:
  - `ca_kind_status_active_idx`
  - `ca_provider_acct_inst_idx`
  - `ca_provider_acct_ip_idx`
  - `ca_order_status_idx`
  - `ca_kind_user_status_idx`

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/lifecycle_state.py cloud/models.py cloud/api.py cloud/server_records.py
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/upsert_cloud_asset.py cloud/management/commands/dedupe_servers.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check --verbosity 2
uv run python manage.py migrate cloud 0039
uv run python manage.py migrate --plan
```

### Remaining Big Refactors

- Physically split `cloud/api.py`.
- Physically split `bot/api.py`.
- Rename legacy server wording inside sync commands and tests from `Server` to `CloudAsset` once test coverage is adjusted.

## 2026-06-01 cloud-dashboard-api-split

### Scope

Third refactor pass focused on shrinking the oversized dashboard cloud API module while preserving existing URL imports.

### Runtime Changes

- Added `cloud/api_servers.py` for server-shaped `CloudAsset(kind='server')` dashboard endpoints:
  - server list payloads
  - server rebuild preserve-link action
  - server delete action
  - server statistics
- Added `cloud/api_plans.py` for cloud plan/pricing dashboard endpoints:
  - provider pricing list
  - custom cloud plan list
  - plan create/update/delete
- `cloud/api.py` now imports these endpoint names at the bottom as compatibility exports, so `shop/dashboard_urls.py` can continue using `cloud_api.<view_name>`.

### Cleanup

- Removed remaining runtime writes to the retired `server` variable inside `update_cloud_asset`.
- Removed removed ORM paths:
  - `order__server__server_name`
  - `order__server__note`
  - `CloudIpLog.select_related('server')`
  - `Q(server__isnull=False)`

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/api_servers.py cloud/api_plans.py
uv run python manage.py check
```

## 2026-06-01 bot-product-api-split

### Scope

Fourth refactor pass started splitting the oversized `bot/api.py` dashboard module.

### Runtime Changes

- Added `bot/api_products.py` for product dashboard endpoints:
  - product list
  - product create
  - product update
- `bot/api.py` keeps compatibility exports for `products_list`, `create_product`, and `update_product`, so existing dashboard URL imports continue to work.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_products.py
uv run python manage.py check
```

## 2026-06-01 bot-admin-api-split

### Scope

Fifth refactor pass continued splitting `bot/api.py` by moving admin account management endpoints.

### Runtime Changes

- Added `bot/api_admin_users.py` for dashboard admin account endpoints:
  - admin user list
  - admin create/update/delete
  - current admin password change
- `bot/api.py` keeps compatibility exports for the moved endpoints, so `shop/dashboard_urls.py` continues resolving the same attributes.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_admin_users.py bot/api_products.py
uv run python manage.py check
```

## 2026-06-01 bot-site-config-api-split

### Scope

Sixth refactor pass moved site configuration and button/text configuration dashboard endpoints out of `bot/api.py`.

### Runtime Changes

- Added `bot/api_site_configs.py` for:
  - site config list/group/update/init
  - text config initialization
  - button config read/update/init
  - daily expiry summary notification test
- Preserved compatibility exports from `bot/api.py` for the moved view names and private payload helpers.
- Removed now-unused config/text/button imports from `bot/api.py`.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_site_configs.py
uv run python manage.py check
```

## 2026-06-01 bot-cloud-account-api-split

### Scope

Seventh refactor pass moved cloud account dashboard management out of `bot/api.py`.

### Runtime Changes

- Added `bot/api_cloud_accounts.py` for:
  - cloud account list/detail
  - create/update/delete
  - AWS and Alibaba Cloud account verification
  - cloud account payloads, duplicate detection, external sync log payloads
- Preserved compatibility exports from `bot/api.py` for moved public views and private helper names.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_cloud_accounts.py bot/api_site_configs.py
uv run python manage.py check
```

## 2026-06-01 bot-auth-api-split

### Scope

Eighth refactor pass moved dashboard authentication and current-user endpoints out of `bot/api.py`.

### Runtime Changes

- Added `bot/api_auth.py` for:
  - login/logout/refresh
  - auth code list
  - TOTP start/bind
  - user info and current user metadata
- Preserved compatibility exports from `bot/api.py` for all moved auth view names.
- Removed unused `authenticate`, `login`, and `logout` imports from `bot/api.py`.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_auth.py bot/api_cloud_accounts.py bot/api_site_configs.py
uv run python manage.py check
```

## 2026-06-01 bot-user-balance-api-split

### Scope

Ninth refactor pass moved Telegram user listing and balance management endpoints out of `bot/api.py`.

### Runtime Changes

- Added `bot/api_users.py` for:
  - user list
  - manual USDT/TRX balance update
  - cloud discount update
  - user balance detail timeline
  - balance ledger payload and manual ledger recording helpers
- Preserved compatibility exports from `bot/api.py` for moved public views and private ledger helper names.
- Removed unused balance/query imports from `bot/api.py`.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_users.py
uv run python manage.py check
```

## 2026-06-01 bot-operation-log-api-split

### Scope

Tenth refactor pass moved bot operation log dashboard endpoints out of `bot/api.py`.

### Runtime Changes

- Added `bot/api_operation_logs.py` for operation log payloads and search/list view.
- Preserved compatibility exports from `bot/api.py`.
- Removed the now-unused `BotOperationLog` import from `bot/api.py`.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_operation_logs.py
uv run python manage.py check
```

## 2026-06-01 bot-telegram-api-split

### Scope

Eleventh refactor pass moved Telegram dashboard login, chat, message, and group-filter endpoints out of `bot/api.py`.

### Runtime Changes

- Added `bot/api_telegram.py` for:
  - Telegram account overview
  - personal account login/code/password/status flows
  - account notification toggles
  - group filter list/detail/create/update
  - chat message send/archive/list
  - Telegram payload and validation helpers
- Preserved compatibility exports from `bot/api.py` for moved public views and private helper names.
- Removed Telegram-specific model/service imports from `bot/api.py`.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_telegram.py
uv run python manage.py check
```

## 2026-06-01 dashboard-api-core-extraction

### Scope

Twelfth refactor pass addressed the cross-domain coupling where cloud and orders dashboard APIs imported private helpers from `bot/api.py`.

### Runtime Changes

- Added `core/dashboard_api.py` as the shared dashboard API utility module.
- Moved generic helpers into core:
  - response helpers: `_ok`, `_error`
  - formatting helpers: `_iso`, `_decimal_to_str`, `_parse_decimal`
  - request/query helpers: `_read_payload`, `_get_keyword`, `_apply_keyword_filter`
  - payload/label helpers: `_split_usernames`, `_user_payload`, `_status_label`, `_days_left`, `_countdown_label`, `_provider_label`, `_provider_status_label`, `_region_label`, `_server_source_label`
  - dashboard session/auth helpers and decorators
- `bot/api.py` now re-exports those helpers for compatibility.
- `cloud/api.py`, `cloud/api_servers.py`, `cloud/api_plans.py`, and `orders/api.py` import shared helpers/decorators from `core.dashboard_api`, removing their `bot.api` helper dependency.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/dashboard_api.py bot/api.py bot/api_auth.py bot/api_users.py bot/api_operation_logs.py bot/api_cloud_accounts.py bot/api_site_configs.py bot/api_admin_users.py bot/api_products.py bot/api_telegram.py cloud/api.py cloud/api_servers.py cloud/api_plans.py orders/api.py
uv run python manage.py check
```

## 2026-06-01 provisioning-structured-result-logging

### Scope

Thirteenth refactor pass removed production `print('[PROVISION_RESULT]', ...)` calls from cloud provisioning.

### Runtime Changes

- Added `_log_provision_result()` in `cloud/provisioning.py`.
- Replaced all provision result `print()` calls with `logger.log(...)`.
- Provision result logs now include structured `extra={'provision_result': ...}` fields.
- MTProxy links are logged as masked previews instead of full raw links in the result payload.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/provisioning.py core/dashboard_api.py bot/api.py cloud/api.py cloud/api_servers.py cloud/api_plans.py orders/api.py
uv run python manage.py check
```

## 2026-06-01 cloud-dashboard-api-helper-extraction

### Scope

Fourteenth refactor pass reduced reverse dependencies where split cloud dashboard API modules treated `cloud/api.py` as a shared helper library.

### Runtime Changes

- Added `cloud/dashboard_snapshots.py` as the single dashboard snapshot refresh coordinator.
- `cloud/services.py` and `cloud/lifecycle.py` now refresh dashboard snapshots through `cloud.dashboard_snapshots` instead of importing `cloud.api`.
- Added `cloud/dashboard_api_helpers.py` for cloud dashboard display helpers:
  - cloud plan config id generation
  - preserve-link status labels
  - dashboard sort direction and expiry ordering
- `cloud/api_servers.py` and `cloud/api_plans.py` no longer import `cloud.api` through `_api_helpers()`.
- Moved rebuild background retry execution from `cloud/api.py` into `cloud/services.py` as `run_cloud_server_rebuild_job()`.
- Kept `cloud/api.py` importing the extracted helper names so existing internal references and compatibility imports continue to work.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/dashboard_api_helpers.py cloud/dashboard_snapshots.py cloud/api.py cloud/api_servers.py cloud/api_plans.py cloud/services.py cloud/lifecycle.py
uv run python manage.py check
```

## 2026-06-01 async-runtime-config-fix

### Scope

Fifteenth refactor pass addressed the P0 issue where `get_runtime_config()` returns env/default values in a running async event loop and can miss updated `SiteConfig` values.

### Runtime Changes

- Replaced async runtime config reads in `bot/runner.py`, `bot/handlers.py`, and `cloud/resource_monitor.py` with `await core.cache.get_config(...)`.
- Removed `asyncio.to_thread(get_runtime_config, ...)` and `sync_to_async(get_runtime_config, ...)` usage from async runtime paths.
- Refactored `core/cache.py:get_config()` so sync DB/default fallback happens inside a dedicated thread helper.
- Verified there are no remaining direct `get_runtime_config()` calls inside `async def` bodies, and no remaining `to_thread/sync_to_async(get_runtime_config)` adapters.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/cache.py core/runtime_config.py bot/runner.py bot/handlers.py cloud/resource_monitor.py cloud/dashboard_api_helpers.py cloud/dashboard_snapshots.py cloud/api.py cloud/api_servers.py cloud/api_plans.py cloud/services.py cloud/lifecycle.py
uv run python manage.py check
```

## 2026-06-01 dashboard-bearer-write-auth

### Scope

Sixteenth refactor pass addressed the CSRF/auth boundary risk where csrf-exempt dashboard write APIs could still authenticate through cookie session state.

### Runtime Changes

- `core/dashboard_api.py` now treats unsafe dashboard methods (`POST`, `PUT`, `PATCH`, `DELETE`, etc.) as bearer-only.
- Dashboard write requests must provide `Authorization: Bearer session-...`; cookie-authenticated `request.user` alone is no longer accepted for write views.
- Safe read methods still support existing cookie/session authentication for compatibility.
- Updated dashboard auth tests so write tests attach explicit bearer session headers.
- Added a regression test proving cookie-only dashboard writes are rejected with 401.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/dashboard_api.py bot/tests.py bot/api_auth.py bot/api.py bot/api_admin_users.py
uv run python manage.py check
```

Blocked locally:

```bash
uv run python manage.py test bot.tests.DashboardSessionExpiryTestCase bot.tests.DashboardAuthSurfaceTestCase --keepdb
```

The focused test run is blocked by local MySQL test database permissions:

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```

## 2026-06-01 cloud-sync-structured-state

### Scope

Twentieth refactor pass replaced the cloud missing-delete confirmation marker text with structured cloud asset sync state.

### Runtime Changes

- Added `CloudAsset.sync_state` JSON field and migration `0040_cloudasset_sync_state`.
- `cloud/sync_safety.py` now treats `sync_state['missing_confirmation']` as the source of truth.
- Removed parsing and writing of legacy `[missing_sync_count:...]` / `[msc_at:...]` provider-status markers.
- AWS and Alibaba Cloud missing-resource sync now:
  - increments structured confirmation count on each missing pass
  - keeps the asset/server running while count is below threshold
  - deletes only after the structured count reaches the configured threshold
  - clears missing confirmation state when a later sync sees the resource live again
- Dashboard lifecycle/delete-plan views now read confirmation progress from item/asset `sync_state`.
- Updated affected tests to assert structured `sync_state` instead of provider-status marker text.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/sync_safety.py cloud/models.py cloud/migrations/0040_cloudasset_sync_state.py cloud/server_records.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py bot/api.py cloud/tests.py
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check
```

Blocked locally:

```bash
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_note_preserves_existing_note cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_requires_interval cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_expose_missing_confirmation_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_unattached_ip_show_confirmation_progress_in_state_and_note cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_missing_instance_requires_five_passes_before_delete --keepdb
```

The focused DB test run is blocked by local MySQL test database permissions:

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```

## 2026-06-02 cloud-dashboard-api-domain-split

### Scope

Twenty-second refactor pass split the remaining cloud dashboard API monolith into asset, order, and task modules while preserving `cloud.api` as the URL compatibility facade.

### Runtime Changes

- Added `cloud/api_assets.py` for proxy/asset list payloads, asset risk summaries, asset editing, auto-renew toggles, and dashboard snapshot refreshes.
- Added `cloud/api_orders.py` for cloud order list/detail payloads, order status updates, order detail saves, and protected order deletion.
- Added `cloud/api_tasks.py` for legacy task overview, notice plan detail/refresh, notice switch/text APIs, auto-renew detail, and manual auto-renew execution.
- Reduced `cloud/api.py` from 4249 lines to 460 lines; it now keeps compatibility imports plus single-asset status sync, server sync, cloud plan sync, and delete-asset handling.
- Kept legacy `cloud.api.*` patch/import points for existing tests and operators by routing patched symbols back into the new modules.
- Added structured logs for cloud order status application, order detail updates, cloud asset deletion, and server sync start/finish.
- Fixed a latent `sync_servers()` `cancelled` local variable error by explicitly initializing the flag.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/api_assets.py cloud/api_orders.py cloud/api_tasks.py
uv run python manage.py check
git diff --check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_orders_list_exposes_auto_renew_enabled cloud.tests.CloudServerServicesTestCase.test_sync_servers_missing_state_does_not_bypass_provider_confirmation cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_uses_bulk_order_inference_without_per_asset_fallback cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_status_uses_asset_scope cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_uses_cloud_notice_plan_table cloud.tests.CloudServerServicesTestCase.test_auto_renew_task_detail_includes_due_retry_and_fallback_items cloud.tests_task_center.CloudTaskCenterApiTestCase --keepdb --noinput --verbosity 1
```

Notes:

- The older direct `RequestFactory` POST tests that do not attach dashboard Bearer credentials still return 401 under the current dashboard write-auth policy; they were not used as pass/fail gates for this split.

## 2026-06-01 cloud-sync-worker-and-status-tracking

### Scope

Latest refactor pass made dashboard-triggered proxy synchronization durable and explicitly observable.

### Runtime Changes

- `/admin/cloud-assets/sync/` now only creates a `CloudAssetSyncJob` queue record and returns immediately.
- Added `process_cloud_asset_sync_jobs` as the persistent DB-backed worker for queued sync jobs.
- `run.py worker` starts the sync worker, and `run.py all` now starts web, bot, and the sync worker together.
- Added sync job list and retry APIs:
  - `/admin/cloud-assets/sync-jobs/`
  - `/admin/cloud-assets/sync-jobs/<id>/retry/`
  - `/admin/cloud-assets/sync-jobs/<id>/cancel/`
- Sync job status is now the durable status surface:
  - `queued`
  - `running`
  - `succeeded`
  - `partial`
  - `failed`
  - `cancelled`
- Added `cloud_asset_sync_job_event` for detailed sync event timelines:
  - queued / claimed / status / task / progress / log / warning / error / cancel / retry / heartbeat
  - the event table stores `job_id` as an indexed scalar instead of a foreign key so detailed logging cannot lock or block the main job status row
- Worker and sync execution update `worker_id`, `worker_heartbeat_at`, `progress_current`, `progress_total`, `current_task`, `errors`, `warnings`, `logs`, `started_at`, `finished_at`, and cancel request fields throughout execution.
- Dashboard snapshot refreshes are now scoped:
  - full cloud sync refreshes the complete `cloud_asset_dashboard_snapshot`
  - selected asset sync and single-asset updates refresh only the affected asset IDs
- The admin frontend has a sync job drawer for status, progress, worker heartbeat, results, detailed events, logs, cancel, retry, status filters, and failed-only filtering; polling updates the visible job row without blocking the whole proxy list after enqueue.
- `lefthook.yml` no longer hardcodes `/opt/homebrew/bin/pnpm`, so Git hooks can use the current shell `pnpm`.

### Verification

Passed locally:

```bash
uv run python -m py_compile run.py cloud/api.py cloud/dashboard_snapshots.py cloud/models.py cloud/tests.py cloud/management/commands/process_cloud_asset_sync_jobs.py shop/dashboard_urls.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check
uv run python manage.py sqlmigrate cloud 0042
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_runs_enabled_accounts_and_merges_results cloud.tests.CloudServerServicesTestCase.test_cancel_queued_cloud_asset_sync_job_marks_terminal_and_events cloud.tests.CloudServerServicesTestCase.test_process_cloud_asset_sync_jobs_worker_processes_queued_job --keepdb --noinput --verbosity 1
(cd /Users/a399/Desktop/data/vue-shop-admin && ./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json)
git diff --check
```

## 2026-06-01 cloud-asset-list-and-sync-performance

### Scope

Twenty-second refactor pass optimized the slow proxy asset list and dashboard-triggered cloud sync path.

### Runtime Changes

- Added `cloud_asset_dashboard_snapshot` as a materialized dashboard list table for cloud assets.
- `cloud_assets_list()` and `cloud_assets_risk_summary()` now read snapshot rows for search, risk filters, grouping, counts, and database pagination instead of rebuilding every row on each request.
- Added `refresh_cloud_asset_dashboard_snapshots` management command and wired dashboard snapshot refreshes after sync/service changes.
- Added `cloud_asset_sync_job` to queue dashboard sync requests, track progress/result/log tails, and expose `/admin/cloud-assets/sync-jobs/<id>/`.
- `/admin/cloud-assets/sync/` now returns immediately with a queued job; the background thread executes account/asset scoped sync tasks and records the final result.
- AWS and Alibaba Cloud sync commands no longer maintain the retired `Server` compatibility mirror; `cloud_asset` remains the single cloud resource truth.
- The admin frontend now uses true server-side pagination in non-grouped proxy list mode and polls cloud sync jobs until terminal status.
- The "show deleted" toggle is sent to the backend so pagination totals match the visible list.
- Database naming and data-flow docs now list the new snapshot/job tables.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/dashboard_snapshots.py cloud/models.py cloud/tests.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/refresh_cloud_asset_dashboard_snapshots.py shop/dashboard_urls.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check
cd /Users/a399/Desktop/data/vue-shop-admin && ./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json
```

Blocked locally:

```bash
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_asset_dashboard_snapshot_refresh_materializes_paginated_list ... --keepdb --noinput
```

The focused DB test run is still blocked by local MySQL test database permissions:

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```

## 2026-06-01 proxy-list-and-sync-performance

### Scope

Twenty-seventh refactor pass optimized dashboard proxy list loading and selected-asset cloud sync.

### Runtime Changes

- Added `core.cloud_accounts.list_cloud_account_labels()` so dashboard payload rendering can load active cloud account labels once per request instead of once per asset.
- Added a `CloudAssetPayloadContext` for proxy list payloads:
  - bulk infers missing `CloudServerOrder` links by IP/name/resource identifiers;
  - disables per-row order fallback queries in list/risk-summary reads;
  - avoids `sync_cloud_asset_user_binding()` writes during list rendering;
  - computes missing unattached-IP expiry for display without saving during a GET.
- `cloud_assets_list` and `cloud_assets_risk_summary` now build payloads through the shared context.
- `sync_cloud_assets` now treats selected `asset_ids` as real asset-scoped sync tasks instead of widening to full account sync. Multi-select creates scoped tasks with `asset_id`, `instance_id`, `public_ip`, account, and region.
- Sync task locks include the scoped asset/resource key, so two selected assets in the same account/region do not skip each other.
- Removed the runtime reconcile command call from dashboard sync because `CloudAsset(kind='server')` is now canonical.
- Dashboard sync snapshot refresh now uses the deferred refresh path.
- AWS/Aliyun sync command visible-count summaries use a cheap active asset count instead of full dashboard dedupe scans.
- Frontend proxy list load now uses `risk_counts` returned by the list endpoint and avoids the duplicate concurrent risk-summary request.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/cloud_accounts.py cloud/api.py cloud/api_servers.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
uv run python manage.py check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_uses_bulk_order_inference_without_per_asset_fallback cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_does_not_persist_unattached_ip_expiry cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_with_selected_assets_uses_asset_scoped_tasks --keepdb --noinput --verbosity 1
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_runs_enabled_accounts_and_merges_results cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_status_uses_asset_scope cloud.tests.CloudServerServicesTestCase.test_sync_retained_ip_asset_uses_order_account_and_static_ip_scope --keepdb --noinput --verbosity 1
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_dedupes_same_cloud_account_label_variants cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_keeps_same_user_on_same_page cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_keeps_same_telegram_group_on_same_page cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_paginated_uses_twenty_user_groups_per_page --keepdb --noinput --verbosity 1
(cd /Users/a399/Desktop/data/vue-shop-admin && ./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json)
```

Frontend note: `pnpm -F @vben/web-antd typecheck` is blocked by local engine mismatch (`pnpm 9.15.9`, Node `v26.0.0`), so the same `vue-tsc` command was run directly.

## 2026-06-01 aws-lightsail-structured-create-log

### Scope

Twenty-second refactor pass removed the remaining runtime `print` from AWS Lightsail provisioning and routed the result through structured logging.

### Runtime Changes

- Replaced the `print('[AWS_CREATE_RESULT]', ...)` stdout dump in `cloud/aws_lightsail.py` with a structured `logger.info(...)` event.
- The creation log now carries `order_no`, `server_name`, `region`, `bundle_id`, `blueprint_id`, `public_ip`, and `static_ip_name` as log fields.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/aws_lightsail.py
uv run python manage.py check
```

## 2026-06-01 cache-redis-fallback-observability

### Scope

Twenty-fourth refactor pass made Redis daily-stat fallback paths observable without changing the local fallback behavior.

### Runtime Changes

- `core/cache.py` now logs debug entries when Redis daily-stat increment, read, or close operations fail.
- The in-process fallback counters still run exactly as before when Redis is unavailable.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/cache.py
uv run python manage.py check
git diff --check
```

## 2026-06-01 aws-missing-confirmation-duplicate-guard

### Scope

Twenty-fifth refactor pass fixed duplicate confirmation increments when the AWS missing-resource sync sees both a canonical `CloudAsset` row and a legacy `Server` compatibility row for the same cloud resource.

### Runtime Changes

- AWS missing confirmation now copies structured `sync_state` from the primary row to the related compatibility row instead of incrementing both independently.
- `_mark_deleted_when_missing_in_aws()` tracks rows already handled in the current sync pass and skips duplicate compatibility rows.
- Local focused tests can run in this aggressive refactor branch with `DJANGO_TEST_REUSE_DB=1`, which reuses the current MySQL database instead of trying to create `test_a`.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_note_preserves_existing_note cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_requires_interval cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_expose_missing_confirmation_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_unattached_ip_show_confirmation_progress_in_state_and_note cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_missing_instance_requires_five_passes_before_delete --keepdb --noinput --verbosity 1
```

## 2026-06-01 server-compat-runtime-shrink

### Scope

Twenty-sixth refactor pass removed more runtime dependency on the `cloud.server_records.Server` compatibility wrapper.

### Runtime Changes

- `core.cloud_accounts.list_cloud_accounts_by_server_load()` now counts `CloudAsset(kind='server')` directly.
- `upsert_cloud_asset` no longer writes a duplicate compatibility `Server` row after creating/updating the canonical asset.
- `dedupe_servers` now de-duplicates canonical server assets in `cloud_asset`.
- `reconcile_cloud_assets_from_servers` is now an explicit no-op compatibility command because `cloud_server` has already been removed.
- Remaining runtime compatibility wrapper imports are limited to AWS/Aliyun sync commands; historical migrations and tests still reference old labels intentionally.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/cloud_accounts.py cloud/management/commands/upsert_cloud_asset.py cloud/management/commands/dedupe_servers.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
uv run python manage.py check
uv run python manage.py reconcile_cloud_assets_from_servers
uv run python manage.py dedupe_servers
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test core.tests.CloudAccountSelectionTestCase --keepdb --noinput --verbosity 1
```

## 2026-06-01 dashboard-api-helper-extraction

### Scope

Twenty-third refactor pass removed the dashboard API submodules' reverse dependency on the `bot.api` aggregation module.

### Runtime Changes

- Added `core/dashboard_totp.py` for dashboard TOTP secret normalization, generation, otpauth URL building, and token verification.
- Added `bot/user_stats.py` for active cloud asset and per-user proxy count queries.
- Moved generic dashboard payload helpers (`_json_payload`, `_payload_bool`, `_parse_runtime_time_point`) into `core/dashboard_api.py`.
- `bot/api_auth.py`, `bot/api_admin_users.py`, `bot/api_cloud_accounts.py`, `bot/api_operation_logs.py`, `bot/api_products.py`, `bot/api_site_configs.py`, `bot/api_telegram.py`, and `bot/api_users.py` no longer import from `bot.api`.
- `bot/api.py` now consumes the extracted helpers and remains a route/export aggregation point.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/dashboard_api.py core/dashboard_totp.py bot/user_stats.py bot/api.py bot/api_auth.py bot/api_admin_users.py bot/api_cloud_accounts.py bot/api_operation_logs.py bot/api_products.py bot/api_site_configs.py bot/api_telegram.py bot/api_users.py
uv run python manage.py check
git diff --check
```

## 2026-06-01 cloud-asset-query-helper

### Scope

Twenty-first refactor pass moved shared cloud asset list visibility and de-duplication logic out of dashboard API modules.

### Runtime Changes

- Added `cloud/asset_queries.py` for canonical `CloudAsset` visible-list and de-duplication helpers.
- `cloud/api.py` now consumes the shared asset query helpers instead of owning them.
- AWS sync, Alibaba Cloud sync, and asset reconciliation commands no longer import `cloud.api` just to count visible assets.
- AWS and Alibaba Cloud sync commands now import `_provider_status_label` from `core.dashboard_api` instead of `bot.api`.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/asset_queries.py cloud/api.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
uv run python manage.py check
git diff --check
```

## 2026-06-01 db-naming-convention-alignment

### Scope

Seventeenth refactor pass corrected database naming documentation so it matches the actual runtime schema.

### Runtime Changes

- Updated `docs/DB_NAMING_CONVENTIONS.md` from the previous idealized plural-table convention to the real project convention:
  - `core_*`
  - `bot_*`
  - `order_*`
  - `cloud_*`
- Documented the current `db_table` inventory for `core`, `bot`, `orders`, and `cloud`.
- Clarified that new runtime tables should use `域前缀_单数语义名`.
- Explicitly marked plural alternatives such as `cloud_assets`, `cloud_server_orders`, and `balance_ledgers` as non-default unless part of a planned migration.
- Reconfirmed `cloud_asset` as the cloud resource source-of-truth table.

### Verification

Documentation-only change. Source table list was checked with:

```bash
rg -n "db_table\\s*=|class Meta:" core bot orders cloud -g'*.py'
```

## 2026-06-01 encrypted-config-invalid-token-handling

### Scope

Eighteenth refactor pass tightened encrypted configuration handling so broken Fernet-looking ciphertext is not silently treated as plaintext.

### Runtime Changes

- `core/crypto.py:decrypt_text()` still returns legacy plaintext values unchanged when they do not look encrypted.
- Values starting with the Fernet token prefix `gAAAA` now log `CONFIG_DECRYPT_INVALID_TOKEN` and return an empty string when decryption fails.
- Added focused tests for:
  - legacy plaintext fallback
  - invalid Fernet-like token handling after an encryption key mismatch
- Fixed `core/tests.py` to import the `Server` compatibility model from `cloud.server_records`, matching the current cloud asset architecture.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/crypto.py core/tests.py core/models.py bot/models.py bot/api_site_configs.py
uv run python manage.py test core.tests.CryptoDecryptTestCase --keepdb
uv run python manage.py check
```

## 2026-06-01 site-config-cache-invalidation

### Scope

Nineteenth refactor pass reduced configuration cache split-brain between `SiteConfig` local cache and `core.cache` async config cache.

### Runtime Changes

- Added explicit `core.cache` helpers:
  - `get_cached_config_value()`
  - `cache_config_value()`
  - `invalidate_config_cache()`
- `SiteConfig.clear_cache()` now invalidates the async config cache as well as the model-local 30-second cache.
- Replaced direct `_cached_config` writes/reads in bot text/config paths with helper functions.
- Added a focused regression test for `SiteConfig.set()` invalidating the async config cache.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/cache.py core/models.py core/texts.py core/tests.py bot/api_site_configs.py bot/handlers.py
uv run python manage.py check
```

Blocked locally:

```bash
uv run python manage.py test core.tests.SiteConfigCacheTestCase --keepdb
```

The focused DB test run is blocked by local MySQL test database permissions:

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```
