"""Dashboard snapshot refresh coordination for cloud runtime changes."""

import logging
import hashlib
import sys
import threading

from django.core.cache import cache
from django.db import close_old_connections
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

logger = logging.getLogger(__name__)


def _is_db_table_not_ready_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in ['no such table', 'does not exist', 'undefined table'])


def _is_interpreter_shutdown_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        sys.is_finalizing()
        or 'interpreter shutdown' in message
        or 'cannot schedule new futures' in message
        or 'can\'t start new thread' in message
        or 'cannot start new thread' in message
        or 'can\'t create new thread' in message
        or 'cannot create new thread' in message
    )


def _normalize_asset_ids(asset_ids):
    if not asset_ids:
        return None
    normalized = []
    for value in asset_ids:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in normalized:
            normalized.append(parsed)
    return normalized or None


def _refresh_dashboard_plan_snapshots(reason: str = '', *, lifecycle_limit: int = 1000, cloud_asset_ids=None, full_cloud_assets: bool | None = None):
    normalized_asset_ids = _normalize_asset_ids(cloud_asset_ids)
    if full_cloud_assets is None:
        full_cloud_assets = normalized_asset_ids is None
    try:
        from cloud.api_asset_snapshots import refresh_cloud_asset_dashboard_snapshots
        refresh_cloud_asset_dashboard_snapshots(
            asset_ids=normalized_asset_ids,
            reason=reason or 'dashboard_snapshot_refresh',
            full=full_cloud_assets,
        )
    except (OperationalError, ProgrammingError) as exc:
        if _is_db_table_not_ready_error(exc):
            logger.debug('DASHBOARD_SNAPSHOT_CLOUD_ASSET_REFRESH_SKIPPED reason=%s error=%s', reason, exc)
        else:
            logger.exception('DASHBOARD_SNAPSHOT_CLOUD_ASSET_REFRESH_FAILED reason=%s', reason)
    except RuntimeError as exc:
        if _is_interpreter_shutdown_error(exc):
            logger.info('DASHBOARD_SNAPSHOT_CLOUD_ASSET_REFRESH_SKIPPED reason=%s error=%s', reason, exc)
        else:
            logger.exception('DASHBOARD_SNAPSHOT_CLOUD_ASSET_REFRESH_FAILED reason=%s', reason)
    except Exception:
        logger.exception('DASHBOARD_SNAPSHOT_CLOUD_ASSET_REFRESH_FAILED reason=%s', reason)
    try:
        from cloud.api_tasks import _build_auto_renew_plan_items
        _build_auto_renew_plan_items(now=timezone.now())
    except (OperationalError, ProgrammingError) as exc:
        if _is_db_table_not_ready_error(exc):
            logger.debug('DASHBOARD_SNAPSHOT_AUTO_RENEW_REFRESH_SKIPPED reason=%s error=%s', reason, exc)
        else:
            logger.exception('DASHBOARD_SNAPSHOT_AUTO_RENEW_REFRESH_FAILED reason=%s', reason)
    except RuntimeError as exc:
        if _is_interpreter_shutdown_error(exc):
            logger.info('DASHBOARD_SNAPSHOT_AUTO_RENEW_REFRESH_SKIPPED reason=%s error=%s', reason, exc)
        else:
            logger.exception('DASHBOARD_SNAPSHOT_AUTO_RENEW_REFRESH_FAILED reason=%s', reason)
    except Exception:
        logger.exception('DASHBOARD_SNAPSHOT_AUTO_RENEW_REFRESH_FAILED reason=%s', reason)
    try:
        from cloud.api_tasks import _build_notice_plan_summary
        _build_notice_plan_summary(
            limit=500,
            offset=0,
            history_limit=1000,
            history_offset=0,
            fields={'basic'},
            include_total_counts=True,
        )
    except (OperationalError, ProgrammingError) as exc:
        if _is_db_table_not_ready_error(exc):
            logger.debug('DASHBOARD_SNAPSHOT_NOTICE_REFRESH_SKIPPED reason=%s error=%s', reason, exc)
        else:
            logger.exception('DASHBOARD_SNAPSHOT_NOTICE_REFRESH_FAILED reason=%s', reason)
    except RuntimeError as exc:
        if _is_interpreter_shutdown_error(exc):
            logger.info('DASHBOARD_SNAPSHOT_NOTICE_REFRESH_SKIPPED reason=%s error=%s', reason, exc)
        else:
            logger.exception('DASHBOARD_SNAPSHOT_NOTICE_REFRESH_FAILED reason=%s', reason)
    except Exception:
        logger.exception('DASHBOARD_SNAPSHOT_NOTICE_REFRESH_FAILED reason=%s', reason)
    _refresh_lifecycle_plan_view(reason, lifecycle_limit=lifecycle_limit)


