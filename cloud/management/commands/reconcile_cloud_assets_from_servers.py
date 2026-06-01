from django.core.management.base import BaseCommand
from django.db.models import Case, IntegerField, Q, Value, When

from cloud.models import CloudAsset
from cloud.server_records import Server
from core.cloud_accounts import cloud_account_label_variants, get_cloud_account_from_label


SOURCE_MAP = {
    Server.SOURCE_ALIYUN: CloudAsset.SOURCE_ALIYUN,
    Server.SOURCE_AWS_MANUAL: CloudAsset.SOURCE_AWS_MANUAL,
    Server.SOURCE_AWS_SYNC: CloudAsset.SOURCE_AWS_SYNC,
    Server.SOURCE_ORDER: CloudAsset.SOURCE_ORDER,
}

RESIDUAL_SERVER_STATUSES = {
    Server.STATUS_DELETED,
    Server.STATUS_DELETING,
    Server.STATUS_TERMINATED,
    Server.STATUS_TERMINATING,
    Server.STATUS_EXPIRED,
}
RESIDUAL_ORDER_STATUSES = {'deleted', 'deleting', 'expired', 'cancelled', 'refunded', 'failed'}


def _visible_asset_total():
    from cloud.api import _cloud_assets_base_queryset, _dedupe_cloud_asset_rows
    return len(_dedupe_cloud_asset_rows(list(_cloud_assets_base_queryset())))


