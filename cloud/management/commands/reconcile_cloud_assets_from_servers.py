from django.core.management.base import BaseCommand
from django.db.models import Q

from cloud.models import CloudAsset, Server


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


def _resolve_asset(server: Server):
    lookup = Q(kind=CloudAsset.KIND_SERVER)
    candidates = Q()
    if server.order_id:
        candidates |= Q(order=server.order)
    if server.instance_id:
        candidates |= Q(instance_id=server.instance_id)
    if server.provider_resource_id:
        candidates |= Q(provider_resource_id=server.provider_resource_id)
    if server.public_ip:
        candidates |= Q(public_ip=server.public_ip)
    if not candidates:
        return None
    return CloudAsset.objects.filter(lookup & candidates).order_by('-updated_at', '-id').first()


def _should_reconcile_server(server: Server) -> bool:
    order = getattr(server, 'order', None)
    provider_status = str(getattr(server, 'provider_status', '') or '')
    note = str(getattr(server, 'note', '') or '')
    return not (
        server.status in RESIDUAL_SERVER_STATUSES
        or (order and order.status in RESIDUAL_ORDER_STATUSES)
        or not getattr(server, 'is_active', True)
        or '云上未找到' in provider_status
        or '云上未找到' in note
    )


class Command(BaseCommand):
    help = '按服务器表兜底补齐/回写代理列表资产记录，确保服务器存在时代理列表里也有对应项'

    def handle(self, *args, **options):
        before_asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        server_total = Server.objects.count()
        created_count = 0
        updated_count = 0
        skipped_count = 0
        created_asset_ids = []
        updated_asset_ids = []
        skipped_asset_ids = []
        conflict_skipped_items = []
        claimed_assets = {}
        queryset = Server.objects.select_related('order', 'user').order_by('-updated_at', '-id')
        for server in queryset:
            if not _should_reconcile_server(server):
                skipped_count += 1
                skipped_asset_ids.append(f'server#{server.id}:{server.public_ip or server.previous_public_ip or "缺失"}:{server.instance_id or server.server_name or "-"}:inactive')
                continue
            asset = _resolve_asset(server)
            order = server.order
            defaults = {
                'kind': CloudAsset.KIND_SERVER,
                'source': SOURCE_MAP.get(server.source, CloudAsset.SOURCE_ORDER),
                'provider': server.provider,
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
                'note': server.note,
                'sort_order': server.sort_order or 99,
                'status': server.status,
                'provider_status': server.provider_status,
                'is_active': server.is_active,
            }
            if asset:
                defaults['user'] = asset.user
                defaults['actual_expires_at'] = asset.actual_expires_at
                if asset.price is not None:
                    defaults['price'] = asset.price
                if asset.note:
                    defaults['note'] = asset.note
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
            created_count += 1
            created_asset_ids.append(f'{created.id}:{server.public_ip or "缺失"}:{server.instance_id or server.server_name or "-"}')

        after_asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        self.stdout.write(self.style.SUCCESS(
            f'代理列表补齐汇总：服务器表 {server_total} 条；代理列表原有 {before_asset_total} 条；新增 {created_count} 条，更新 {updated_count} 条，跳过 {skipped_count} 条；补齐后代理列表共 {after_asset_total} 条。'
        ))
        self.stdout.write(
            f'代理列表补齐详情：新增ID={created_asset_ids[:20] or []}；更新ID={updated_asset_ids[:20] or []}；跳过ID={skipped_asset_ids[:20] or []}；冲突跳过={conflict_skipped_items[:20] or []}'
        )
