import logging

from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from bot.models import AdminReplyLink, TelegramChatMessage
from cloud.models import CloudServerOrder
from core.models import SiteConfig
from orders.models import Order, Recharge

logger = logging.getLogger(__name__)


DEFAULT_RETENTION_DAYS = 100
MIN_RETENTION_DAYS = 30


class Command(BaseCommand):
    help = 'Delete old orders and Telegram chat records beyond retention days.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=None, help='Retention days, default from SiteConfig cleanup_retention_days or 100.')
        parser.add_argument('--dry-run', action='store_true', help='Only print counts, do not delete.')

    def handle(self, *args, **options):
        days = options.get('days') or self._configured_days()
        days = max(int(days or DEFAULT_RETENTION_DAYS), MIN_RETENTION_DAYS)
        cutoff = timezone.now() - timezone.timedelta(days=days)
        dry_run = bool(options.get('dry_run'))

        regular_orders = Order.objects.filter(created_at__lt=cutoff)
        recharges = Recharge.objects.filter(created_at__lt=cutoff)
        chat_messages = TelegramChatMessage.objects.filter(created_at__lt=cutoff)
        admin_reply_links = AdminReplyLink.objects.filter(created_at__lt=cutoff)
        cloud_orders = CloudServerOrder.objects.filter(created_at__lt=cutoff).filter(self._cloud_order_cleanup_filter(cutoff))

        targets = {
            'orders': regular_orders,
            'recharges': recharges,
            'cloud_orders': cloud_orders,
            'telegram_chat_messages': chat_messages,
            'admin_reply_links': admin_reply_links,
        }
        counts = {name: qs.count() for name, qs in targets.items()}
        message = f'CLEANUP_OLD_RECORDS days={days} cutoff={cutoff.isoformat()} dry_run={dry_run} counts={counts}'
        logger.info(message)
        self.stdout.write(message)
        self._log_cleanup_candidates(targets)

        if dry_run:
            logger.info('CLEANUP_OLD_RECORDS_DRY_RUN_DONE days=%s cutoff=%s counts=%s', days, cutoff.isoformat(), counts)
            return

        deleted = {}
        for name in ['admin_reply_links', 'telegram_chat_messages', 'orders', 'recharges', 'cloud_orders']:
            deleted_count, deleted_detail = targets[name].delete()
            deleted[name] = deleted_count
            logger.info(
                'CLEANUP_OLD_RECORDS_DELETE_RESULT target=%s deleted_count=%s detail=%s cutoff=%s retention_days=%s',
                name,
                deleted_count,
                deleted_detail,
                cutoff.isoformat(),
                days,
            )

        done_message = f'CLEANUP_OLD_RECORDS_DONE days={days} cutoff={cutoff.isoformat()} deleted={deleted}'
        logger.info(done_message)
        self.stdout.write(self.style.SUCCESS(done_message))

    def _log_cleanup_candidates(self, targets: dict):
        for name, qs in targets.items():
            sample = self._candidate_sample(name, qs)
            status_counts = self._status_counts(qs) if name in {'orders', 'recharges', 'cloud_orders'} else []
            logger.info('CLEANUP_OLD_RECORDS_CANDIDATES target=%s sample=%s status_counts=%s', name, sample, status_counts)
            self.stdout.write(f'CLEANUP_OLD_RECORDS_CANDIDATES target={name} sample={sample} status_counts={status_counts}')

    @staticmethod
    def _candidate_sample(name: str, qs):
        base_fields = ['id', 'created_at']
        field_map = {
            'orders': ['order_no', 'user_id', 'status', 'pay_method', 'currency', 'total_amount', 'paid_at', 'expired_at'],
            'recharges': ['user_id', 'status', 'currency', 'amount', 'pay_amount', 'completed_at', 'expired_at'],
            'cloud_orders': ['order_no', 'user_id', 'status', 'provider', 'public_ip', 'previous_public_ip', 'service_expires_at', 'ip_recycle_at'],
            'telegram_chat_messages': ['tg_user_id', 'chat_id', 'message_id', 'direction', 'content_type'],
            'admin_reply_links': ['admin_chat_id', 'admin_message_id', 'user_id', 'user_chat_id', 'user_message_id', 'source_content_type'],
        }
        fields = [*base_fields, *field_map.get(name, [])]
        return list(qs.order_by('created_at', 'id').values(*fields)[:20])

    @staticmethod
    def _status_counts(qs):
        return list(qs.values('status').annotate(total=Count('id')).order_by('status'))

    @staticmethod
    def _configured_days() -> int:
        raw = SiteConfig.get('cleanup_retention_days', str(DEFAULT_RETENTION_DAYS))
        try:
            return int(str(raw).strip() or DEFAULT_RETENTION_DAYS)
        except Exception:
            return DEFAULT_RETENTION_DAYS

    @staticmethod
    def _cloud_order_cleanup_filter(cutoff):
        terminal_statuses = {'deleted', 'cancelled', 'expired', 'failed'}
        expired_inactive = (
            Q(status__in={'completed', 'expiring', 'renew_pending', 'suspended', 'deleting'})
            & Q(service_expires_at__lt=cutoff)
            & (Q(ip_recycle_at__isnull=True) | Q(ip_recycle_at__lt=cutoff))
        )
        return Q(status__in=terminal_statuses) | expired_inactive
