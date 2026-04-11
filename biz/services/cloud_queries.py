from asgiref.sync import sync_to_async

from biz.models import CloudServerOrder


@sync_to_async
def list_user_cloud_servers(user_id: int):
    return list(CloudServerOrder.objects.filter(user_id=user_id).order_by('-created_at')[:20])


@sync_to_async
def get_user_cloud_server(order_id: int, user_id: int):
    return CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
