"""Dashboard API views for server-shaped cloud assets."""

import threading

from django.db.models import Count, Q
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from cloud.models import CloudAsset, CloudIpLog
from cloud.dashboard_api_helpers import _dashboard_expiry_ordering, _dashboard_sort_direction, _preserve_link_status_label, _preserve_link_status_with_countdown
from cloud.services import AWS_REGION_NAMES, create_cloud_server_rebuild_order, record_cloud_ip_log, run_cloud_server_rebuild_job
from core.cloud_accounts import cloud_account_label, cloud_account_label_variants, list_cloud_account_labels
from core.dashboard_api import _apply_keyword_filter, _countdown_label, _days_left, _error, _get_keyword, _iso, _ok, _provider_label, _provider_status_label, _region_label, _server_source_label, _status_label, _user_payload, dashboard_login_required, dashboard_superuser_required
from core.models import CloudAccountConfig


def _server_payload(asset):
    user = asset.user
    user_payload = None
    if user:
        usernames = user.usernames
        user_payload = _user_payload({
            'id': user.id,
            'tg_user_id': user.tg_user_id,
            'username': user.username,
            'first_name': user.first_name,
            'usernames': usernames,
            'primary_username': usernames[0] if usernames else '',
        })
    order = asset.order
    return {
        'id': asset.id,
        'status': asset.status,
        'status_label': '旧机保留中' if asset.status == CloudAsset.STATUS_DELETING and '旧机保留期' in str(asset.provider_status or '') else _status_label(asset.status, CloudAsset.STATUS_CHOICES),
        'source': asset.source,
        'source_label': _server_source_label(asset.source),
        'provider': asset.provider,
        'provider_label': _provider_label(asset.provider),
        'account_label': asset.account_label,
        'region_label': _region_label(asset.region_code, asset.region_name),
        'region_name': asset.region_name,
        'server_name': asset.asset_name,
        'instance_id': asset.instance_id,
        'provider_resource_id': asset.provider_resource_id,
        'public_ip': asset.public_ip,
        'login_user': asset.login_user,
        'expires_at': _iso(asset.actual_expires_at),
        'days_left': _days_left(asset.actual_expires_at),
        'status_countdown': _countdown_label(asset.actual_expires_at),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'order_id': order.id if order else None,
        'order_no': order.order_no if order else '',
        'order_detail_path': f'/admin/cloud-orders/{order.id}' if order else '',
        'provider_status': '已删除' if asset.status == CloudAsset.STATUS_DELETED else _provider_status_label(asset.provider_status),
        'preserve_link_status': _preserve_link_status_with_countdown(
            _preserve_link_status_label(asset.note, getattr(order, 'provision_note', None)),
            _countdown_label(asset.actual_expires_at),
        ),
        'is_active': asset.is_active,
        'updated_at': _iso(asset.updated_at),
    }


@dashboard_login_required
@require_GET
def servers_list(request):
    keyword = _get_keyword(request)
    dedup_raw = (request.GET.get('dedup') or '').lower()
    dedup = dedup_raw not in {'0', 'false', 'no', 'off'}
    sort_by = (request.GET.get('sort_by') or '').strip().lower()
    sort_direction = _dashboard_sort_direction(request)
    ordering = ['actual_expires_at', '-updated_at', '-id']
    if sort_by in {'expires_at', 'days_left', 'remaining_days'}:
        ordering = _dashboard_expiry_ordering('actual_expires_at', sort_direction)
    unattached_ip_q = (
        (Q(provider_status__icontains='未附加') | Q(note__icontains='未附加IP') | Q(note__icontains='未附加固定IP'))
        & (Q(instance_id__isnull=True) | Q(instance_id=''))
    )
    queryset = CloudAsset.objects.select_related('user', 'order').filter(kind=CloudAsset.KIND_SERVER).exclude(status=CloudAsset.STATUS_DELETED).exclude(public_ip__isnull=True).exclude(public_ip='').exclude(unattached_ip_q).order_by(*ordering)
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['asset_name', 'instance_id', 'public_ip', 'account_label', 'provider', 'region_name', 'user__tg_user_id', 'user__username', 'order__order_no'],
    )
    provider = (request.GET.get('provider') or '').strip()
    region_code = (request.GET.get('region_code') or '').strip()
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(region_code=region_code)
    items = [_server_payload(asset) for asset in queryset[:500]]
    if dedup:
        seen = set()
        deduped = []
        for item in items:
            dedup_key = (item.get('provider') or '', item.get('instance_id') or '', item.get('public_ip') or '', item.get('server_name') or '')
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            deduped.append(item)
        items = deduped
    return _ok(items)


@csrf_exempt
@dashboard_superuser_required
@require_POST
def rebuild_server_preserve_link(request, server_id: int):
    asset = CloudAsset.objects.select_related('order').filter(id=server_id, kind=CloudAsset.KIND_SERVER).first()
    if not asset or not asset.order_id:
        return _error('服务器不存在或未关联订单', status=404)
    order, error = create_cloud_server_rebuild_order(asset.order_id)
    if error:
        return _error(error, status=400)
    thread = threading.Thread(target=run_cloud_server_rebuild_job, args=(order.id,), daemon=True)
    thread.start()
    return _ok({
        'accepted': True,
        'message': '已发起 AWS 重装迁移，后台失败会自动重试（最多 3 次），成功后旧实例保留 3 天再删除。',
        'order_id': order.id,
        'order_no': order.order_no,
        'replacement_for_id': order.replacement_for_id,
    })


