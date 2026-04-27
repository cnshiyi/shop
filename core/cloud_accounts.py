from django.apps import apps
from django.db.models import Count, Q


_PROVIDER_ALIASES = {
    'aws_lightsail': 'aws',
    'aws': 'aws',
    'aliyun_simple': 'aliyun',
    'aliyun': 'aliyun',
}


def normalize_cloud_account_provider(provider: str) -> str:
    return _PROVIDER_ALIASES.get(str(provider or '').strip(), str(provider or '').strip())


def cloud_account_label(account) -> str:
    if not account:
        return ''
    account_id = str(getattr(account, 'external_account_id', '') or account.id).strip()
    return f'{account.provider}+{account_id}+{account.name}'[:191]


def list_active_cloud_accounts(provider: str, region_code: str | None = None):
    """返回某云厂商可用账号列表。优先 ok，其次 unknown，最后 error。"""
    CloudAccountConfig = apps.get_model('core', 'CloudAccountConfig')
    normalized_provider = normalize_cloud_account_provider(provider)
    queryset = CloudAccountConfig.objects.filter(provider=normalized_provider, is_active=True)
    region = str(region_code or '').strip()
    if region:
        queryset = queryset.filter(Q(region_hint__isnull=True) | Q(region_hint='') | Q(region_hint=region))
    status_rank = {'ok': 0, 'unknown': 1, '': 1, 'error': 2, 'unsupported': 3}
    return sorted(list(queryset.order_by('id')), key=lambda item: (status_rank.get(item.status or '', 9), item.id))


def get_active_cloud_account(provider: str, region_code: str | None = None):
    """返回最优先的活跃云账号。优先选状态正常的，其次未检查的，最后才是异常的。"""
    candidates = list_active_cloud_accounts(provider, region_code)
    return candidates[0] if candidates else None


def _provider_values(provider: str) -> list[str]:
    normalized_provider = normalize_cloud_account_provider(provider)
    if normalized_provider == 'aws':
        return ['aws', 'aws_lightsail']
    if normalized_provider == 'aliyun':
        return ['aliyun', 'aliyun_simple']
    return [normalized_provider]


def list_cloud_accounts_by_server_load(provider: str, region_code: str | None = None):
    """按服务器统计里的实际服务器数量排序账号，服务器少的优先。"""
    candidates = list_active_cloud_accounts(provider, region_code)
    if not candidates:
        return []
    Server = apps.get_model('cloud', 'Server')
    provider_values = _provider_values(provider)
    labels = {item.id: cloud_account_label(item) for item in candidates}
    queryset = Server.objects.filter(provider__in=provider_values, account_label__in=list(labels.values()))
    region = str(region_code or '').strip()
    if region:
        queryset = queryset.filter(Q(region_code=region) | Q(region_code='') | Q(region_code__isnull=True))
    loads_by_label = {
        row['account_label']: row['count']
        for row in queryset.values('account_label').annotate(count=Count('id'))
    }
    return sorted(candidates, key=lambda item: (loads_by_label.get(labels[item.id], 0), item.id))


def choose_cloud_account_for_order(provider: str, region_code: str | None = None):
    """按服务器统计负载选择下单账号。"""
    candidates = list_cloud_accounts_by_server_load(provider, region_code)
    return candidates[0] if candidates else None
