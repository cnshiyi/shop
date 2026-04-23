from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from bot.models import TelegramUser
from cloud.models import CloudAsset, Server


def _resolve_user(value):
    raw = str(value or '').strip().lstrip('@')
    if not raw:
        return None
    if raw.isdigit():
        return TelegramUser.objects.filter(tg_user_id=int(raw)).first() or TelegramUser.objects.filter(id=int(raw)).first()
    return TelegramUser.objects.filter(username__icontains=raw).first()


def parse_decimal(value):
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        raise CommandError('`--price` 格式错误。')


class Command(BaseCommand):
    help = '手工录入或更新 AWS / MTProxy 资产到统一云资产表'

    def add_arguments(self, parser):
        parser.add_argument('--kind', required=True, choices=[CloudAsset.KIND_SERVER, CloudAsset.KIND_MTPROXY])
        parser.add_argument('--instance-id', default='')
        parser.add_argument('--asset-name', default='')
        parser.add_argument('--provider', default='aws_lightsail')
        parser.add_argument('--region-code', default='')
        parser.add_argument('--region-name', default='')
        parser.add_argument('--public-ip', default='')
        parser.add_argument('--mtproxy-port', type=int)
        parser.add_argument('--mtproxy-link', default='')
        parser.add_argument('--mtproxy-secret', default='')
        parser.add_argument('--actual-expires-at', default='')
        parser.add_argument('--price', default='')
        parser.add_argument('--currency', default='USDT')
        parser.add_argument('--note', default='')
        parser.add_argument('--user', default='', help='绑定用户，可填后台用户ID、Telegram用户ID或用户名')
        parser.add_argument('--inactive', action='store_true')

    def handle(self, *args, **options):
        instance_id = (options.get('instance_id') or '').strip()
        asset_name = (options.get('asset_name') or '').strip()
        if not instance_id and not asset_name:
            raise CommandError('至少提供 --instance-id 或 --asset-name 之一。')

        actual_expires_at = None
        if options.get('actual_expires_at'):
            actual_expires_at = parse_datetime(options['actual_expires_at'])
            if actual_expires_at is None:
                raise CommandError('`--actual-expires-at` 格式错误，请传 ISO 时间。')

        lookup = {'kind': options['kind']}
        if instance_id:
            lookup['instance_id'] = instance_id
        else:
            lookup['asset_name'] = asset_name

        user = None
        if options.get('user'):
            user = _resolve_user(options['user'])
            if not user:
                raise CommandError('未找到匹配的绑定用户。')

        asset, created = CloudAsset.objects.update_or_create(
            **lookup,
            defaults={
                'source': CloudAsset.SOURCE_AWS_MANUAL,
                'provider': options['provider'],
                'region_code': options['region_code'] or None,
                'region_name': options['region_name'] or None,
                'asset_name': asset_name or instance_id,
                'public_ip': options['public_ip'] or None,
                'mtproxy_port': options.get('mtproxy_port'),
                'mtproxy_link': options['mtproxy_link'] or None,
                'mtproxy_secret': options['mtproxy_secret'] or None,
                'actual_expires_at': actual_expires_at,
                'price': parse_decimal(options.get('price')),
                'currency': options.get('currency') or 'USDT',
                'user': user,
                'note': options['note'] or None,
                'is_active': not options['inactive'],
            },
        )
        if asset.kind == CloudAsset.KIND_SERVER:
            Server.objects.update_or_create(
                instance_id=asset.instance_id or asset.provider_resource_id or asset.public_ip,
                defaults={
                    'source': Server.SOURCE_AWS_MANUAL,
                    'provider': asset.provider,
                    'account_label': asset.provider,
                    'region_code': asset.region_code,
                    'region_name': asset.region_name,
                    'server_name': asset.asset_name,
                    'provider_resource_id': asset.provider_resource_id or asset.instance_id,
                    'public_ip': asset.public_ip,
                    'previous_public_ip': asset.previous_public_ip,
                    'login_user': asset.login_user,
                    'login_password': asset.login_password,
                    'expires_at': asset.actual_expires_at,
                    'order': asset.order,
                    'user': asset.user,
                    'note': asset.note,
                    'is_active': asset.is_active,
                },
            )
        action = '创建' if created else '更新'
        self.stdout.write(self.style.SUCCESS(f'{action}成功: {asset.id} {asset.asset_name or asset.instance_id}'))