@csrf_exempt
@dashboard_superuser_required
@require_http_methods(['POST', 'DELETE'])
def delete_server(request, server_id: int):
    asset = CloudAsset.objects.select_related('order').filter(id=server_id, kind=CloudAsset.KIND_SERVER).first()
    if not asset:
        return _error('服务器不存在', status=404)
    now = timezone.now()
    before_status = asset.status
    note = f'后台手动删除服务器列表记录；时间: {now.isoformat()}'
    previous_public_ip = asset.public_ip or asset.previous_public_ip
    order = asset.order

    record_cloud_ip_log(event_type='deleted', order=order, asset=asset, previous_public_ip=previous_public_ip, public_ip=None, note=note)
    asset.delete()
    return _ok({
        'target_type': 'cloud_asset',
        'target_id': server_id,
        'before_status': before_status,
        'after_status': None,
        'hard_deleted': True,
        'exists_after': CloudAsset.objects.filter(id=server_id, kind=CloudAsset.KIND_SERVER).exists(),
        'removed_assets': 0,
        'order_status_changed': False,
    })


def _statistics_account_label(account) -> str:
    return cloud_account_label(account)


def _cloud_account_labels_queryset(is_active: bool | None = None):
    return list_cloud_account_labels(is_active)


@dashboard_login_required
@require_GET
def servers_statistics(request):
    keyword = _get_keyword(request)
    aws_regions = [{'region_code': code, 'region_label': label} for code, label in AWS_REGION_NAMES.items()]
    region_pairs = [*aws_regions, {'region_code': 'cn-hongkong', 'region_label': '香港'}]
    region_codes = [item['region_code'] for item in region_pairs]

    active_statuses = [
        CloudAsset.STATUS_RUNNING,
        CloudAsset.STATUS_PENDING,
        CloudAsset.STATUS_STARTING,
        CloudAsset.STATUS_STOPPED,
        CloudAsset.STATUS_SUSPENDED,
        CloudAsset.STATUS_EXPIRED_GRACE,
    ]
    active_account_labels = _cloud_account_labels_queryset(True)
    inactive_account_labels = _cloud_account_labels_queryset(False)
    queryset = CloudAsset.objects.select_related('order', 'order__cloud_account').filter(kind=CloudAsset.KIND_SERVER, status__in=active_statuses).exclude(
        account_label__in=inactive_account_labels,
    ).filter(
        Q(account_label__in=active_account_labels)
        | Q(account_label__isnull=True)
        | Q(account_label='')
        | Q(order__cloud_account__is_active=True)
    )
    if keyword:
        queryset = _apply_keyword_filter(
            queryset,
            keyword,
            ['region_code', 'region_name', 'provider', 'account_label', 'asset_name', 'instance_id', 'public_ip'],
        )
    rows = list(
        queryset
        .values('provider', 'region_code', 'region_name', 'account_label')
        .annotate(total_count=Count('id'))
        .order_by('account_label', 'provider', 'region_name')
    )

    account_map = {}
    active_accounts = list(CloudAccountConfig.objects.filter(provider__in=[CloudAccountConfig.PROVIDER_AWS, CloudAccountConfig.PROVIDER_ALIYUN], is_active=True).order_by('provider', 'id'))
    for account in active_accounts:
        technical_label = cloud_account_label(account)
        display_label = _statistics_account_label(account)
        if keyword and keyword.lower() not in technical_label.lower() and keyword.lower() not in display_label.lower() and keyword.lower() not in account.provider.lower():
            has_server_match = any((row.get('account_label') or '') == technical_label for row in rows)
            if not has_server_match:
                continue
        account_map[technical_label] = {
            'account_id': technical_label,
            'account_label': display_label,
            'provider_label': 'AWS' if account.provider == CloudAccountConfig.PROVIDER_AWS else '阿里云',
            'regions': {},
            'total_count': 0,
            'sort_key': (account.provider, account.id),
        }

    for row in rows:
        technical_label = row['account_label'] or '-'
        entry = account_map.setdefault(
            technical_label,
            {
                'account_id': technical_label,
                'account_label': technical_label,
                'provider_label': _provider_label(row['provider']),
                'regions': {},
                'total_count': 0,
                'sort_key': (row['provider'] or '', 999999, technical_label),
            },
        )
        region_key = row['region_code'] or _region_label(row['region_code'] or '', row['region_name'])
        if region_key not in region_codes:
            continue
        count = row['total_count']
        entry['regions'][region_key] = entry['regions'].get(region_key, 0) + count
        entry['total_count'] += count

    items = []
    totals = {'account_id': '合计', 'account_label': '合计', 'provider_label': '-', 'regions': {}, 'total_count': 0}
    for technical_label, entry in sorted(account_map.items(), key=lambda item: item[1]['sort_key']):
        row_payload = {
            'account_id': entry['account_id'],
            'account_label': entry['account_label'],
            'provider_label': entry['provider_label'],
            'total_count': entry['total_count'],
        }
        for region in region_pairs:
            region_key = region['region_code']
            value = entry['regions'].get(region_key, 0)
            row_payload[region_key] = value
            totals['regions'][region_key] = totals['regions'].get(region_key, 0) + value
        totals['total_count'] += entry['total_count']
        items.append(row_payload)

    total_row = {
        'account_id': totals['account_id'],
        'account_label': totals['account_label'],
        'provider_label': totals['provider_label'],
        'total_count': totals['total_count'],
    }
    for region in region_pairs:
        total_row[region['region_code']] = totals['regions'].get(region['region_code'], 0)

    return _ok({
        'regions': region_pairs,
        'items': items,
        'summary': total_row,
    })
