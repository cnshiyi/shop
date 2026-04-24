from asgiref.sync import sync_to_async


def build_cloud_server_name(*args, **kwargs):
    from cloud.services import build_cloud_server_name as impl
    return impl(*args, **kwargs)


def ensure_unique_cloud_server_name(*args, **kwargs):
    from cloud.services import ensure_unique_cloud_server_name as impl
    return impl(*args, **kwargs)


@sync_to_async
def ensure_cloud_server_pricing(*args, **kwargs):
    from cloud.services import ensure_cloud_server_pricing as impl
    return impl.__wrapped__(*args, **kwargs)


@sync_to_async
def ensure_cloud_server_plans(*args, **kwargs):
    from cloud.services import ensure_cloud_server_plans as impl
    return impl.__wrapped__(*args, **kwargs)


async def list_custom_regions(*args, **kwargs):
    from cloud.services import list_custom_regions as impl
    return await impl(*args, **kwargs)


async def list_region_plans(*args, **kwargs):
    from cloud.services import list_region_plans as impl
    return await impl(*args, **kwargs)


async def refresh_custom_plan_cache(*args, **kwargs):
    from cloud.services import refresh_custom_plan_cache as impl
    return await impl(*args, **kwargs)


@sync_to_async
def get_cloud_plan(*args, **kwargs):
    from cloud.services import get_cloud_plan as impl
    return impl.__wrapped__(*args, **kwargs)


@sync_to_async
def create_cloud_server_order(*args, **kwargs):
    from cloud.services import create_cloud_server_order as impl
    return impl.__wrapped__(*args, **kwargs)


@sync_to_async
def buy_cloud_server_with_balance(*args, **kwargs):
    from cloud.services import buy_cloud_server_with_balance as impl
    return impl.__wrapped__(*args, **kwargs)


@sync_to_async
def pay_cloud_server_order_with_balance(*args, **kwargs):
    from cloud.services import pay_cloud_server_order_with_balance as impl
    return impl.__wrapped__(*args, **kwargs)


@sync_to_async
def set_cloud_server_port(*args, **kwargs):
    from cloud.services import set_cloud_server_port as impl
    return impl.__wrapped__(*args, **kwargs)


__all__ = [
    'build_cloud_server_name',
    'buy_cloud_server_with_balance',
    'create_cloud_server_order',
    'ensure_cloud_server_plans',
    'ensure_cloud_server_pricing',
    'ensure_unique_cloud_server_name',
    'get_cloud_plan',
    'list_custom_regions',
    'list_region_plans',
    'pay_cloud_server_order_with_balance',
    'refresh_custom_plan_cache',
    'set_cloud_server_port',
]
