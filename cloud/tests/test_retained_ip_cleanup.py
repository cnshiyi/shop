from .common import *


class CloudServerRetainedIpCleanupMixin:
    def test_sync_aws_assets_revives_deleted_order_when_instance_exists(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-revive-deleted-order',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        now = timezone.now()
        order = CloudServerOrder.objects.create(
            order_no='AWS-SYNC-REVIVE-DELETED-1',
            user=self.user,
            plan=self.plan,
            cloud_account=account,
            account_label=account_label,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='deleted',
            public_ip=None,
            previous_public_ip='10.9.0.8',
            instance_id='i-revive-deleted-1',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-revive-deleted-1',
            server_name='i-revive-deleted-1',
            service_started_at=now - timezone.timedelta(days=20),
            service_expires_at=now - timezone.timedelta(days=5),
            suspend_at=now - timezone.timedelta(days=2),
            delete_at=now - timezone.timedelta(days=1),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='i-revive-deleted-1',
            public_ip='10.9.0.8',
            previous_public_ip='10.9.0.8',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )

        class FakeLightsailClient:
            def get_static_ips(self, **kwargs):
                return {'staticIps': [], 'nextPageToken': None}

            def get_instances(self, **kwargs):
                return {
                    'instances': [{
                        'name': 'i-revive-deleted-1',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-revive-deleted-1',
                        'state': {'name': 'running'},
                        'location': {'regionName': '新加坡'},
                        'publicIpAddress': '10.9.0.8',
                        'bundleId': 'micro_1_0',
                        'blueprintId': 'debian_12',
                    }],
                    'nextPageToken': None,
                }

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.status, 'deleting')
        self.assertEqual(order.public_ip, '10.9.0.8')
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.order_id, order.id)
        self.assertNotIn('已标记删除', asset.note or '')
        due = async_to_sync(_get_due_orders)()
        self.assertIn(order.id, [item.id for item in due['delete']])

    def test_proxy_list_hides_deleted_order_retained_ip(self):
        order = CloudServerOrder.objects.create(
            order_no='DELETED-LIST-HIDDEN-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='20.20.20.30',
            previous_public_ip='20.20.20.30',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=5),
            instance_id='',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-hidden-retained',
            public_ip='20.20.20.30',
            previous_public_ip='20.20.20.30',
            actual_expires_at=order.ip_recycle_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='固定IP保留中-实例已删除',
            is_active=False,
        )

        items = async_to_sync(list_user_cloud_servers)(self.user.id)
        from cloud.services import get_user_proxy_asset_detail
        detail = async_to_sync(get_user_proxy_asset_detail)(asset.id, self.user.id, 'asset')

        self.assertFalse(any(getattr(item, 'asset_id', None) == asset.id for item in items))
        self.assertIsNone(detail)

    def test_cloud_sync_resolvers_ignore_deleted_ip_records(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='deleted-sync-asset',
            instance_id='deleted-sync-instance',
            provider_resource_id='deleted-sync-arn',
            public_ip=None,
            previous_public_ip='20.20.20.31',
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='deleted-sync-server',
            instance_id='deleted-sync-instance',
            provider_resource_id='deleted-sync-arn',
            public_ip=None,
            previous_public_ip='20.20.20.31',
            status=Server.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
        )
        from cloud.management.commands.sync_aliyun_assets import _resolve_asset as resolve_aliyun_asset
        from cloud.management.commands.sync_aliyun_assets import _resolve_server as resolve_aliyun_server
        from cloud.management.commands.sync_aws_assets import _resolve_asset as resolve_aws_asset
        from cloud.management.commands.sync_aws_assets import _resolve_server as resolve_aws_server

        self.assertIsNone(resolve_aws_asset(asset.instance_id, asset.provider_resource_id, asset.previous_public_ip, None))
        self.assertIsNone(resolve_aws_server(server.instance_id, server.provider_resource_id, server.previous_public_ip, None))
        self.assertIsNone(resolve_aliyun_asset(asset.instance_id, asset.previous_public_ip))
        self.assertIsNone(resolve_aliyun_server(server.instance_id, server.previous_public_ip))

    def test_cloud_sync_resolvers_keep_ip_primary_when_instance_changes(self):
        aws_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-ip-primary',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        aws_label = cloud_account_label(aws_account)
        aws_ip_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=aws_account,
            account_label=aws_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='aws-old-instance-for-same-ip',
            instance_id='aws-old-instance-for-same-ip',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/aws-old-instance-for-same-ip',
            public_ip='20.20.20.40',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        aws_direct_conflict = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=aws_account,
            account_label=aws_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='aws-new-instance-conflict',
            instance_id='aws-new-instance-conflict',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/aws-new-instance-conflict',
            public_ip='20.20.20.41',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        aws_ip_server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=aws_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='aws-old-instance-for-same-ip',
            instance_id='aws-old-instance-for-same-ip',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/aws-old-instance-for-same-ip',
            public_ip='20.20.20.40',
            status=Server.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=aws_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='aws-new-instance-conflict',
            instance_id='aws-new-instance-conflict',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/aws-new-instance-conflict',
            public_ip='20.20.20.41',
            status=Server.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        from cloud.management.commands.sync_aws_assets import _resolve_asset as resolve_aws_asset
        from cloud.management.commands.sync_aws_assets import _resolve_server as resolve_aws_server

        self.assertEqual(resolve_aws_asset(aws_direct_conflict.instance_id, aws_direct_conflict.provider_resource_id, '20.20.20.40', None, aws_account).id, aws_ip_asset.id)
        self.assertEqual(resolve_aws_server('aws-new-instance-conflict', aws_direct_conflict.provider_resource_id, '20.20.20.40', None, aws_account).id, aws_ip_server.id)

        aliyun_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-ip-primary',
            external_account_id='5698076839482440',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='cn-hongkong',
            is_active=True,
        )
        aliyun_label = cloud_account_label(aliyun_account)
        aliyun_ip_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aliyun_simple',
            cloud_account=aliyun_account,
            account_label=aliyun_label,
            region_code='cn-hongkong',
            region_name='香港',
            asset_name='aliyun-old-instance-for-same-ip',
            instance_id='aliyun-old-instance-for-same-ip',
            provider_resource_id='aliyun-old-instance-for-same-ip',
            public_ip='20.20.20.42',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        aliyun_direct_conflict = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aliyun_simple',
            cloud_account=aliyun_account,
            account_label=aliyun_label,
            region_code='cn-hongkong',
            region_name='香港',
            asset_name='aliyun-new-instance-conflict',
            instance_id='aliyun-new-instance-conflict',
            provider_resource_id='aliyun-new-instance-conflict',
            public_ip='20.20.20.43',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        aliyun_ip_server = Server.objects.create(
            source=Server.SOURCE_ALIYUN,
            user=self.user,
            provider='aliyun_simple',
            account_label=aliyun_label,
            region_code='cn-hongkong',
            region_name='香港',
            server_name='aliyun-old-instance-for-same-ip',
            instance_id='aliyun-old-instance-for-same-ip',
            provider_resource_id='aliyun-old-instance-for-same-ip',
            public_ip='20.20.20.42',
            status=Server.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        Server.objects.create(
            source=Server.SOURCE_ALIYUN,
            user=self.user,
            provider='aliyun_simple',
            account_label=aliyun_label,
            region_code='cn-hongkong',
            region_name='香港',
            server_name='aliyun-new-instance-conflict',
            instance_id='aliyun-new-instance-conflict',
            provider_resource_id='aliyun-new-instance-conflict',
            public_ip='20.20.20.43',
            status=Server.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        from cloud.management.commands.sync_aliyun_assets import _resolve_asset as resolve_aliyun_asset
        from cloud.management.commands.sync_aliyun_assets import _resolve_server as resolve_aliyun_server

        self.assertEqual(resolve_aliyun_asset(aliyun_direct_conflict.instance_id, '20.20.20.42', aliyun_account).id, aliyun_ip_asset.id)
        self.assertEqual(resolve_aliyun_server('aliyun-new-instance-conflict', '20.20.20.42', aliyun_account).id, aliyun_ip_server.id)

    def test_cloud_sync_resolvers_prefer_current_ip_over_stale_previous_ip(self):
        current_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='current-ip-owner',
            instance_id='current-ip-owner',
            public_ip='20.20.20.50',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        stale_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='stale-previous-ip-owner',
            instance_id='stale-previous-ip-owner',
            public_ip='20.20.20.51',
            previous_public_ip='20.20.20.50',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        current_server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='current-ip-owner',
            instance_id='current-ip-owner',
            public_ip='20.20.20.50',
            status=Server.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='stale-previous-ip-owner',
            instance_id='stale-previous-ip-owner',
            public_ip='20.20.20.51',
            previous_public_ip='20.20.20.50',
            status=Server.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        from cloud.management.commands.sync_aws_assets import _resolve_asset as resolve_aws_asset
        from cloud.management.commands.sync_aws_assets import _resolve_server as resolve_aws_server

        self.assertEqual(resolve_aws_asset('', '', '20.20.20.50', None).id, current_asset.id)
        self.assertEqual(resolve_aws_server('', '', '20.20.20.50', None).id, current_server.id)
        self.assertNotEqual(stale_asset.id, current_asset.id)

    def test_delete_server_marks_instance_deleted_but_retains_static_ip(self):
        now = timezone.now()
        recycle_at = now + timezone.timedelta(days=7)
        order = CloudServerOrder.objects.create(
            order_no='DELETE-RETAIN-STATIC-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleting',
            public_ip='20.20.20.32',
            previous_public_ip='20.20.20.32',
            static_ip_name='StaticIp-delete-retain',
            instance_id='delete-retain-instance',
            provider_resource_id='delete-retain-arn',
            ip_recycle_at=recycle_at,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='delete-retain-instance',
            instance_id='delete-retain-instance',
            provider_resource_id='delete-retain-arn',
            public_ip='20.20.20.32',
            previous_public_ip='20.20.20.32',
            actual_expires_at=recycle_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='delete-retain-instance',
            instance_id='delete-retain-instance',
            provider_resource_id='delete-retain-arn',
            public_ip='20.20.20.32',
            previous_public_ip='20.20.20.32',
            expires_at=recycle_at,
            status=Server.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )

        async_to_sync(_mark_deleted)(order.id, '实例已删除，固定 IP 保留。')

        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertEqual(order.public_ip, '20.20.20.32')
        self.assertEqual(order.previous_public_ip, '20.20.20.32')
        self.assertEqual(order.static_ip_name, 'StaticIp-delete-retain')
        self.assertGreater(order.ip_recycle_at, recycle_at)
        self.assertGreater(order.ip_recycle_at, now + timezone.timedelta(days=14))
        self.assertEqual(asset.actual_expires_at, order.ip_recycle_at)
        self.assertEqual(server.expires_at, order.ip_recycle_at)
        self.assertIn('固定IP名=StaticIp-delete-retain', order.provision_note)
        self.assertIn('未附加 IP 计划回收=', order.provision_note)
        self.assertEqual(order.instance_id, '')
        self.assertEqual(asset.public_ip, '20.20.20.32')
        self.assertIsNone(asset.instance_id)
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(asset.provider_status, '固定IP保留中-实例已删除')
        self.assertEqual(server.public_ip, '20.20.20.32')
        self.assertIsNone(server.instance_id)
        self.assertEqual(server.status, Server.STATUS_DELETED)
        self.assertEqual(server.provider_status, '固定IP保留中-实例已删除')
        self.assertFalse(any(getattr(item, 'asset_id', None) == asset.id for item in async_to_sync(list_user_cloud_servers)(self.user.id)))
        admin = get_user_model().objects.create_user(username='admin_retained_ip_asset_filter', password='x', is_staff=True)
        request = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'risk_status': 'unattached_ip'})
        request.user = admin
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']
        self.assertNotIn(asset.id, [item['id'] for item in payload['items']])

    def test_unattached_static_ip_is_not_auto_renewed(self):
        expires_at = timezone.now() + timezone.timedelta(hours=8)
        order = CloudServerOrder.objects.create(
            order_no='UNATTACHED-NO-AUTO-RENEW-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            public_ip='20.20.20.34',
            previous_public_ip='20.20.20.34',
            static_ip_name='StaticIp-unattached-no-auto-renew',
            service_expires_at=expires_at,
            auto_renew_enabled=True,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-unattached-no-auto-renew',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-unattached-no-auto-renew',
            public_ip='20.20.20.34',
            previous_public_ip='20.20.20.34',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='未附加固定IP',
            is_active=False,
            note='未附加固定IP',
        )

        due = async_to_sync(_get_due_orders)()
        auto_renew_ids = {item.id for item in due['auto_renew']}
        auto_renew_notice_ids = {item.id for item in due['auto_renew_notice']}
        auto_renew_items = async_to_sync(list_all_auto_renew_cloud_servers)()

        self.assertNotIn(order.id, auto_renew_ids)
        self.assertNotIn(order.id, auto_renew_notice_ids)
        self.assertFalse(any(getattr(item, 'asset_id', None) == asset.id for item in auto_renew_items))

    def test_deleted_retained_static_ip_remains_query_renewable(self):
        recycle_at = timezone.now() + timezone.timedelta(days=7)
        order = CloudServerOrder.objects.create(
            order_no='DELETE-RETAIN-QUERY-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleting',
            public_ip='20.20.20.33',
            previous_public_ip='20.20.20.33',
            static_ip_name='StaticIp-delete-retain-query',
            instance_id='delete-retain-query-instance',
            provider_resource_id='delete-retain-query-arn',
            ip_recycle_at=recycle_at,
            mtproxy_secret='eeeeeeeeeeeeeeee',
            mtproxy_port=9528,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='delete-retain-query-instance',
            instance_id='delete-retain-query-instance',
            provider_resource_id='delete-retain-query-arn',
            public_ip='20.20.20.33',
            previous_public_ip='20.20.20.33',
            actual_expires_at=recycle_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )

        async_to_sync(_mark_deleted)(order.id, '实例已删除，固定 IP 保留。')

        queried = async_to_sync(get_cloud_server_by_ip_for_user)('20.20.20.33', self.user.id)
        self.assertIsNotNone(queried)
        self.assertEqual(queried.id, order.id)
        retained_order, plans, err = async_to_sync(list_retained_ip_renewal_plans)(order.id, self.user.id)
        self.assertIsNone(err)
        self.assertIsNotNone(retained_order)
        self.assertTrue(plans)

    def test_provision_expected_ip_failure_schedules_cleanup(self):
        order = CloudServerOrder.objects.create(
            order_no='PROVISION-IP-MISSING-CLEANUP',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=self._aws_test_account(),
            account_label=cloud_account_label(self._aws_test_account()),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='paid',
            paid_at=timezone.now(),
            public_ip='20.20.20.35',
            previous_public_ip='20.20.20.35',
            static_ip_name='StaticIp-provision-ip-missing-cleanup',
            mtproxy_secret='eeeeeeeeeeeeeeee',
            mtproxy_port=9528,
        )
        result = SimpleNamespace(
            ok=True,
            instance_id='provision-ip-missing-cleanup-instance',
            public_ip='54.54.54.54',
            login_user='admin',
            login_password='pw',
            note='AWS 实例已创建',
            static_ip_name='StaticIp-provision-ip-missing-cleanup',
            private_key_path='',
        )

        with patch('cloud.provisioning.create_aws_instance', new=AsyncMock(return_value=result)), \
            patch('cloud.provisioning.public_ip_exists', new=AsyncMock(return_value=(False, '原固定 IP 已不在 AWS 账号中'))):
            saved = async_to_sync(provision_cloud_server)(order.id)

        self.assertEqual(saved.status, 'failed')
        self.assertIsNotNone(saved.delete_at)
        self.assertIn('创建流程未完成', saved.provision_note)
        self.assertIn('原固定 IP 已不在 AWS 账号中', saved.provision_note)

    def test_retained_ip_postcheck_reuses_completed_recovery_order(self):
        recycle_at = timezone.now() + timezone.timedelta(days=7)
        source = CloudServerOrder.objects.create(
            order_no='RETAINED-POSTCHECK-SOURCE',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='20.20.20.34',
            previous_public_ip='20.20.20.34',
            static_ip_name='StaticIp-retained-postcheck-source',
            instance_id='',
            ip_recycle_at=recycle_at,
            mtproxy_secret='eeeeeeeeeeeeeeee',
            mtproxy_port=9528,
        )
        completed_recovery = CloudServerOrder.objects.create(
            order_no='RETAINED-POSTCHECK-RECOVERY',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='20.20.20.34',
            previous_public_ip='20.20.20.34',
            static_ip_name='StaticIp-retained-postcheck-source',
            instance_id='retained-postcheck-recovered-instance',
            replacement_for=source,
        )

        result, err = async_to_sync(run_cloud_server_renewal_postcheck)(source.id)

        self.assertEqual(result.id, completed_recovery.id)
        self.assertEqual(err, '固定 IP 保留期续费，已进入自动恢复流程。')
        self.assertEqual(CloudServerOrder.objects.filter(replacement_for=source).count(), 1)

    def test_lifecycle_tick_releases_retained_static_ip_after_recycle_due(self):
        SiteConfig.set('cloud_ip_delete_enabled', '1')
        recycle_due_at = timezone.now() - timezone.timedelta(days=2)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RECYCLE-DUE',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='20.20.20.20',
            previous_public_ip='20.20.20.20',
            static_ip_name='StaticIp-retained-due',
            service_expires_at=timezone.now() - timezone.timedelta(days=20),
            delete_at=timezone.now() - timezone.timedelta(days=17),
            ip_recycle_at=recycle_due_at,
            instance_id='',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-retained-due',
            public_ip='20.20.20.20',
            previous_public_ip='20.20.20.20',
            actual_expires_at=recycle_due_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='固定IP保留中-实例已删除',
            note='固定IP保留中',
            is_active=False,
        )

        released = []

        class FakeLightsailClient:
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {'operations': [{'id': 'op-retained-release'}]}

        with patch('cloud.lifecycle._aws_client', return_value=FakeLightsailClient()), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=True):
            async_to_sync(lifecycle_tick)()

        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(released, ['StaticIp-retained-due'])
        self.assertIsNone(order.ip_recycle_at)
        self.assertEqual(order.public_ip, '')
        self.assertEqual(order.previous_public_ip, '20.20.20.20')
        self.assertEqual(order.static_ip_name, '')
        self.assertIsNone(asset.public_ip)
        self.assertEqual(asset.previous_public_ip, '20.20.20.20')
        self.assertIn('AWS 固定 IP 已真实释放', order.provision_note or '')

    def test_lifecycle_tick_releases_retained_static_ip_when_asset_already_deleted(self):
        SiteConfig.set('cloud_ip_delete_enabled', '1')
        recycle_due_at = timezone.now() - timezone.timedelta(days=2)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RECYCLE-DELETED-ASSET',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='20.20.20.21',
            previous_public_ip='20.20.20.21',
            static_ip_name='StaticIp-retained-deleted-asset',
            service_expires_at=timezone.now() - timezone.timedelta(days=20),
            delete_at=timezone.now() - timezone.timedelta(days=17),
            ip_recycle_at=recycle_due_at,
            instance_id='',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-retained-deleted-asset',
            public_ip='20.20.20.21',
            previous_public_ip='20.20.20.21',
            actual_expires_at=recycle_due_at,
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            note='固定IP保留中',
            is_active=False,
        )

        released = []

        class FakeLightsailClient:
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {'operations': [{'id': 'op-retained-release-deleted-asset'}]}

        with patch('cloud.lifecycle._aws_client', return_value=FakeLightsailClient()), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=True):
            async_to_sync(lifecycle_tick)()

        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(released, ['StaticIp-retained-deleted-asset'])
        self.assertIsNone(order.ip_recycle_at)
        self.assertEqual(order.public_ip, '')
        self.assertEqual(order.previous_public_ip, '20.20.20.21')
        self.assertEqual(order.static_ip_name, '')
        self.assertIsNone(asset.public_ip)
        self.assertEqual(asset.previous_public_ip, '20.20.20.21')
        self.assertIn('AWS 固定 IP 已真实释放', order.provision_note or '')

    def test_lifecycle_tick_recycle_respects_ip_delete_time_window(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RECYCLE-WINDOW-BLOCKED',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='20.20.20.22',
            previous_public_ip='20.20.20.22',
            static_ip_name='StaticIp-retained-window-blocked',
            ip_recycle_at=timezone.now() - timezone.timedelta(minutes=1),
            instance_id='',
        )
        due_order = CloudServerOrder.objects.get(id=order.id)
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [due_order],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=False) as safe_time, \
            patch('cloud.lifecycle._release_order_static_ip', new_callable=AsyncMock) as release_mock:
            async_to_sync(lifecycle_tick)()

        safe_time.assert_called_once()
        release_mock.assert_not_awaited()
        order.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertIsNotNone(order.ip_recycle_at)

    def test_release_order_static_ip_uses_static_ip_asset_name_when_order_name_missing(self):
        from cloud.lifecycle import _release_order_static_ip_sync

        account = self._aws_test_account()
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RECYCLE-ASSET-NAME-FALLBACK',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='20.20.20.23',
            previous_public_ip='20.20.20.23',
            static_ip_name='',
            ip_recycle_at=timezone.now() - timezone.timedelta(minutes=1),
            instance_id='',
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-retained-asset-fallback',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-retained-asset-fallback',
            public_ip='20.20.20.23',
            previous_public_ip='20.20.20.23',
            actual_expires_at=order.ip_recycle_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='固定IP仍存在但未附加',
            is_active=False,
        )
        released = []

        class FakeLightsailClient:
            def get_static_ips(self, **kwargs):
                return {'staticIps': []}

            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {'operations': [{'id': 'op-retained-asset-fallback'}]}

        with patch('cloud.lifecycle._aws_client', return_value=FakeLightsailClient()):
            ok, note = _release_order_static_ip_sync(order)

        self.assertTrue(ok)
        self.assertEqual(released, ['StaticIp-retained-asset-fallback'])
        self.assertIn('AWS 固定 IP 已真实释放', note)

    def test_lifecycle_tick_releases_overdue_unattached_static_ip(self):
        SiteConfig.set('cloud_ip_delete_enabled', '1')
        due_at = timezone.now() - timezone.timedelta(days=4)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-unattached-due',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-unattached-due',
            public_ip='21.21.21.21',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='unattached-static-ip-shadow',
            public_ip='21.21.21.21',
            expires_at=due_at,
            status=Server.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )

        released = []

        class FakeLightsailClient:
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {'operations': [{'id': 'op-unattached-release'}]}

        with patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._aws_client', return_value=FakeLightsailClient()), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=True):
            async_to_sync(lifecycle_tick)()

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(released, ['StaticIp-unattached-due'])
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(asset.provider_status, '未附加固定IP-已到期删除')
        self.assertIsNone(asset.public_ip)
        self.assertEqual(asset.previous_public_ip, '21.21.21.21')
        self.assertEqual(server.status, Server.STATUS_DELETED)
        self.assertEqual(server.provider_status, '未附加固定IP-已到期删除')
        self.assertIsNone(server.public_ip)
        self.assertEqual(server.previous_public_ip, '21.21.21.21')

    def test_lifecycle_tick_unattached_ip_uses_ip_delete_time_window(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-unattached-window-blocked',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-unattached-window-blocked',
            public_ip='21.21.21.24',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[asset]), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=False) as safe_time, \
            patch('cloud.lifecycle._release_unattached_static_ip', new_callable=AsyncMock) as release_mock:
            async_to_sync(lifecycle_tick)()

        safe_time.assert_called_once()
        release_mock.assert_not_awaited()
        asset.refresh_from_db()
        self.assertNotEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(asset.public_ip, '21.21.21.24')

    def test_lifecycle_tick_rechecks_orphan_asset_delete_time_before_cloud_delete(self):
        SiteConfig.set('cloud_suspend_after_days', '3')
        SiteConfig.set('cloud_suspend_time', '17:00')
        SiteConfig.set('cloud_delete_after_days', '3')
        SiteConfig.set('cloud_delete_time', '18:00')
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='orphan-recheck-future-delete',
            instance_id='orphan-recheck-future-delete',
            public_ip='21.21.21.22',
            actual_expires_at=timezone.now() - timezone.timedelta(days=10),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        due_asset = CloudAsset.objects.get(id=asset.id)
        CloudAsset.objects.filter(id=asset.id).update(actual_expires_at=timezone.now() - timezone.timedelta(days=1))
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[due_asset]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_delete_safe_time', return_value=True), \
            patch('cloud.lifecycle._delete_orphan_asset_instance', new_callable=AsyncMock) as delete_mock:
            async_to_sync(lifecycle_tick)()

        delete_mock.assert_not_awaited()
        asset.refresh_from_db()
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)

    def test_lifecycle_tick_rechecks_unattached_ip_delete_time_before_release(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-unattached-recheck-future',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-unattached-recheck-future',
            public_ip='21.21.21.23',
            actual_expires_at=timezone.now() - timezone.timedelta(minutes=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        due_asset = CloudAsset.objects.get(id=asset.id)
        CloudAsset.objects.filter(id=asset.id).update(actual_expires_at=timezone.now() + timezone.timedelta(days=1))
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[due_asset]), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=True), \
            patch('cloud.lifecycle._release_unattached_static_ip', new_callable=AsyncMock) as release_mock:
            async_to_sync(lifecycle_tick)()

        release_mock.assert_not_awaited()
        asset.refresh_from_db()
        self.assertNotEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(asset.public_ip, '21.21.21.23')

    def test_aws_sync_release_static_ip_respects_shutdown_disabled_account(self):
        from cloud.management.commands.sync_aws_assets import _release_static_ip_if_due

        SiteConfig.set('cloud_ip_delete_enabled', '1')
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-sync-release-disabled',
            region_hint=self.plan.region_code,
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
            shutdown_enabled=False,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            region_code=self.plan.region_code,
            asset_name='StaticIp-sync-disabled',
            public_ip='21.21.21.88',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        released = []

        class FakeClient:
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {}

        class FakeStyle:
            def WARNING(self, text):
                return text

        class FakeStdout:
            style = FakeStyle()

            def write(self, text):
                return None

        ok = _release_static_ip_if_due(FakeClient(), self.plan.region_code, asset, 'StaticIp-sync-disabled', '', '21.21.21.88', FakeStdout())

        asset.refresh_from_db()
        self.assertFalse(ok)
        self.assertEqual(released, [])
        self.assertEqual(asset.status, CloudAsset.STATUS_UNKNOWN)
        self.assertEqual(asset.provider_status, '未附加固定IP-关机计划关闭')

    def test_aws_sync_release_static_ip_respects_global_ip_delete_switch(self):
        from cloud.management.commands.sync_aws_assets import _release_static_ip_if_due

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='StaticIp-sync-global-disabled',
            public_ip='21.21.21.89',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        released = []

        class FakeClient:
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {}

        class FakeStyle:
            def WARNING(self, text):
                return text

        class FakeStdout:
            style = FakeStyle()

            def write(self, text):
                return None

        with patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=False):
            ok = _release_static_ip_if_due(FakeClient(), self.plan.region_code, asset, 'StaticIp-sync-global-disabled', '', '21.21.21.89', FakeStdout())

        asset.refresh_from_db()
        self.assertFalse(ok)
        self.assertEqual(released, [])
        self.assertEqual(asset.status, CloudAsset.STATUS_UNKNOWN)
        self.assertEqual(asset.provider_status, '未附加固定IP-删除IP总开关关闭')
