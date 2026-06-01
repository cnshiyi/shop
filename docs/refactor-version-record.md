# Refactor Version Record

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
