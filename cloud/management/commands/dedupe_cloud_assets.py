from django.core.management.base import BaseCommand
from django.db import transaction

from cloud.models import CloudAsset, CloudIpLog


class Command(BaseCommand):
    help = '按强身份字段去重云资产，保留最新一条并迁移 CloudIpLog.asset 外键'

    def handle(self, *args, **options):
        queryset = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).order_by('-updated_at', '-id')
        groups: dict[tuple[str, str], list[CloudAsset]] = {}
        for asset in queryset:
            key = None
            if str(asset.instance_id or '').strip():
                key = ('instance_id', asset.instance_id.strip())
            elif str(asset.provider_resource_id or '').strip():
                key = ('provider_resource_id', asset.provider_resource_id.strip())
            elif str(asset.public_ip or '').strip():
                key = ('public_ip', asset.public_ip.strip())
            if not key:
                continue
            groups.setdefault(key, []).append(asset)

        duplicate_groups = [(key, assets) for key, assets in groups.items() if len(assets) > 1]
        secondary_groups: dict[tuple[str, str], list[CloudAsset]] = {}
        for asset in CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).order_by('-updated_at', '-id'):
            if not asset.order_id:
                continue
            if not str(asset.public_ip or '').strip():
                continue
            secondary_groups.setdefault((f'order_ip:{asset.order_id}', asset.public_ip.strip()), []).append(asset)
        duplicate_groups.extend((key, assets) for key, assets in secondary_groups.items() if len(assets) > 1)
        if not duplicate_groups:
            self.stdout.write(self.style.SUCCESS('云资产去重：未发现重复记录。'))
            return

        merged_count = 0
        deleted_count = 0
        relinked_log_count = 0
        details = []
        deleted_ids = set()
        with transaction.atomic():
            for key, assets in duplicate_groups:
                alive_assets = [asset for asset in assets if asset.id not in deleted_ids]
                if len(alive_assets) <= 1:
                    continue
                keeper = alive_assets[0]
                duplicates = alive_assets[1:]
                duplicate_ids = [asset.id for asset in duplicates]
                relinked = CloudIpLog.objects.filter(asset_id__in=duplicate_ids).update(asset=keeper)
                CloudAsset.objects.filter(id__in=duplicate_ids).delete()
                deleted_ids.update(duplicate_ids)
                merged_count += 1
                deleted_count += len(duplicates)
                relinked_log_count += relinked
                details.append(f'{key[0]}={key[1]} 保留#{keeper.id} 删除{duplicate_ids} 迁日志{relinked}')

        self.stdout.write(self.style.SUCCESS(
            f'云资产去重完成：处理重复组 {merged_count} 组；删除重复资产 {deleted_count} 条；迁移日志引用 {relinked_log_count} 条。'
        ))
        for line in details[:50]:
            self.stdout.write(line)
