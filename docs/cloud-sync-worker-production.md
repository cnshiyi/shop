# 云资产同步 Worker 生产说明

## 进程

至少运行一个独立的 worker 进程：

```bash
uv run python manage.py process_cloud_asset_sync_jobs --poll-interval 2 --batch-size 1 --stale-running-minutes 90
```

worker 会领取状态为 `queued` 的 `CloudAssetSyncJob` 记录，将其切换为 `running`，持续更新心跳，写入详细的 `CloudAssetSyncJobEvent` 记录，并最终让每个任务进入终态。

## systemd 示例

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

## 事件清理

每天运行：

```bash
uv run python manage.py prune_cloud_sync_job_events --days 90 --keep-per-job 500
```

调整生产环境保留策略前，先使用 `--dry-run` 预检。

## 运维检查

常用日志 key：

- `CLOUD_SYNC_JOB_QUEUED`
- `CLOUD_SYNC_JOB_EVENT`
- `CLOUD_SYNC_TASK_START`
- `CLOUD_SYNC_TASK_DONE`
- `CLOUD_SYNC_TASK_FAILED_LOG`
- `CLOUD_SYNC_REQUEST_DONE`
- `CLOUD_SYNC_JOB_CANCEL_REQUESTED`

后台页面检查：

- `/admin/tasks` 显示统一任务中心。
- `/admin/cloud-assets` 同步抽屉显示活跃任务、失败任务、陈旧运行中任务数量和 p95 耗时。
- `/admin/cloud-sync-jobs/:id` 显示单个任务的时间线、payload、日志、worker 心跳以及重试/取消操作。

陈旧任务处理：

- `--stale-running-minutes 90` 会把缺少 worker 心跳或心跳过旧的运行中任务重新排队。
- 心跳仍然新鲜的运行中任务不应手动重置。
- 没有 worker 进程时，排队任务保持显示为 `queued` 是预期行为；应启动 worker，而不是直接改数据库记录。
