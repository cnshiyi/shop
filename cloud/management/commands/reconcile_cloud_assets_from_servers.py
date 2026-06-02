from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from cloud.models import CloudAsset, CloudIpLog
from core.cloud_accounts import cloud_account_label, cloud_account_label_variants, normalize_cloud_account_provider
from core.models import CloudAccountConfig


def _account_from_any_label(label: str, provider: str | None = None):
    text = str(label or '').strip()
    if not text:
        return None
    normalized_provider = normalize_cloud_account_provider(provider or '')
    queryset = CloudAccountConfig.objects.all()
    if normalized_provider:
        queryset = queryset.filter(provider=normalized_provider)
    for account in queryset.order_by('id'):
        if text in cloud_account_label_variants(account):
            return account
    return None


def _is_compat_server_record(asset: CloudAsset) -> bool:
    return bool((asset.sync_state or {}).get('compat_server_record'))


def _is_cloud_missing_record(asset: CloudAsset) -> bool:
    text = '\n'.join([
        str(asset.provider_status or ''),
        str(asset.note or ''),
    ])
    return any(marker in text for marker in ['云上未找到', '云上不存在', '已标记删除'])


def _account_scope_q(asset: CloudAsset, account):
    if account:
        labels = cloud_account_label_variants(account)
        return Q(cloud_account=account) | Q(account_label__in=labels)
    label = str(asset.account_label or '').strip()
    if label:
        return Q(account_label=label)
    return Q(account_label__isnull=True) | Q(account_label='')


def _identity_q(asset: CloudAsset):
    identity = Q()
    for field in ['public_ip', 'previous_public_ip', 'instance_id', 'provider_resource_id']:
        value = str(getattr(asset, field, '') or '').strip()
        if value:
            identity |= Q(**{field: value})
    name = str(asset.asset_name or '').strip()
    if name:
        identity |= Q(asset_name=name) | Q(instance_id=name)
    return identity


def _merge_asset_into_keeper(source: CloudAsset, keeper: CloudAsset, account) -> None:
    update_fields = []
    if account and keeper.cloud_account_id != account.id:
        keeper.cloud_account = account
        update_fields.append('cloud_account')
    if source.account_label and keeper.account_label != source.account_label:
        keeper.account_label = source.account_label
        update_fields.append('account_label')
    for field in ['user', 'order']:
        if getattr(source, f'{field}_id', None) and not getattr(keeper, f'{field}_id', None):
            setattr(keeper, field, getattr(source, field))
            update_fields.append(field)
    for field in ['public_ip', 'previous_public_ip', 'instance_id', 'provider_resource_id', 'asset_name', 'region_name']:
        if getattr(source, field, None) and not getattr(keeper, field, None):
            setattr(keeper, field, getattr(source, field))
            update_fields.append(field)
    if update_fields:
        keeper.save(update_fields=[*dict.fromkeys(update_fields), 'updated_at'])
    CloudIpLog.objects.filter(asset=source).update(asset=keeper)
    source.delete()


class Command(BaseCommand):
    help = '历史兼容命令：在统一云资产表中归一化服务器形态记录'

    def handle(self, *args, **options):
        deleted_count = 0
        merged_count = 0
        normalized_count = 0
        with transaction.atomic():
            assets = list(CloudAsset.objects.select_related('cloud_account').filter(kind=CloudAsset.KIND_SERVER).order_by('id'))
            for asset in assets:
                if not CloudAsset.objects.filter(id=asset.id).exists():
                    continue
                account = asset.cloud_account or _account_from_any_label(asset.account_label, asset.provider)
                if account and not account.is_active and _is_compat_server_record(asset):
                    asset.delete()
                    deleted_count += 1
                    continue
                if (
                    _is_cloud_missing_record(asset)
                    and _is_compat_server_record(asset)
                    and asset.status not in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED}
                ):
                    asset.delete()
                    deleted_count += 1
                    continue
                update_fields = []
                if account and asset.cloud_account_id != account.id:
                    asset.cloud_account = account
                    update_fields.append('cloud_account')
                if account and not asset.account_label:
                    asset.account_label = cloud_account_label(account)
                    update_fields.append('account_label')
                if update_fields:
                    asset.save(update_fields=[*update_fields, 'updated_at'])
                    normalized_count += 1

                identity = _identity_q(asset)
                if not identity:
                    continue
                candidates = list(
                    CloudAsset.objects
                    .select_related('cloud_account')
                    .filter(kind=CloudAsset.KIND_SERVER, provider=asset.provider, region_code=asset.region_code)
                    .filter(_account_scope_q(asset, account))
                    .filter(identity)
                    .exclude(id=asset.id)
                    .order_by('-updated_at', '-id')
                )
                if not candidates:
                    continue
                candidates.sort(key=lambda item: (
                    1 if not _is_compat_server_record(item) else 0,
                    1 if item.is_active else 0,
                    item.updated_at.timestamp() if item.updated_at else 0,
                    item.id,
                ), reverse=True)
                keeper = candidates[0]
                if _is_compat_server_record(keeper) and not _is_compat_server_record(asset):
                    source = keeper
                    keeper = asset
                else:
                    source = asset
                _merge_asset_into_keeper(source, keeper, account)
                merged_count += 1

        total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        visible_total = (
            CloudAsset.objects
            .filter(kind=CloudAsset.KIND_SERVER, is_active=True)
            .exclude(status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED])
            .count()
        )
        self.stdout.write(self.style.SUCCESS(
            f'统一云资产归一完成：规范账号 {normalized_count} 条；合并重复 {merged_count} 条；清理无效兼容记录 {deleted_count} 条；cloud_asset server 总数={total}，可见数={visible_total}。'
        ))
