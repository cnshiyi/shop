from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils.dateparse import parse_datetime

from bot.models import TelegramUser
from cloud.models import CloudAsset
from core.cloud_accounts import cloud_account_label, get_cloud_account_from_label


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
        parser.add_argument('--account-label', default='', help='云账号标识；建议填写，避免跨账号覆盖同名实例')
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
        provider = (options.get('provider') or '').strip()
        account_label = (options.get('account_label') or '').strip()
        cloud_account = get_cloud_account_from_label(account_label, provider) if account_label else None
        if cloud_account and not account_label:
            account_label = cloud_account_label(cloud_account)
        if not instance_id and not asset_name:
            raise CommandError('至少提供 --instance-id 或 --asset-name 之一。')

        actual_expires_at = None
        if options.get('actual_expires_at'):
            actual_expires_at = parse_datetime(options['actual_expires_at'])
            if actual_expires_at is None:
                raise CommandError('`--actual-expires-at` 格式错误，请传 ISO 时间。')

        lookup_q = Q(kind=options['kind'], provider=provider)
        if account_label:
            lookup_q &= Q(account_label=account_label)
        else:
            lookup_q &= (Q(account_label='') | Q(account_label__isnull=True))
        if options.get('region_code'):
            lookup_q &= Q(region_code=options['region_code'])
        if options.get('public_ip'):
            lookup_q &= Q(public_ip=options['public_ip'])
        elif instance_id:
            lookup_q &= Q(instance_id=instance_id)
        else:
            lookup_q &= Q(asset_name=asset_name)

        user = None
        if options.get('user'):
            user = _resolve_user(options['user'])
            if not user:
                raise CommandError('未找到匹配的绑定用户。')

        create_defaults = {
            'source': CloudAsset.SOURCE_AWS_MANUAL,
            'provider': provider,
            'cloud_account': cloud_account,
            'account_label': account_label or None,
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
        }
        asset = CloudAsset.objects.filter(lookup_q).order_by('-updated_at', '-id').first()
        created = asset is None
        if created:
            asset = CloudAsset.objects.create(
                kind=options['kind'],
                instance_id=instance_id or None,
                **create_defaults,
            )
        if not created:
            update_fields = []
            updates = {
                'source': CloudAsset.SOURCE_AWS_MANUAL,
                'provider': provider,
                'cloud_account': cloud_account,
                'account_label': account_label or asset.account_label,
                'asset_name': asset_name or instance_id,
                'currency': options.get('currency') or 'USDT',
            }
            for field, value in updates.items():
                setattr(asset, field, value)
                update_fields.append(field)
            optional_updates = {
                'region_code': ('region_code', options['region_code'] or None),
                'region_name': ('region_name', options['region_name'] or None),
                'public_ip': ('public_ip', options['public_ip'] or None),
                'mtproxy_port': ('mtproxy_port', options.get('mtproxy_port')),
                'mtproxy_link': ('mtproxy_link', options['mtproxy_link'] or None),
                'mtproxy_secret': ('mtproxy_secret', options['mtproxy_secret'] or None),
                'note': ('note', options['note'] or None),
            }
            for field, (option_key, value) in optional_updates.items():
                if options.get(option_key) not in (None, ''):
                    setattr(asset, field, value)
                    update_fields.append(field)
            if options.get('actual_expires_at'):
                asset.actual_expires_at = actual_expires_at
                update_fields.append('actual_expires_at')
            if options.get('price'):
                asset.price = parse_decimal(options.get('price'))
                update_fields.append('price')
            if options.get('user'):
                asset.user = user
                update_fields.append('user')
            if options['inactive']:
                asset.is_active = False
                update_fields.append('is_active')
            if update_fields:
                update_fields.append('updated_at')
                asset.save(update_fields=sorted(set(update_fields)))
        action = '创建' if created else '更新'
        self.stdout.write(self.style.SUCCESS(f'{action}成功: {asset.id} {asset.asset_name or asset.instance_id}'))