def _refresh_lifecycle_plan_view(reason: str = '', *, lifecycle_limit: int = 1000):
    try:
        from bot import api as bot_api
        bot_api._sync_lifecycle_plan_table(limit=None, page_size=lifecycle_limit)
    except (OperationalError, ProgrammingError) as exc:
        if _is_db_table_not_ready_error(exc):
            logger.debug('DASHBOARD_SNAPSHOT_LIFECYCLE_REFRESH_SKIPPED reason=%s error=%s', reason, exc)
        else:
            logger.exception('DASHBOARD_SNAPSHOT_LIFECYCLE_REFRESH_FAILED reason=%s', reason)
    except RuntimeError as exc:
        if _is_interpreter_shutdown_error(exc):
            logger.info('DASHBOARD_SNAPSHOT_LIFECYCLE_REFRESH_SKIPPED reason=%s error=%s', reason, exc)
        else:
            logger.exception('DASHBOARD_SNAPSHOT_LIFECYCLE_REFRESH_FAILED reason=%s', reason)
    except Exception:
        logger.exception('DASHBOARD_SNAPSHOT_LIFECYCLE_REFRESH_FAILED reason=%s', reason)


def _refresh_dashboard_plan_snapshots_deferred(reason: str = '', *, lifecycle_limit: int = 300, cloud_asset_ids=None, full_cloud_assets: bool | None = None):
    normalized_asset_ids = _normalize_asset_ids(cloud_asset_ids)
    if full_cloud_assets is None:
        full_cloud_assets = normalized_asset_ids is None
    if full_cloud_assets:
        scope_key = 'full'
    else:
        scope_key = 'assets:' + ','.join(str(value) for value in normalized_asset_ids or [])
        if len(scope_key) > 120:
            digest = hashlib.sha1(scope_key.encode('utf-8')).hexdigest()[:16]
            scope_key = f'assets:{digest}'
    lock_key = f'dashboard:snapshot-refresh:deferred:{scope_key}'
    if not cache.add(lock_key, reason or 'pending', timeout=60):
        logger.info('DASHBOARD_SNAPSHOT_DEFERRED_SKIPPED reason=%s scope=%s', reason, scope_key)
        return

    def _run():
        close_old_connections()
        try:
            _refresh_dashboard_plan_snapshots(
                reason,
                lifecycle_limit=lifecycle_limit,
                cloud_asset_ids=normalized_asset_ids,
                full_cloud_assets=full_cloud_assets,
            )
        finally:
            cache.delete(lock_key)
            close_old_connections()

    try:
        threading.Thread(target=_run, name='dashboard-snapshot-refresh', daemon=True).start()
    except RuntimeError as exc:
        cache.delete(lock_key)
        if _is_interpreter_shutdown_error(exc):
            logger.info('DASHBOARD_SNAPSHOT_DEFERRED_SKIPPED reason=%s scope=%s error=%s', reason, scope_key, exc)
        else:
            logger.exception('DASHBOARD_SNAPSHOT_DEFERRED_START_FAILED reason=%s scope=%s', reason, scope_key)
