from django.apps import apps
from django.db.models import Count, Q
import re


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


def cloud_account_label_variants(account) -> list[str]:
    if not account:
        return []
    label = cloud_account_label(account)
    return [label] if label else []


def list_cloud_account_labels(is_active: bool | None = None) -> list[str]:
    CloudAccountConfig = apps.get_model('core', 'CloudAccountConfig')
    queryset = CloudAccountConfig.objects.filter(
        provider__in=['aws', 'aliyun'],
    )
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    labels = []
    for account in queryset.order_by('id'):
        labels.extend(cloud_account_label_variants(account))
    return list(dict.fromkeys(labels))


def get_cloud_account_from_label(label: str, provider: str | None = None):
    text = str(label or '').strip()
    if not text:
        return None
    CloudAccountConfig = apps.get_model('core', 'CloudAccountConfig')
    normalized_provider = normalize_cloud_account_provider(provider or '')
    queryset = CloudAccountConfig.objects.filter(is_active=True)
    if normalized_provider:
        queryset = queryset.filter(provider=normalized_provider)
    for account in queryset.order_by('id'):
        if text == cloud_account_label(account):
            return account
    return None


def list_active_cloud_accounts(provider: str, region_code: str | None = None):
    """返回某云厂商可用账号列表。优先 ok，其次 unknown，最后 error。"""
    CloudAccountConfig = apps.get_model('core', 'CloudAccountConfig')
    normalized_provider = normalize_cloud_account_provider(provider)
    queryset = CloudAccountConfig.objects.filter(provider=normalized_provider, is_active=True)
    region = str(region_code or '').strip()
    if region:
        candidates = [item for item in queryset.order_by('id') if cloud_account_supports_region(item, region)]
    else:
        candidates = list(queryset.order_by('id'))
    status_rank = {'ok': 0, 'unknown': 1, '': 1, 'error': 2, 'unsupported': 3}
    return sorted(candidates, key=lambda item: (status_rank.get(item.status or '', 9), item.id))


def cloud_account_region_hints(account) -> set[str]:
    text = str(getattr(account, 'region_hint', '') or '').strip()
    if not text:
        return set()
    return {item.strip() for item in re.split(r'[\s,，;；|]+', text) if item.strip()}


def cloud_account_supports_region(account, region_code: str | None) -> bool:
    region = str(region_code or '').strip()
    if not region:
        return True
    hints = cloud_account_region_hints(account)
    return not hints or '*' in hints or region in hints


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
    CloudAsset = apps.get_model('cloud', 'CloudAsset')
    provider_values = _provider_values(provider)
    labels = {item.id: cloud_account_label_variants(item) for item in candidates}
    all_labels = list(dict.fromkeys(label for variants in labels.values() for label in variants))
    queryset = CloudAsset.objects.filter(kind='server', provider__in=provider_values, account_label__in=all_labels)
    region = str(region_code or '').strip()
    if region:
        queryset = queryset.filter(Q(region_code=region) | Q(region_code='') | Q(region_code__isnull=True))
    loads_by_label = {
        row['account_label']: row['count']
        for row in queryset.values('account_label').annotate(count=Count('id'))
    }
    CloudServerOrder = apps.get_model('cloud', 'CloudServerOrder')
    order_loads_by_label = {
        row['account_label']: row['count']
        for row in CloudServerOrder.objects.filter(
            provider__in=provider_values,
            account_label__in=all_labels,
            status__in=['paid', 'provisioning'],
        ).values('account_label').annotate(count=Count('id'))
    }
    return sorted(
        candidates,
        key=lambda item: (
            sum(loads_by_label.get(label, 0) + order_loads_by_label.get(label, 0) for label in labels[item.id]),
            item.id,
        ),
    )


def choose_cloud_account_for_order(provider: str, region_code: str | None = None):
    """按服务器统计负载选择下单账号。"""
    candidates = list_cloud_accounts_by_server_load(provider, region_code)
    return candidates[0] if candidates else None
