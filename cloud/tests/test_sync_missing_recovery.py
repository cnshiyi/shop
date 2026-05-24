from .common import *


class CloudServerSyncMissingRecoveryMixin:
    def test_order_primary_records_prefer_ip_over_stale_names(self):
        from cloud.services import _order_primary_asset, _order_primary_server

        order = CloudServerOrder.objects.create(
            order_no='PRIMARY-IP-FIRST-1',
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
            public_ip='9.9.9.20',
            previous_public_ip='9.9.9.20',
            server_name='stale-server-name',
            instance_id='stale-instance-id',
            provider_resource_id='stale-resource-id',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        stale_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='stale-server-name',
            instance_id='stale-instance-id',
            provider_resource_id='stale-resource-id',
            public_ip='8.8.8.8',
            status=CloudAsset.STATUS_RUNNING,
        )
        ip_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='current-server-name',
            instance_id='current-instance-id',
            public_ip='9.9.9.20',
            status=CloudAsset.STATUS_RUNNING,
        )
        stale_server = Server.objects.create(
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            server_name='stale-server-name',
            instance_id='stale-instance-id',
            provider_resource_id='stale-resource-id',
            public_ip='8.8.8.8',
            status=Server.STATUS_RUNNING,
        )
        ip_server = Server.objects.create(
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            server_name='current-server-name',
            instance_id='current-instance-id',
            public_ip='9.9.9.20',
            status=Server.STATUS_RUNNING,
        )
        stale_asset.previous_public_ip = '9.9.9.20'
        stale_asset.save(update_fields=['previous_public_ip', 'updated_at'])
        stale_server.previous_public_ip = '9.9.9.20'
        stale_server.save(update_fields=['previous_public_ip', 'updated_at'])

        self.assertEqual(_order_primary_asset(order).id, ip_asset.id)
        self.assertEqual(_order_primary_server(order).id, ip_server.id)
        self.assertNotEqual(_order_primary_asset(order).id, stale_asset.id)
        self.assertNotEqual(_order_primary_server(order).id, stale_server.id)

    def test_lifecycle_aws_resource_resolution_prefers_ip(self):
        from cloud.lifecycle import _aws_instance_name_for_order, _aws_static_ip_name_for_asset, _delete_instance_sync, _delete_orphan_asset_instance_sync

        class FakeClient:
            def __init__(self):
                self.deleted_instances = []

            def get_instances(self, **kwargs):
                return {'instances': [{'name': 'current-ip-instance', 'publicIpAddress': '9.9.9.30'}]}

            def get_static_ips(self, **kwargs):
                return {'staticIps': [{'name': 'current-static-ip-name', 'ipAddress': '9.9.9.31'}]}

            def delete_instance(self, instanceName):
                self.deleted_instances.append(instanceName)

        order = CloudServerOrder(
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            server_name='stale-server-name',
            public_ip='',
            previous_public_ip='9.9.9.30',
        )
        asset = CloudAsset(
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='stale-static-ip-name',
            public_ip='',
            previous_public_ip='9.9.9.31',
        )

        self.assertEqual(_aws_instance_name_for_order(order, FakeClient()), 'current-ip-instance')
        self.assertEqual(_aws_static_ip_name_for_asset(asset, FakeClient()), 'current-static-ip-name')

        delete_client = FakeClient()
        with patch('cloud.lifecycle._aws_client', return_value=delete_client):
            ok, _ = _delete_instance_sync(order)
        self.assertTrue(ok)
        self.assertEqual(delete_client.deleted_instances, ['current-ip-instance'])

        orphan_asset = CloudAsset(
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='stale-orphan-name',
            public_ip='',
            previous_public_ip='9.9.9.30',
        )
        orphan_client = FakeClient()
        with patch('cloud.lifecycle._aws_client', return_value=orphan_client):
            ok, _ = _delete_orphan_asset_instance_sync(orphan_asset)
        self.assertTrue(ok)
        self.assertEqual(orphan_client.deleted_instances, ['current-ip-instance'])

    def test_aws_renewal_start_check_prefers_ip_over_stale_server_name(self):
        from cloud.services import _ensure_aws_instance_running

        started = []

        class FakeClient:
            def get_instances(self, **kwargs):
                return {'instances': [{'name': 'current-ip-instance', 'publicIpAddress': '9.9.9.40'}]}

            def get_instance(self, instanceName):
                if instanceName == 'stale-server-name':
                    raise AssertionError('should not query stale server name first')
                return {'instance': {'name': instanceName, 'publicIpAddress': '9.9.9.40', 'state': {'name': 'stopped'}}}

            def start_instance(self, instanceName):
                started.append(instanceName)

        order = CloudServerOrder.objects.create(
            order_no='ORDER-AWS-START-IP-FIRST',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            server_name='stale-server-name',
            public_ip='',
            previous_public_ip='9.9.9.40',
        )

        with patch('cloud.services._aws_lightsail_client_for_order', return_value=FakeClient()):
            ok, note = _ensure_aws_instance_running(order)

        self.assertTrue(ok)
        self.assertEqual(started, ['current-ip-instance'])
        self.assertIn('已发起开机', note)

    def test_admin_start_restores_suspended_order_to_completed(self):
        from cloud.services import start_cloud_server_from_admin

        account = self._aws_test_account()
        order = CloudServerOrder.objects.create(
            order_no='ORDER-ADMIN-START-RESTORE',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='suspended',
            server_name='admin-start-instance',
            instance_id='admin-start-instance',
            public_ip='9.9.9.41',
            previous_public_ip='9.9.9.41',
            service_started_at=timezone.now() - timezone.timedelta(days=10),
            service_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='admin-start-instance',
            instance_id='admin-start-instance',
            public_ip='9.9.9.41',
            previous_public_ip='9.9.9.41',
            status=CloudAsset.STATUS_STOPPED,
            provider_status='已关机-到期延停',
            is_active=False,
        )
        server = Server.objects.create(
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            server_name='admin-start-instance',
            instance_id='admin-start-instance',
            public_ip='9.9.9.41',
            previous_public_ip='9.9.9.41',
            status=Server.STATUS_STOPPED,
            provider_status='已关机-到期延停',
            is_active=False,
        )

        class FakeClient:
            def get_instance(self, instanceName):
                return {
                    'instance': {
                        'name': instanceName,
                        'publicIpAddress': '9.9.9.41',
                        'state': {'name': 'running'},
                    }
                }

        with patch('cloud.services._aws_lightsail_client_for_order', return_value=FakeClient()), \
             patch('cloud.services._aws_instance_name_for_order_runtime', return_value='admin-start-instance'), \
             patch('cloud.services._ensure_mtproxy_after_renewal', return_value=(True, 'MTProxy OK')):
            returned_order, warning = async_to_sync(start_cloud_server_from_admin)(order.id)

        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertIsNone(warning)
        self.assertEqual(returned_order.status, 'completed')
        self.assertEqual(order.status, 'completed')
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(server.status, Server.STATUS_RUNNING)
        self.assertTrue(asset.is_active)
        self.assertTrue(server.is_active)

    def test_dashboard_asset_order_inference_scopes_duplicate_ip_by_account(self):
        from cloud.api import _infer_asset_order

        first_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='infer-account-a',
            region_hint=self.plan.region_code,
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        second_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='infer-account-b',
            region_hint=self.plan.region_code,
            access_key='C' * 20,
            secret_key='D' * 40,
            is_active=True,
        )
        stale_order = CloudServerOrder.objects.create(
            order_no='ORDER-INFER-STALE',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            cloud_account=first_account,
            account_label=cloud_account_label(first_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            public_ip='9.9.9.50',
            server_name='stale-name-match',
        )
        target_order = CloudServerOrder.objects.create(
            order_no='ORDER-INFER-TARGET',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            cloud_account=second_account,
            account_label=cloud_account_label(second_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            public_ip='9.9.9.50',
            server_name='target-name',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=second_account,
            account_label=cloud_account_label(second_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='stale-name-match',
            public_ip='9.9.9.50',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        self.assertEqual(_infer_asset_order(asset), target_order)
        self.assertNotEqual(_infer_asset_order(asset), stale_order)

    def test_sync_aws_missing_check_uses_previous_public_ip_before_delete(self):
        from cloud.management.commands.sync_aws_assets import _mark_deleted_when_missing_in_aws

        class DummyStyle:
            def WARNING(self, text):
                return text

        class DummyStdout:
            def __init__(self):
                self.stdout = self
                self.style = DummyStyle()
            def write(self, text):
                return text

        order = CloudServerOrder.objects.create(
            order_no='AWS-MISS-PREV-IP-1',
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
            public_ip=None,
            previous_public_ip='9.9.9.10',
            instance_id='',
            provider_resource_id='StaticIp-prev-ip-1',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='aws-prev-ip-asset',
            public_ip=None,
            previous_public_ip='9.9.9.10',
            instance_id='',
            provider_resource_id='StaticIp-prev-ip-1',
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='aws-prev-ip-server',
            public_ip=None,
            previous_public_ip='9.9.9.10',
            instance_id='',
            provider_resource_id='StaticIp-prev-ip-1',
            status=Server.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )

        deleted = _mark_deleted_when_missing_in_aws(self.plan.region_code, set(), {'9.9.9.10'}, DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()

        self.assertEqual(deleted, [])
        self.assertEqual(asset.status, CloudAsset.STATUS_UNKNOWN)
        self.assertEqual(server.status, Server.STATUS_UNKNOWN)
        self.assertEqual(order.status, 'completed')
        self.assertNotIn('云上未找到实例/IP-待确认', asset.provider_status or '')

    def test_sync_aws_missing_blank_asset_does_not_delete_unrelated_blank_server(self):
        from cloud.management.commands.sync_aws_assets import _mark_deleted_when_missing_in_aws

        class DummyStyle:
            def WARNING(self, text):
                return text

        class DummyStdout:
            def __init__(self):
                self.stdout = self
                self.style = DummyStyle()
            def write(self, text):
                return text

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='aws-blank-dirty-asset',
            public_ip='',
            previous_public_ip='',
            instance_id='',
            provider_resource_id='',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            server_name='aws-unrelated-live-server',
            public_ip='9.9.9.77',
            previous_public_ip='',
            instance_id='aws-unrelated-live-instance',
            provider_resource_id='',
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        with patch('cloud.sync_safety.get_missing_confirmation_interval_minutes', return_value=0):
            for _ in range(5):
                _mark_deleted_when_missing_in_aws(self.plan.region_code, {'aws-unrelated-live-instance'}, {'9.9.9.77'}, DummyStdout())

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(server.status, Server.STATUS_RUNNING)

    def test_sync_aliyun_order_update_recalculates_lifecycle_on_expiry_change(self):
        from cloud.management.commands.sync_aliyun_assets import _aliyun_order_updates_from_sync

        old_expires_at = timezone.now() + timezone.timedelta(days=2)
        new_expires_at = timezone.now() + timezone.timedelta(days=31)
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-expiry-sync',
            external_account_id='aliyun-expiry-sync',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='cn-hongkong',
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='ALIYUN-EXPIRY-SYNC-1',
            user=self.user,
            plan=self.plan,
            provider='aliyun_simple',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='cn-hongkong',
            region_name='中国香港',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='6.6.6.70',
            instance_id='i-aliyun-expiry-sync',
            provider_resource_id='i-aliyun-expiry-sync',
            service_started_at=timezone.now() - timezone.timedelta(days=10),
            service_expires_at=old_expires_at,
            renew_notice_sent_at=timezone.now(),
            auto_renew_notice_sent_at=timezone.now(),
            auto_renew_failure_notice_sent_at=timezone.now(),
            delete_notice_sent_at=timezone.now(),
            recycle_notice_sent_at=timezone.now(),
        )

        updates = _aliyun_order_updates_from_sync(
            order,
            normalized_status=CloudAsset.STATUS_RUNNING,
            expires_at=new_expires_at,
            account=account,
            account_label=cloud_account_label(account),
            region='cn-hongkong',
            item={'RegionId': 'cn-hongkong'},
            asset_name='aliyun-expiry-sync',
            instance_id='i-aliyun-expiry-sync',
            public_ip='6.6.6.70',
        )

        self.assertEqual(updates['service_expires_at'], new_expires_at)
        self.assertGreater(updates['suspend_at'], new_expires_at)
        self.assertGreaterEqual(updates['delete_at'], updates['suspend_at'])
        self.assertGreater(updates['ip_recycle_at'], updates['delete_at'])
        self.assertIsNone(updates['renew_notice_sent_at'])
        self.assertIsNone(updates['auto_renew_notice_sent_at'])
        self.assertIsNone(updates['delete_notice_sent_at'])

    def test_sync_aliyun_missing_instance_requires_five_passes_before_delete(self):
        from cloud.management.commands.sync_aliyun_assets import _mark_deleted_when_missing_in_aliyun

        class DummyStyle:
            def WARNING(self, text):
                return text

        class DummyStdout:
            def __init__(self):
                self.stdout = self
                self.style = DummyStyle()
            def write(self, text):
                return text

        order = CloudServerOrder.objects.create(
            order_no='ALIYUN-MISS-CONFIRM-1',
            user=self.user,
            plan=self.plan,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='6.6.6.6',
            previous_public_ip='6.6.6.6',
            instance_id='i-aliyun-missing-confirm-1',
            provider_resource_id='i-aliyun-missing-confirm-1',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            order=order,
            user=self.user,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            asset_name='aliyun-missing-confirm-asset',
            public_ip='6.6.6.6',
            previous_public_ip='6.6.6.6',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        server = Server.objects.create(
            source=Server.SOURCE_ALIYUN,
            order=order,
            user=self.user,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            server_name='aliyun-missing-confirm-server',
            public_ip='6.6.6.6',
            previous_public_ip='6.6.6.6',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        deleted = _mark_deleted_when_missing_in_aliyun('cn-hongkong', set(), DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(deleted, [])
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.provider_status, '云上未找到实例-待确认')
        self.assertEqual(server.status, Server.STATUS_RUNNING)
        self.assertEqual(order.status, 'completed')

        with patch('cloud.sync_safety.get_missing_confirmation_interval_minutes', return_value=0):
            for _ in range(3):
                deleted = _mark_deleted_when_missing_in_aliyun('cn-hongkong', set(), DummyStdout())
                asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
                self.assertEqual(deleted, [])
                self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
                self.assertEqual(server.status, Server.STATUS_RUNNING)
                self.assertEqual(order.status, 'completed')

            deleted = _mark_deleted_when_missing_in_aliyun('cn-hongkong', set(), DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
        self.assertTrue(deleted)
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(server.status, Server.STATUS_DELETED)
        self.assertEqual(order.status, 'deleted')

    def test_sync_aliyun_missing_blank_asset_does_not_delete_unrelated_blank_server(self):
        from cloud.management.commands.sync_aliyun_assets import _mark_deleted_when_missing_in_aliyun

        class DummyStyle:
            def WARNING(self, text):
                return text

        class DummyStdout:
            def __init__(self):
                self.stdout = self
                self.style = DummyStyle()
            def write(self, text):
                return text

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            asset_name='aliyun-blank-dirty-asset',
            public_ip='',
            previous_public_ip='',
            instance_id='',
            provider_resource_id='',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        server = Server.objects.create(
            source=Server.SOURCE_ALIYUN,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            server_name='aliyun-unrelated-live-server',
            public_ip='6.6.6.77',
            previous_public_ip='',
            instance_id='aliyun-unrelated-live-instance',
            provider_resource_id='',
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        with patch('cloud.sync_safety.get_missing_confirmation_interval_minutes', return_value=0):
            for _ in range(5):
                _mark_deleted_when_missing_in_aliyun('cn-hongkong', set(), DummyStdout())

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(server.status, Server.STATUS_RUNNING)

    def test_aliyun_order_is_not_enqueued_for_shutdown_delete_plan(self):
        from bot.api import _collect_shutdown_plan_queue

        order = CloudServerOrder.objects.create(
            order_no='ALIYUN-NO-DELETE-PLAN-1',
            user=self.user,
            plan=self.plan,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='suspended',
            public_ip='6.6.7.1',
            previous_public_ip='6.6.7.1',
            instance_id='i-aliyun-no-delete-plan-1',
            provider_resource_id='i-aliyun-no-delete-plan-1',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
            delete_at=timezone.now() - timezone.timedelta(days=1),
        )

        queue = _collect_shutdown_plan_queue(timezone.now(), limit=20)
        order_ids = {item.get('order_id') for item in [*queue['due_items'], *queue['future_plan_items']]}

        self.assertNotIn(order.id, order_ids)

    def test_manual_aliyun_delete_plan_is_blocked_without_local_delete(self):
        from bot.api import _run_shutdown_order_sync

        order = CloudServerOrder.objects.create(
            order_no='ALIYUN-NO-DELETE-RUN-1',
            user=self.user,
            plan=self.plan,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='suspended',
            public_ip='6.6.7.2',
            previous_public_ip='6.6.7.2',
            instance_id='i-aliyun-no-delete-run-1',
            provider_resource_id='i-aliyun-no-delete-run-1',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
            delete_at=timezone.now() - timezone.timedelta(days=1),
        )

        result = _run_shutdown_order_sync(order.id, enforce_schedule=False)
        order.refresh_from_db()

        self.assertFalse(result['ok'])
        self.assertIn('未接入删除 API', result['error'])
        self.assertEqual(order.status, 'suspended')

    def test_failed_aliyun_order_is_not_enqueued_for_fallback_delete(self):
        order = CloudServerOrder.objects.create(
            order_no='ALIYUN-FAILED-NO-FALLBACK-DELETE-1',
            user=self.user,
            plan=self.plan,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='failed',
            public_ip='6.6.7.3',
            previous_public_ip='6.6.7.3',
            instance_id='i-aliyun-failed-no-delete-1',
            provider_resource_id='i-aliyun-failed-no-delete-1',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
            delete_at=timezone.now() - timezone.timedelta(days=1),
        )

        due = async_to_sync(_get_due_orders)()

        self.assertNotIn(order.id, [item.id for item in due['delete']])

    def test_sync_aws_assets_rebinds_unattached_ip_when_instance_reappears(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-rebind',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='rebind-static-ip',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/rebind-static-ip',
            public_ip='10.9.0.2',
            actual_expires_at=timezone.now() + timezone.timedelta(days=3),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='rebind-static-ip-server',
            public_ip='10.9.0.2',
            expires_at=asset.actual_expires_at,
            status=Server.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )

        class FakeLightsailClient:
            def get_static_ips(self, **kwargs):
                return {'staticIps': [], 'nextPageToken': None}

            def get_instances(self, **kwargs):
                return {
                    'instances': [{
                        'name': 'i-rebound-sync-1',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-rebound-sync-1',
                        'state': {'name': 'running'},
                        'location': {'regionName': '新加坡'},
                        'publicIpAddress': '10.9.0.2',
                        'bundleId': 'micro_1_0',
                        'blueprintId': 'debian_12',
                    }],
                    'nextPageToken': None,
                }

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(asset.instance_id, 'i-rebound-sync-1')
        self.assertEqual(asset.provider_status, '已重新绑定实例-待人工添加时间')
        self.assertIsNone(asset.actual_expires_at)
        self.assertTrue(asset.is_active)
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.note, '未附加固定IP')
        self.assertEqual(server.instance_id, 'i-rebound-sync-1')
        self.assertIsNone(server.expires_at)
        self.assertEqual(server.provider_status, '已重新绑定实例-待人工添加时间')
        self.assertTrue(server.is_active)
        self.assertEqual(server.status, Server.STATUS_RUNNING)

    def test_sync_aws_assets_updates_retained_asset_after_renewal_recovery(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-retained-recovered',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        old_order = CloudServerOrder.objects.create(
            order_no='RETAINED-RECOVERED-OLD',
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
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            instance_id='',
            static_ip_name='recovered-static-ip',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=5),
            cloud_account=account,
            account_label=account_label,
        )
        recovery_expires_at = timezone.now() + timezone.timedelta(days=31)
        recovery_order = CloudServerOrder.objects.create(
            order_no='RETAINED-RECOVERED-NEW',
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
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            instance_id='i-recovered-sync-1',
            server_name='i-recovered-sync-1',
            static_ip_name='recovered-static-ip',
            service_expires_at=recovery_expires_at,
            cloud_account=account,
            account_label=account_label,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=old_order,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='recovered-static-ip',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/recovered-static-ip',
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            actual_expires_at=old_order.ip_recycle_at,
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            note='固定IP保留中-实例已删除',
            is_active=False,
        )

        class FakeLightsailClient:
            def get_static_ips(self, **kwargs):
                return {'staticIps': [{
                    'name': 'recovered-static-ip',
                    'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/recovered-static-ip',
                    'ipAddress': '10.9.0.3',
                    'attachedTo': 'i-recovered-sync-1',
                    'location': {'regionName': '新加坡'},
                }], 'nextPageToken': None}

            def get_instances(self, **kwargs):
                return {
                    'instances': [{
                        'name': 'i-recovered-sync-1',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-recovered-sync-1',
                        'state': {'name': 'running'},
                        'location': {'regionName': '新加坡'},
                        'publicIpAddress': '10.9.0.3',
                        'bundleId': 'micro_1_0',
                        'blueprintId': 'debian_12',
                    }],
                    'nextPageToken': None,
                }

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        asset.refresh_from_db()
        self.assertEqual(CloudAsset.objects.filter(public_ip='10.9.0.3').count(), 1)
        self.assertEqual(asset.instance_id, 'i-recovered-sync-1')
        self.assertEqual(asset.order_id, recovery_order.id)
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.provider_status, '运行中')
        self.assertEqual(asset.actual_expires_at, recovery_expires_at)
        self.assertTrue(asset.is_active)
        self.assertEqual(asset.note, '固定IP保留中-实例已删除')

    def test_sync_aws_assets_preserves_existing_unattached_ip_due_time(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-stale-unattached-ip',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        stale_due_at = timezone.now() - timezone.timedelta(days=1)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-stale-unattached',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-stale-unattached',
            public_ip='10.9.0.4',
            actual_expires_at=stale_due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )

        class FakeLightsailClient:
            def get_static_ips(self, **kwargs):
                return {
                    'staticIps': [{
                        'name': 'StaticIp-stale-unattached',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-stale-unattached',
                        'ipAddress': '10.9.0.4',
                        'attachedTo': '',
                        'location': {'regionName': '新加坡'},
                    }],
                    'nextPageToken': None,
                }

            def get_instances(self, **kwargs):
                return {'instances': [], 'nextPageToken': None}

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        asset.refresh_from_db()
        self.assertEqual(asset.provider_status, '未附加固定IP')
        self.assertEqual(asset.actual_expires_at, stale_due_at)
        self.assertEqual(asset.note, '未附加固定IP')

    def test_sync_aws_unattached_ip_duplicate_cleanup_is_account_scoped(self):
        account_a = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-static-account-a',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_b = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-static-account-b',
            external_account_id='222222222222',
            access_key='C' * 20,
            secret_key='D' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        foreign_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account_b,
            account_label=cloud_account_label(account_b),
            region_code='ap-southeast-1',
            asset_name='foreign-static-ip',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:222222222222:StaticIp/foreign-static-ip',
            public_ip='10.9.0.40',
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )

        class FakeLightsailClient:
            def get_static_ips(self, **kwargs):
                return {
                    'staticIps': [{
                        'name': 'account-a-static-ip',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/account-a-static-ip',
                        'ipAddress': '10.9.0.40',
                        'attachedTo': '',
                        'location': {'regionName': '新加坡'},
                    }],
                    'nextPageToken': None,
                }

            def get_instances(self, **kwargs):
                return {'instances': [], 'nextPageToken': None}

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), \
            patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), \
            patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1', account_id=str(account_a.id))

        foreign_asset.refresh_from_db()
        self.assertEqual(foreign_asset.status, CloudAsset.STATUS_UNKNOWN)
        self.assertEqual(foreign_asset.public_ip, '10.9.0.40')

    def test_sync_aws_assets_preserves_existing_manual_asset_note(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-status-note-append',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='i-status-note-append',
            instance_id='i-status-note-append',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-status-note-append',
            public_ip='10.9.0.5',
            actual_expires_at=timezone.now() + timezone.timedelta(days=10),
            status=CloudAsset.STATUS_STOPPED,
            provider_status='旧状态',
            note='人工备注：不要覆盖',
            is_active=False,
        )

        class FakeLightsailClient:
            def get_static_ips(self, **kwargs):
                return {'staticIps': [], 'nextPageToken': None}

            def get_instances(self, **kwargs):
                return {
                    'instances': [{
                        'name': 'i-status-note-append',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-status-note-append',
                        'state': {'name': 'running'},
                        'location': {'regionName': '新加坡'},
                        'publicIpAddress': '10.9.0.5',
                        'bundleId': 'micro_1_0',
                        'blueprintId': 'debian_12',
                    }],
                    'nextPageToken': None,
                }

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        asset.refresh_from_db()
        self.assertEqual(asset.provider_status, '运行中')
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.note, '人工备注：不要覆盖')

    def test_cloud_asset_sync_interval_defaults_to_ten_minutes(self):
        from core.runtime_config import get_cloud_asset_sync_interval_seconds

        self.assertEqual(get_cloud_asset_sync_interval_seconds(), 600)

    def test_sync_aws_assets_keeps_runtime_running_when_order_is_suspended(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-suspended-runtime',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        order = CloudServerOrder.objects.create(
            order_no='AWS-SYNC-SUSPENDED-RUNTIME-1',
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
            status='suspended',
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            instance_id='i-suspended-runtime-1',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-suspended-runtime-1',
            server_name='i-suspended-runtime-1',
            service_started_at=timezone.now() - timezone.timedelta(days=20),
            service_expires_at=timezone.now() - timezone.timedelta(days=1),
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
            asset_name='i-suspended-runtime-1',
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_SUSPENDED,
            provider_status='已到期关机，等待删除（云端已关机）',
            is_active=False,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='i-suspended-runtime-1',
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            expires_at=order.service_expires_at,
            status=Server.STATUS_SUSPENDED,
            provider_status='已到期关机，等待删除（云端已关机）',
            is_active=False,
        )

        class FakeLightsailClient:
            def get_static_ips(self, **kwargs):
                return {'staticIps': [], 'nextPageToken': None}

            def get_instances(self, **kwargs):
                return {
                    'instances': [{
                        'name': 'i-suspended-runtime-1',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-suspended-runtime-1',
                        'state': {'name': 'running'},
                        'location': {'regionName': '新加坡'},
                        'publicIpAddress': '10.9.0.3',
                        'bundleId': 'micro_1_0',
                        'blueprintId': 'debian_12',
                    }],
                    'nextPageToken': None,
                }

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        asset.refresh_from_db()
        server.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertTrue(asset.is_active)
        self.assertEqual(server.status, Server.STATUS_RUNNING)
        self.assertTrue(server.is_active)
        self.assertEqual(order.status, 'suspended')
        self.assertIn('云端运行中', asset.provider_status or '')
        self.assertIn('已到期关机，等待删除', asset.provider_status or '')

    def test_dirty_deleted_note_does_not_hide_live_synced_asset(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='dirty-note-live-asset',
            instance_id='dirty-note-live-asset',
            public_ip='10.9.0.7',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            note='历史脏数据：IP校验发现云上不存在，已标记删除；最新同步又确认运行中',
            is_active=True,
        )

        self.assertFalse(_cloud_asset_deleted_or_missing(asset))
        queried = async_to_sync(get_proxy_asset_by_ip_for_user)('10.9.0.7', self.user.id)
        self.assertIsNotNone(queried)
        self.assertEqual(queried.id, asset.id)

    def test_sync_aws_assets_revives_dirty_deleted_asset_when_instance_exists(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-revive-dirty-deleted-asset',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='i-revive-dirty-deleted-asset',
            public_ip='10.9.0.6',
            previous_public_ip='10.9.0.6',
            instance_id='i-revive-dirty-deleted-asset',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-revive-dirty-deleted-asset',
            status=CloudAsset.STATUS_DELETED,
            provider_status='云上未找到实例/IP-待确认',
            note='IP校验发现云上不存在，已标记删除',
            is_active=False,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='i-revive-dirty-deleted-asset',
            public_ip='10.9.0.6',
            previous_public_ip='10.9.0.6',
            instance_id='i-revive-dirty-deleted-asset',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-revive-dirty-deleted-asset',
            status=Server.STATUS_DELETED,
            provider_status='云上未找到实例/IP-待确认',
            note='服务器校验发现云上不存在，已标记删除',
            is_active=False,
        )

        class FakeLightsailClient:
            def get_static_ips(self, **kwargs):
                return {'staticIps': [], 'nextPageToken': None}

            def get_instances(self, **kwargs):
                return {
                    'instances': [{
                        'name': 'i-revive-dirty-deleted-asset',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-revive-dirty-deleted-asset',
                        'state': {'name': 'running'},
                        'location': {'regionName': '新加坡'},
                        'publicIpAddress': '10.9.0.6',
                        'bundleId': 'micro_1_0',
                        'blueprintId': 'debian_12',
                    }],
                    'nextPageToken': None,
                }

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(CloudAsset.objects.filter(instance_id='i-revive-dirty-deleted-asset').count(), 1)
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertTrue(asset.is_active)
        self.assertNotIn('已标记删除', asset.note or '')
        self.assertNotIn('云上不存在', asset.note or '')
        self.assertEqual(server.status, Server.STATUS_RUNNING)
        self.assertTrue(server.is_active)
        self.assertNotIn('已标记删除', server.note or '')
        queried = async_to_sync(get_proxy_asset_by_ip_for_user)('10.9.0.6', self.user.id)
        self.assertIsNotNone(queried)
        self.assertEqual(queried.id, asset.id)
