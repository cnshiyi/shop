from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from cloud.aliyun_simple import _build_client, _region_endpoint, _runtime_options
from cloud.management.commands.sync_aws_assets import _lightsail_client
from cloud.models import CloudAsset


class Command(BaseCommand):
    help = '打印数据库中的代理 IP 列表，并到云厂商侧核对实例/IP 是否存在'

    def add_arguments(self, parser):
        parser.add_argument('--provider', default='', help='可选：aws_lightsail / aliyun_simple')
        parser.add_argument('--limit', type=int, default=200)

    def _load_aws_inventory(self, region: str):
        client = _lightsail_client(region)
        instances = {}
        next_page_token = None
        while True:
            kwargs = {}
            if next_page_token:
                kwargs['pageToken'] = next_page_token
            response = client.get_instances(**kwargs)
            for item in response.get('instances') or []:
                instances[item.get('name') or ''] = item
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break

        static_ips = {}
        static_ip_next_page_token = None
        while True:
            kwargs = {}
            if static_ip_next_page_token:
                kwargs['pageToken'] = static_ip_next_page_token
            response = client.get_static_ips(**kwargs)
            for item in response.get('staticIps') or []:
                static_ips[item.get('ipAddress') or item.get('name') or ''] = item
            static_ip_next_page_token = response.get('nextPageToken')
            if not static_ip_next_page_token:
                break
        return {'instances': instances, 'static_ips': static_ips}

    def _load_aliyun_inventory(self, region: str):
        client = _build_client(_region_endpoint(region))
        if not client:
            raise CommandError(f'阿里云地区 {region} 未配置可用凭据。')
        from alibabacloud_swas_open20200601 import models as swas_models

        response = client.list_instances_with_options(
            swas_models.ListInstancesRequest(region_id=region, page_size=100),
            _runtime_options(),
        )
        instances = {}
        for item in response.body.to_map().get('Instances', []):
            instances[item.get('InstanceId') or ''] = item
        return {'instances': instances}

    def handle(self, *args, **options):
        provider = (options.get('provider') or '').strip()
        limit = max(1, int(options.get('limit') or 200))
        queryset = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).order_by('-updated_at', '-id')
        if provider:
            queryset = queryset.filter(provider=provider)
        assets = list(queryset[:limit])
        if not assets:
            self.stdout.write('没有可审计的代理资产。')
            return

        grouped = defaultdict(list)
        for asset in assets:
            grouped[(asset.provider or '', asset.region_code or '')].append(asset)

        self.stdout.write(self.style.SUCCESS(f'开始审计：数据库代理记录 {len(assets)} 条。'))
        for (asset_provider, region_code), rows in grouped.items():
            self.stdout.write(self.style.WARNING(f'--- 分组 provider={asset_provider or "-"} region={region_code or "-"} count={len(rows)} ---'))
            inventory = None
            try:
                if asset_provider == 'aws_lightsail':
                    inventory = self._load_aws_inventory(region_code or 'ap-southeast-1')
                elif asset_provider == 'aliyun_simple':
                    inventory = self._load_aliyun_inventory(region_code or 'cn-hongkong')
                else:
                    self.stdout.write(f'跳过未知 provider：{asset_provider or "-"}')
                    continue
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'加载云侧清单失败 provider={asset_provider} region={region_code}: {exc}'))
                continue

            for asset in rows:
                exists = False
                exists_reason = '未命中'
                if asset_provider == 'aws_lightsail':
                    instance = inventory['instances'].get(asset.instance_id or '') if asset.instance_id else None
                    static_ip = inventory['static_ips'].get(asset.public_ip or '') if asset.public_ip else None
                    if instance:
                        exists = True
                        exists_reason = f'实例存在 state={((instance.get("state") or {}).get("name") or "-")}'
                    elif static_ip:
                        exists = True
                        exists_reason = f'固定IP存在 attachedTo={static_ip.get("attachedTo") or "未附加"}'
                elif asset_provider == 'aliyun_simple':
                    instance = inventory['instances'].get(asset.instance_id or '') if asset.instance_id else None
                    if instance:
                        exists = True
                        exists_reason = f'实例存在 status={instance.get("Status") or "-"}'

                self.stdout.write(
                    f'资产ID={asset.id} source={asset.source} name={asset.asset_name or "-"} '
                    f'instance_id={asset.instance_id or "-"} public_ip={asset.public_ip or "-"} '
                    f'db_status={asset.status or "-"} provider_status={asset.provider_status or "-"} '
                    f'cloud_exists={"是" if exists else "否"} detail={exists_reason}'
                )
