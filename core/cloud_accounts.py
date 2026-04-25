from django.apps import apps


def get_active_cloud_account(provider: str):
    """返回最优先的活跃云账号。优先选状态正常的，其次未检查的，最后才是异常的。"""
    CloudAccountConfig = apps.get_model('core', 'CloudAccountConfig')
    candidates = list(
        CloudAccountConfig.objects.filter(provider=provider, is_active=True).order_by('id')
    )
    if not candidates:
        return None
    # 优先 ok > unknown > error
    for preferred_status in ('ok', 'unknown', ''):
        for c in candidates:
            if (c.status or '') == preferred_status:
                return c
    # 全是 error，返回第一个
    return candidates[0]
