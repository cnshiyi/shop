# Cloud Sync Worker Production Notes

## Process

Run at least one dedicated worker process:

```bash
uv run python manage.py process_cloud_asset_sync_jobs --poll-interval 2 --batch-size 1 --stale-running-minutes 90
```

The worker claims `queued` `CloudAssetSyncJob` rows, moves them to `running`, updates heartbeat, writes detailed `CloudAssetSyncJobEvent` rows, and exits each job in a terminal state.

## systemd example

```ini
[Unit]
Description=Shop cloud asset sync worker
After=network.target mysql.service redis.service

[Service]
WorkingDirectory=/opt/shop
Environment=DJANGO_SETTINGS_MODULE=shop.settings
ExecStart=/usr/bin/env uv run python manage.py process_cloud_asset_sync_jobs --poll-interval 2 --batch-size 1 --stale-running-minutes 90
Restart=always
RestartSec=5
User=shop
Group=shop

[Install]
WantedBy=multi-user.target
```

## Event cleanup

Run daily:

```bash
uv run python manage.py prune_cloud_sync_job_events --days 90 --keep-per-job 500
```

Use `--dry-run` before changing production retention.

## Operational checks

Useful log keys:

- `CLOUD_SYNC_JOB_QUEUED`
- `CLOUD_SYNC_JOB_EVENT`
- `CLOUD_SYNC_TASK_START`
- `CLOUD_SYNC_TASK_DONE`
- `CLOUD_SYNC_TASK_FAILED_LOG`
- `CLOUD_SYNC_REQUEST_DONE`
- `CLOUD_SYNC_JOB_CANCEL_REQUESTED`

Dashboard checks:

- `/admin/tasks` shows the unified task center.
- `/admin/cloud-assets` sync drawer shows active jobs, failed jobs, stale running count, and p95 duration.
- `/admin/cloud-sync-jobs/:id` shows one job timeline, payloads, logs, worker heartbeat, and retry/cancel actions.

Stale job handling:

- `--stale-running-minutes 90` requeues running jobs whose worker heartbeat is missing or too old.
- A running task with a fresh heartbeat should not be manually reset.
- A queued task with no worker process is expected to remain visible as `queued`; start the worker rather than editing rows.