def _resolve_asset(server: Server):
    lookup = Q(kind=CloudAsset.KIND_SERVER)
    if server.provider:
        lookup &= Q(provider=server.provider)
    account = _server_cloud_account(server)
    if account:
        lookup &= (Q(cloud_account=account) | Q(account_label__in=cloud_account_label_variants(account)))
    elif server.account_label:
        lookup &= Q(account_label=server.account_label)
    queryset = CloudAsset.objects.filter(lookup)
    ordering = [
        Case(
            When(status=CloudAsset.STATUS_DELETING, then=Value(0)),
            When(status=CloudAsset.STATUS_RUNNING, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        ),
        '-updated_at',
        '-id',
    ]
    public_ip = str(server.public_ip or '').strip()
    previous_public_ip = str(server.previous_public_ip or '').strip()
    if public_ip:
        asset = queryset.filter(Q(public_ip=public_ip) | Q(previous_public_ip=public_ip)).order_by(
            Case(
                When(public_ip=public_ip, then=Value(0)),
                When(previous_public_ip=public_ip, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            ),
            *ordering,
        ).first()
        if asset:
            return asset
    if previous_public_ip:
        asset = queryset.filter(Q(public_ip=previous_public_ip) | Q(previous_public_ip=previous_public_ip)).order_by(*ordering).first()
        if asset:
            return asset
    if server.order_id:
        asset = queryset.filter(order=server.order).order_by(*ordering).first()
        if asset:
            return asset
    direct_candidates = Q()
    if server.instance_id:
        direct_candidates |= Q(instance_id=server.instance_id)
    if server.provider_resource_id:
        direct_candidates |= Q(provider_resource_id=server.provider_resource_id)
    if not direct_candidates:
        return None
    if server.region_code:
        queryset = queryset.filter(region_code=server.region_code)
    return queryset.filter(direct_candidates).order_by(*ordering).first()


def _server_cloud_account(server: Server):
    order = getattr(server, 'order', None)
    if order and getattr(order, 'cloud_account_id', None):
        return order.cloud_account
    return get_cloud_account_from_label(server.account_label or '', server.provider)


def _should_reconcile_server(server: Server) -> bool:
    order = getattr(server, 'order', None)
    account = _server_cloud_account(server)
    provider_status = str(getattr(server, 'provider_status', '') or '')
    note = str(getattr(server, 'note', '') or '')
    return not (
        server.status in RESIDUAL_SERVER_STATUSES
        or (order and order.status in RESIDUAL_ORDER_STATUSES)
        or not getattr(server, 'is_active', True)
        or (server.account_label and not account)
        or (account and not account.is_active)
        or '云上未找到' in provider_status
        or '云上不存在' in provider_status
        or '已标记删除' in provider_status
        or '云上未找到' in note
        or '云上不存在' in note
        or '已标记删除' in note
    )


class Command(BaseCommand):
    help = '按服务器表兜底补齐/回写代理列表资产记录，确保服务器存在时代理列表里也有对应项'

    def handle(self, *args, **options):
        before_asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        before_visible_asset_total = _visible_asset_total()
        server_total = Server.objects.count()
        created_count = 0
        updated_count = 0
        skipped_count = 0
        created_asset_ids = []
        updated_asset_ids = []
        skipped_asset_ids = []
        conflict_skipped_items = []
        claimed_assets = {}
        processed_asset_ids = set()
        queryset = Server.objects.select_related('order', 'user').order_by('-updated_at', '-id')
        for server in queryset:
            if not _should_reconcile_server(server):
                skipped_count += 1
                skipped_asset_ids.append(f'server#{server.id}:{server.public_ip or server.previous_public_ip or "缺失"}:{server.instance_id or server.server_name or "-"}:inactive')
                continue
            asset = _resolve_asset(server)
            order = server.order
            cloud_account = _server_cloud_account(server)
            account_label = server.account_label or getattr(order, 'account_label', None) or ''
            defaults = {
                'kind': CloudAsset.KIND_SERVER,
                'source': SOURCE_MAP.get(server.source, CloudAsset.SOURCE_ORDER),
                'provider': server.provider,
                'cloud_account': cloud_account,
                'account_label': account_label,
                'region_code': server.region_code,
                'region_name': server.region_name,
                'asset_name': server.server_name,
                'instance_id': server.instance_id,
                'provider_resource_id': server.provider_resource_id,
                'public_ip': server.public_ip,
                'previous_public_ip': server.previous_public_ip,
                'login_user': server.login_user,
                'login_password': server.login_password,
                'mtproxy_port': getattr(order, 'mtproxy_port', None) if order else None,
                'mtproxy_link': getattr(order, 'mtproxy_link', None) if order else None,
                'mtproxy_secret': getattr(order, 'mtproxy_secret', None) if order else None,
                'mtproxy_host': getattr(order, 'mtproxy_host', None) if order else None,
                'actual_expires_at': server.expires_at,
                'price': getattr(order, 'total_amount', None) if order else None,
                'currency': getattr(order, 'currency', None) if order else 'USDT',
                'order': order,
                'user': server.user,
                'sort_order': server.sort_order or 99,
                'status': server.status,
                'provider_status': server.provider_status,
                'is_active': server.is_active,
            }
            if asset:
                if asset.id in processed_asset_ids:
                    skipped_count += 1
                    skipped_asset_ids.append(f'{asset.id}:{server.public_ip or "缺失"}:{server.instance_id or server.server_name or "-"}:duplicate')
                    continue
                defaults['user'] = asset.user
                defaults['actual_expires_at'] = asset.actual_expires_at
                if asset.price is not None:
                    defaults['price'] = asset.price
                asset_signature = f'{server.instance_id or "-"}|{server.provider_resource_id or "-"}|{server.public_ip or "缺失"}'
                claimed_signature = claimed_assets.get(asset.id)
                if claimed_signature and claimed_signature != asset_signature:
                    skipped_count += 1
                    occupied_ip = claimed_signature.split('|')[-1]
                    current_ip = asset_signature.split('|')[-1]
                    conflict_skipped_items.append(f'{asset.id}:{occupied_ip}->{current_ip}')
                    self.stdout.write(
                        self.style.WARNING(
                            f'冲突已跳过 资产#{asset.id} 已占IP={occupied_ip} 当前IP={current_ip}'
                        )
                    )
                    continue
                claimed_assets[asset.id] = asset_signature
                processed_asset_ids.add(asset.id)
                dirty = False
                for key, value in defaults.items():
                    if getattr(asset, key) != value:
                        setattr(asset, key, value)
                        dirty = True
                if dirty:
                    asset.save()
                    updated_count += 1
                    updated_asset_ids.append(f'{asset.id}:{server.public_ip or "缺失"}:{server.instance_id or server.server_name or "-"}')
                else:
                    skipped_count += 1
                    skipped_asset_ids.append(f'{asset.id}:{server.public_ip or "缺失"}:{server.instance_id or server.server_name or "-"}')
                continue
            created = CloudAsset.objects.create(**defaults)
            claimed_assets[created.id] = f'{server.instance_id or "-"}|{server.provider_resource_id or "-"}|{server.public_ip or "缺失"}'
            processed_asset_ids.add(created.id)
            created_count += 1
            created_asset_ids.append(f'{created.id}:{server.public_ip or "缺失"}:{server.instance_id or server.server_name or "-"}')

        after_asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        after_visible_asset_total = _visible_asset_total()
        self.stdout.write(self.style.SUCCESS(
            f'代理列表补齐汇总：服务器表 {server_total} 条；资产总记录 {before_asset_total}->{after_asset_total} 条；当前可见代理 {before_visible_asset_total}->{after_visible_asset_total} 条；新增 {created_count} 条，更新 {updated_count} 条，跳过 {skipped_count} 条。'
        ))
        self.stdout.write(
            f'代理列表补齐详情：新增ID={created_asset_ids[:20] or []}；更新ID={updated_asset_ids[:20] or []}；跳过ID={skipped_asset_ids[:20] or []}；冲突跳过={conflict_skipped_items[:20] or []}'
        )
