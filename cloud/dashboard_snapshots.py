"""Dashboard snapshot refresh coordination for cloud runtime changes."""

import logging
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
    return sys.is_finalizing() or 'interpreter shutdown' in message or 'cannot schedule new futures' in message


def _refresh_dashboard_plan_snapshots(reason: str = '', *, lifecycle_limit: int = 1000):
    try:
        from cloud import api as cloud_api
        cloud_api._sync_auto_renew_plan_table(now=timezone.now())
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
        from cloud import api as cloud_api
        cloud_api._sync_notice_plan_table(limit=500, future_limit=200, history_limit=1000)
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
    _refresh_lifecycle_plan_snapshot(reason, lifecycle_limit=lifecycle_limit)


def _refresh_lifecycle_plan_snapshot(reason: str = '', *, lifecycle_limit: int = 1000):
    try:
        from bot import api as bot_api
        bot_api._sync_lifecycle_plan_table(limit=lifecycle_limit)
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


def _refresh_dashboard_plan_snapshots_deferred(reason: str = '', *, lifecycle_limit: int = 300):
    lock_key = 'dashboard:snapshot-refresh:deferred'
    if not cache.add(lock_key, reason or 'pending', timeout=60):
        logger.info('DASHBOARD_SNAPSHOT_DEFERRED_SKIPPED reason=%s', reason)
        return

    def _run():
        close_old_connections()
        try:
            _refresh_dashboard_plan_snapshots(reason, lifecycle_limit=lifecycle_limit)
        finally:
            cache.delete(lock_key)
            close_old_connections()

    threading.Thread(target=_run, name='dashboard-snapshot-refresh', daemon=True).start()
