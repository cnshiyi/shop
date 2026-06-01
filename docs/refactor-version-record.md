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
