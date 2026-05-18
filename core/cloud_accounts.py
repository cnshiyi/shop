from core.models import CloudAccountConfig


def get_active_cloud_account(provider: str, region_hint: str | None = None):
    queryset = CloudAccountConfig.objects.filter(
        provider=provider,
        is_active=True,
    ).order_by('id')
    ok_queryset = queryset.filter(status=CloudAccountConfig.STATUS_OK)
    usable_queryset = ok_queryset if ok_queryset.exists() else queryset.exclude(
        status=CloudAccountConfig.STATUS_ERROR,
    )
    if region_hint:
        region_account = usable_queryset.filter(region_hint=region_hint).first()
        if region_account:
            return region_account
    return usable_queryset.first()
