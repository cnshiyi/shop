from django.core.management.base import BaseCommand
from django.db.models import Count

from cloud.models import Server


class Command(BaseCommand):
    help = '清理 servers 表重复记录，保留每组最新一条有效记录'

    def handle(self, *args, **options):
        duplicate_groups = (
            Server.objects.exclude(instance_id__isnull=True)
            .exclude(instance_id='')
            .values('provider', 'account_label', 'region_code', 'instance_id', 'public_ip', 'previous_public_ip')
            .annotate(total=Count('id'))
            .filter(total__gt=1)
        )
        removed = 0
        for group in duplicate_groups:
            queryset = Server.objects.filter(
                provider=group['provider'],
                account_label=group['account_label'],
                region_code=group['region_code'],
                instance_id=group['instance_id'],
                public_ip=group['public_ip'],
                previous_public_ip=group['previous_public_ip'],
            ).order_by('-is_active', '-updated_at', '-id')
            keep = queryset.first()
            for item in queryset.exclude(pk=keep.pk):
                item.delete()
                removed += 1
                self.stdout.write(self.style.WARNING(f"已删除重复服务器 #{item.pk} {item.provider}:{item.account_label or '-'}:{item.instance_id}"))
        self.stdout.write(self.style.SUCCESS(f'完成，共删除 {removed} 条重复服务器记录。'))
