from django.core.management.base import BaseCommand

from cloud.models import CloudAsset


def _visible_asset_total():
    return (
        CloudAsset.objects
        .filter(kind=CloudAsset.KIND_SERVER, is_active=True)
        .exclude(status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED])
        .count()
    )


class Command(BaseCommand):
    help = '历史兼容命令：cloud_server 已拆除，当前仅校验统一云资产表'

    def handle(self, *args, **options):
        total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        visible_total = _visible_asset_total()
        self.stdout.write(self.style.SUCCESS(
            f'cloud_server 已拆除，无需从服务器表回填；cloud_asset server 总数={total}，可见数={visible_total}。'
        ))
