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
