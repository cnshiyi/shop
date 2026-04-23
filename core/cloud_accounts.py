from core.models import CloudAccountConfig


def get_active_cloud_account(provider: str):
    return CloudAccountConfig.objects.filter(provider=provider, is_active=True).order_by('id').first()
