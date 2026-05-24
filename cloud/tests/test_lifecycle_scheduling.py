from .common import *


class CloudServerLifecycleSchedulingMixin:
    def test_due_orders_use_order_expiry_for_lightsail_instead_of_stale_asset_expiry(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-DUE-1',
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
            public_ip='10.0.0.1',
            service_started_at=timezone.now() - timezone.timedelta(days=1),
            service_expires_at=timezone.now() + timezone.timedelta(days=10),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='stale-expired-asset',
            public_ip='10.0.0.9',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )

        due = async_to_sync(_get_due_orders)()

        self.assertFalse(any(item.id == order.id for item in due['expire']))
        self.assertFalse(any(item.id == order.id for item in due['suspend']))
        self.assertFalse(any(item.id == order.id for item in due['delete']))

    def test_due_orders_skip_suspend_when_account_shutdown_disabled(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='shutdown-off',
            external_account_id='acct-shutdown-off',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            shutdown_enabled=False,
        )
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-SUSPEND-OFF-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='10.0.0.21',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            cloud_account=account,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='shutdown-off-asset',
            public_ip='10.0.0.21',
            actual_expires_at=timezone.now() - timezone.timedelta(days=5),
            is_active=True,
        )

        due = async_to_sync(_get_due_orders)()

        self.assertFalse(any(item.id == order.id for item in due['suspend']))
        self.assertTrue(any(item.id == order.id for item in due['expire']))

    def test_lifecycle_suspend_execution_guard_respects_account_shutdown_disabled(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='shutdown-off-exec',
            external_account_id='acct-shutdown-off-exec',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            shutdown_enabled=False,
        )
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-SUSPEND-GUARD-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='10.0.0.22',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
            suspend_at=timezone.now() - timezone.timedelta(minutes=5),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            cloud_account=account,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='shutdown-off-exec-asset',
            public_ip='10.0.0.22',
            actual_expires_at=timezone.now() - timezone.timedelta(days=5),
            is_active=True,
        )
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [order],
            'delete': [],
            'recycle': [],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._is_cloud_suspend_time', return_value=True), \
            patch('cloud.lifecycle._stop_instance', new_callable=AsyncMock) as stop_mock:
            async_to_sync(lifecycle_tick)()

        stop_mock.assert_not_awaited()
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')

    def test_due_orders_include_order_expiry_when_asset_expiry_missing(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-ORDER-EXPIRY-FALLBACK',
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
            public_ip='10.0.0.23',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(hours=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='order-expiry-fallback-asset',
            public_ip='10.0.0.23',
            actual_expires_at=None,
            is_active=True,
        )

        due = async_to_sync(_get_due_orders)()

        self.assertTrue(any(item.id == order.id for item in due['expire']))

    def test_due_orders_respect_deferred_suspend_at(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-DEFERRED-SUSPEND',
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
            status='expiring',
            public_ip='10.0.0.24',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
        )
        deferred_suspend_at = timezone.now() + timezone.timedelta(hours=6)
        CloudServerOrder.objects.filter(id=order.id).update(suspend_at=deferred_suspend_at)
        order.refresh_from_db()
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='deferred-suspend-asset',
            public_ip='10.0.0.24',
            actual_expires_at=order.service_expires_at,
            is_active=True,
        )

        due = async_to_sync(_get_due_orders)()

        self.assertFalse(any(item.id == order.id for item in due['suspend']))

    def test_orphan_rebound_asset_waiting_manual_time_is_not_delete_due(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='orphan-rebound-wait-time',
            public_ip='10.0.0.26',
            instance_id='i-orphan-rebound-wait-time',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            provider_status='已重新绑定实例-待人工添加时间',
            note='未附加IP已重新绑定到实例，等待人工添加真实到期时间。',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        due = async_to_sync(_get_orphan_asset_delete_due)()

        self.assertFalse(any(item.id == asset.id for item in due))

    def test_due_orders_restore_suspend_after_account_shutdown_reenabled(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='shutdown-on',
            external_account_id='acct-shutdown-on',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            shutdown_enabled=False,
        )
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-SUSPEND-ON-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='10.0.0.22',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            cloud_account=account,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='shutdown-on-asset',
            public_ip='10.0.0.22',
            actual_expires_at=timezone.now() - timezone.timedelta(days=5),
            is_active=True,
        )

        self.assertFalse(any(item.id == order.id for item in async_to_sync(_get_due_orders)()['suspend']))

        account.shutdown_enabled = True
        account.save(update_fields=['shutdown_enabled', 'updated_at'])

        due = async_to_sync(_get_due_orders)()

        self.assertTrue(any(item.id == order.id for item in due['suspend']))

    def test_mark_suspended_only_updates_latest_asset_and_server(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-SUSPEND-1',
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
            public_ip='10.0.0.2',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
        )
        stale_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='stale-asset',
            public_ip='10.0.0.3',
            actual_expires_at=timezone.now() - timezone.timedelta(days=6),
            is_active=True,
        )
        active_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='active-asset',
            public_ip='10.0.0.2',
            actual_expires_at=timezone.now() - timezone.timedelta(days=5),
            is_active=True,
        )
        stale_server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='stale-server',
            public_ip='10.0.0.3',
            is_active=True,
        )
        active_server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='active-server',
            public_ip='10.0.0.2',
            is_active=True,
        )

        async_to_sync(_mark_suspended)(order.id, 'unit-test suspend')

        stale_asset.refresh_from_db()
        active_asset.refresh_from_db()
        stale_server.refresh_from_db()
        active_server.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(order.status, 'suspended')
        self.assertTrue(stale_asset.is_active)
        self.assertFalse(active_asset.is_active)
        self.assertTrue(stale_server.is_active)
        self.assertFalse(active_server.is_active)
        self.assertIn('unit-test suspend', active_asset.note)
        self.assertIn('unit-test suspend', active_server.note)

    def test_cloud_action_time_only_runs_in_configured_window(self):
        base = timezone.localtime(timezone.now()).replace(hour=15, minute=5, second=0, microsecond=0)
        with patch('cloud.lifecycle._config_time', return_value=(15, 0)):
            self.assertTrue(_is_cloud_suspend_time(now=base))
            self.assertTrue(_is_cloud_delete_safe_time(now=base))
            self.assertFalse(_is_cloud_suspend_time(now=base.replace(minute=11)))
            self.assertFalse(_is_cloud_delete_safe_time(now=base.replace(minute=11)))

    def test_lifecycle_tick_reads_suspend_time_config_outside_async_loop(self):
        now = timezone.now()
        local_now = timezone.localtime(now)
        configured_time = f'{local_now.hour:02d}:{local_now.minute:02d}'
        order = CloudServerOrder.objects.create(
            order_no='ASYNC-CONFIG-SUSPEND-1',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='Singapore',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            server_name='async-config-suspend-server',
            public_ip='13.250.10.20',
            service_started_at=now - timezone.timedelta(days=40),
            service_expires_at=now - timezone.timedelta(days=1),
            suspend_at=now - timezone.timedelta(minutes=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            public_ip=order.public_ip,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        CloudServerOrder.objects.filter(id=order.id).update(suspend_at=now - timezone.timedelta(minutes=1))
        order.suspend_at = now - timezone.timedelta(minutes=1)
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [order],
            'delete': [],
            'recycle': [],
        }

        def runtime_config_side_effect(key, default=None):
            if key == 'cloud_suspend_time':
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    return configured_time
                return '00:00'
            return default

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle.get_runtime_config', side_effect=runtime_config_side_effect), \
            patch('cloud.lifecycle._stop_instance', new_callable=AsyncMock, return_value=(True, 'stopped')) as stop_mock:
            async_to_sync(lifecycle_tick)()

        stop_mock.assert_awaited_once()

    def test_next_cloud_action_run_at_sticks_to_configured_time(self):
        base = timezone.localtime(timezone.now()).replace(hour=16, minute=20, second=0, microsecond=0)
        with patch('cloud.lifecycle._config_time', return_value=(15, 0)):
            run_at = _next_cloud_action_run_at('cloud_suspend_time', '15:00', now=base, min_delay_seconds=3600)
        self.assertEqual((run_at.hour, run_at.minute), (15, 0))
        self.assertGreater(run_at, base + timezone.timedelta(seconds=3600))

    def test_notice_plan_text_shows_configured_execution_time(self):
        order = CloudServerOrder.objects.create(
            order_no='PLAN-TEXT-1',
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
            public_ip='3.3.3.3',
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
            suspend_at=timezone.now() + timezone.timedelta(days=4),
            delete_at=timezone.now() + timezone.timedelta(days=4, hours=1),
        )
        with patch('cloud.lifecycle._config_time', side_effect=[(15, 30), (16, 45)]):
            text = _notice_plan_text(order)
        self.assertIn('关机计划:', text)
        self.assertNotIn('后台执行时间', text)

    def test_get_migration_due_orders_is_distinct(self):
        old_order = CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-OLD-1',
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
            status='deleting',
            public_ip='10.0.1.1',
            migration_due_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-NEW-1',
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
            public_ip='10.0.1.2',
            replacement_for=old_order,
        )
        CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-NEW-2',
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
            public_ip='10.0.1.3',
            replacement_for=old_order,
        )

        due_orders = async_to_sync(_get_migration_due_orders)()

        self.assertEqual([item.id for item in due_orders], [old_order.id])

    def test_get_migration_due_orders_skips_non_deleting_orders(self):
        old_order = CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-OLD-SKIP-1',
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
            public_ip='10.0.2.1',
            migration_due_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-NEW-SKIP-1',
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
            public_ip='10.0.2.2',
            replacement_for=old_order,
        )

        due_orders = async_to_sync(_get_migration_due_orders)()

        self.assertEqual(due_orders, [])

    def test_mark_failed_schedules_incomplete_instance_cleanup(self):
        order = CloudServerOrder.objects.create(
            order_no='FAILED-CLEANUP-SCHEDULE',
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
            status='provisioning',
            server_name='failed-instance-1',
            instance_id='failed-instance-1',
            public_ip='13.229.249.56',
        )
        cleanup_at = timezone.now() + timezone.timedelta(days=1)

        async_to_sync(_mark_failed)(order.id, '固定 IP 迁移失败', cleanup_at=cleanup_at)

        order.refresh_from_db()
        self.assertEqual(order.status, 'failed')
        self.assertEqual(order.delete_at, cleanup_at)
        self.assertIn('固定 IP 迁移失败', order.provision_note)

    def test_failed_instance_cleanup_due_orders_are_deleted(self):
        SiteConfig.set('cloud_server_delete_enabled', '1')
        order = CloudServerOrder.objects.create(
            order_no='FAILED-CLEANUP-DUE',
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
            status='failed',
            server_name='failed-instance-2',
            instance_id='failed-instance-2',
            public_ip='13.229.249.57',
            delete_at=timezone.now() - timezone.timedelta(minutes=1),
            provision_note='创建流程未完成，等待清理。',
        )

        due = async_to_sync(_get_due_orders)()
        self.assertIn(order.id, [item.id for item in due['delete']])

        async def fake_delete_instance(delete_order):
            return True, '失败新实例已删除'

        with patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_delete_safe_time', return_value=True), \
            patch('cloud.lifecycle._delete_instance', side_effect=fake_delete_instance):
            async_to_sync(lifecycle_tick)()

        order.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertEqual(order.instance_id, '')
        self.assertIn('失败新实例已删除', order.provision_note)

    def test_lifecycle_tick_rechecks_order_delete_at_before_cloud_delete(self):
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-RECHECK-FUTURE-DELETE',
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
            server_name='lifecycle-recheck-future-delete',
            public_ip='13.229.249.58',
            delete_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        due_order = CloudServerOrder.objects.get(id=order.id)
        CloudServerOrder.objects.filter(id=order.id).update(delete_at=timezone.now() + timezone.timedelta(days=1))
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [due_order],
            'recycle': [],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_delete_safe_time', return_value=True), \
            patch('cloud.lifecycle._delete_instance', new_callable=AsyncMock) as delete_mock:
            async_to_sync(lifecycle_tick)()

        delete_mock.assert_not_awaited()
        order.refresh_from_db()
        self.assertEqual(order.status, 'deleting')

    def test_lifecycle_tick_rechecks_order_ip_recycle_at_before_release(self):
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-RECHECK-FUTURE-RECYCLE',
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
            public_ip='13.229.249.59',
            previous_public_ip='13.229.249.59',
            static_ip_name='StaticIp-recheck-future-recycle',
            instance_id='',
            ip_recycle_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        due_order = CloudServerOrder.objects.get(id=order.id)
        CloudServerOrder.objects.filter(id=order.id).update(ip_recycle_at=timezone.now() + timezone.timedelta(days=1))
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
            patch('cloud.lifecycle._release_order_static_ip', new_callable=AsyncMock) as release_mock:
            async_to_sync(lifecycle_tick)()

        release_mock.assert_not_awaited()
        order.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertIsNotNone(order.ip_recycle_at)

    def test_create_cloud_server_rebuild_order_reuses_original_static_ip_without_temp(self):
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-1',
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
            public_ip='1.2.3.4',
            static_ip_name='hb-static-ip',
            mtproxy_port=8443,
            mtproxy_secret='ee1234567890abcdef1234567890abcd',
            mtproxy_link='tg://proxy?server=1.2.3.4&port=8443&secret=ee1234567890abcdef1234567890abcd',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )

        new_order, error = create_cloud_server_rebuild_order(source_order.id)

        self.assertIsNone(error)
        self.assertIsNotNone(new_order)
        self.assertEqual(new_order.replacement_for_id, source_order.id)
        self.assertEqual(new_order.static_ip_name, source_order.static_ip_name)
        self.assertEqual(new_order.mtproxy_secret, source_order.mtproxy_secret)
        self.assertEqual(new_order.mtproxy_port, source_order.mtproxy_port)
        self.assertEqual(new_order.status, 'paid')
        source_order.refresh_from_db()
        self.assertIsNotNone(source_order.migration_due_at)

    def test_reinit_request_reinstalls_current_server_without_rebuild_order(self):
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REINIT-NO-REBUILD-1',
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
            public_ip='1.2.3.44',
            login_password='root-password',
            static_ip_name='hb-static-ip-reinit',
            mtproxy_port=8443,
            mtproxy_secret='ee1234567890abcdef1234567890abcd',
            mtproxy_link='tg://proxy?server=1.2.3.44&port=8443&secret=ee1234567890abcdef1234567890abcd',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )

        result = async_to_sync(mark_cloud_server_reinit_requested)(source_order.id, self.user.id)

        self.assertEqual(result.id, source_order.id)
        self.assertFalse(CloudServerOrder.objects.filter(replacement_for=source_order).exists())
        source_order.refresh_from_db()
        self.assertIn('不创建新实例，不迁移固定 IP', source_order.provision_note)
        self.assertIsNone(source_order.migration_due_at)

    def test_rebuild_static_ip_context_corrects_stale_static_ip_name(self):
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-STATIC-RESOLVE-1',
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
            public_ip='3.1.169.183',
            static_ip_name='260410007170',
            mtproxy_port=8443,
            mtproxy_secret='ee1234567890abcdef1234567890abcd',
        )
        rebuild_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-STATIC-RESOLVE-2',
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
            status='paid',
            static_ip_name='260410007170',
            replacement_for=source_order,
        )
        with patch('cloud.provisioning._resolve_aws_static_ip_name_for_order', return_value='StaticIp-real-name'):
            context = async_to_sync(_get_rebuild_static_ip_context)(rebuild_order.id)

        self.assertTrue(context['is_rebuild'])
        self.assertEqual(context['original_static_ip_name'], 'StaticIp-real-name')
        self.assertEqual(context['payload']['original_public_ip'], '3.1.169.183')
        source_order.refresh_from_db()
        rebuild_order.refresh_from_db()
        self.assertEqual(source_order.static_ip_name, 'StaticIp-real-name')
        self.assertEqual(rebuild_order.static_ip_name, 'StaticIp-real-name')

    def test_resolve_static_ip_name_for_move_falls_back_to_public_ip(self):
        class FakeClient:
            def get_static_ip(self, staticIpName):
                raise Exception(f'The StaticIp does not exist: {staticIpName}')

            def get_static_ips(self, **kwargs):
                return {
                    'staticIps': [
                        {'name': 'StaticIp-real-name', 'ipAddress': '13.229.249.56', 'attachedTo': 'old-instance'},
                    ]
                }

        resolved = _resolve_static_ip_name_for_move(
            FakeClient(),
            '260410007170',
            {'order_no': 'SRVDOWNGRADE-TEST', 'original_public_ip': '13.229.249.56'},
        )

        self.assertEqual(resolved, 'StaticIp-real-name')

    def test_rebuild_order_create_payload_skips_static_ip_binding(self):
        source_account = CloudAccountConfig.objects.create(
            provider='aws',
            name='source-account',
            external_account_id='111111111111',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
        )
        other_account = CloudAccountConfig.objects.create(
            provider='aws',
            name='other-account',
            external_account_id='222222222222',
            access_key='ak2',
            secret_key='sk2',
            region_hint='ap-southeast-1',
        )
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-PAYLOAD-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=source_account,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='3.1.169.183',
            static_ip_name='StaticIp-2',
            mtproxy_port=8443,
            mtproxy_secret='ee1234567890abcdef1234567890abcd',
        )
        rebuild_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-PAYLOAD-2',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=source_account,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='paid',
            static_ip_name='StaticIp-2',
            replacement_for=source_order,
        )
        Server.objects.create(
            provider='aws_lightsail',
            account_label=f'aws+{other_account.external_account_id}+{other_account.name}',
            region_code=self.plan.region_code,
            public_ip='3.0.114.174',
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        payload = async_to_sync(_get_aws_create_payload)(rebuild_order.id)
        account_ids = async_to_sync(_candidate_cloud_account_ids)(rebuild_order.id)

        self.assertTrue(payload['skip_static_ip'])
        self.assertEqual(payload['static_ip_name'], '')
        self.assertEqual(payload['cloud_account_id'], source_account.id)
        self.assertEqual(account_ids, [source_account.id])

    def test_rebuild_source_expiry_moves_to_three_day_migration_due(self):
        source = CloudServerOrder.objects.create(
            order_no='REBUILD-SOURCE-EXPIRY',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            account_label='aws+111111111111+primary',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='1.2.3.4',
            service_expires_at=timezone.now() + timezone.timedelta(days=30),
            migration_due_at=timezone.now() + timezone.timedelta(days=3),
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=source, user=self.user, public_ip='1.2.3.4')
        Server.objects.create(source=Server.SOURCE_ORDER, order=source, user=self.user, public_ip='1.2.3.4')
        replacement = CloudServerOrder.objects.create(
            order_no='REBUILD-NEW-EXPIRY',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            account_label='aws+111111111111+primary',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='5.6.7.8',
            replacement_for=source,
        )

        async_to_sync(_mark_rebuild_source_pending_deletion)(source.id, replacement.id, '旧机保留 3 天后删除。')

        source.refresh_from_db()
        asset = CloudAsset.objects.get(order=source)
        server = Server.objects.get(order=source)
        self.assertEqual(source.service_expires_at, source.migration_due_at)
        self.assertEqual(source.renew_grace_expires_at, source.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(source.delete_at, source.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(asset.actual_expires_at, source.migration_due_at)
        self.assertEqual(server.expires_at, source.migration_due_at)

    def test_rebuild_job_keeps_old_instance_until_migration_due(self):
        from cloud.api import _run_rebuild_job

        source = CloudServerOrder.objects.create(
            order_no='REBUILD-JOB-SOURCE-KEEP-3D',
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
            status='deleting',
            public_ip='1.2.3.40',
            server_name='old-rebuild-job-instance',
            instance_id='old-rebuild-job-instance',
            migration_due_at=timezone.now() + timezone.timedelta(days=3),
            service_expires_at=timezone.now() + timezone.timedelta(days=3),
            delete_at=timezone.now() + timezone.timedelta(days=6),
        )
        replacement = CloudServerOrder.objects.create(
            order_no='REBUILD-JOB-NEW-KEEP-3D',
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
            public_ip='1.2.3.40',
            server_name='new-rebuild-job-instance',
            instance_id='new-rebuild-job-instance',
            replacement_for=source,
            service_expires_at=timezone.now() + timezone.timedelta(days=30),
        )

        async def fake_provision_cloud_server(order_id):
            self.assertEqual(order_id, replacement.id)
            return replacement

        with patch('cloud.api.provision_cloud_server', fake_provision_cloud_server), \
            patch('cloud.api._delete_instance') as delete_instance, \
            patch('cloud.api._mark_replaced_order_deleted') as mark_deleted:
            _run_rebuild_job(replacement.id)

        delete_instance.assert_not_called()
        mark_deleted.assert_not_called()
        source.refresh_from_db()
        self.assertEqual(source.status, 'deleting')
        self.assertIsNotNone(source.delete_at)

    def test_manual_admin_replace_order_takes_effect_immediately_for_aws_asset(self):
        old_expiry = timezone.now() + timezone.timedelta(days=10)
        new_expiry = timezone.now() + timezone.timedelta(days=40)
        old_order = CloudServerOrder.objects.create(
            order_no='MANUAL-REPLACE-OLD-1',
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
            public_ip='8.8.8.8',
            service_started_at=timezone.now(),
            service_expires_at=old_expiry,
            renew_grace_expires_at=old_expiry + timezone.timedelta(days=3),
            suspend_at=old_expiry + timezone.timedelta(days=3),
            delete_at=old_expiry + timezone.timedelta(days=3),
            ip_recycle_at=old_expiry + timezone.timedelta(days=18),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=old_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-proxy',
            public_ip='8.8.8.8',
            actual_expires_at=old_expiry,
            price='23.00',
        )
        server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=old_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            public_ip='8.8.8.8',
            expires_at=old_expiry,
            is_active=True,
        )
        new_user = TelegramUser.objects.create(tg_user_id=990002, username='svc_target')

        new_order, err = replace_cloud_asset_order_by_admin(
            asset,
            new_user=new_user,
            new_expires_at=new_expiry,
            previous_user=self.user,
            previous_expires_at=old_expiry,
        )

        self.assertIsNone(err)
        self.assertIsNotNone(new_order)
        old_order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(old_order.status, 'cancelled')
        self.assertIsNone(old_order.renew_grace_expires_at)
        self.assertIsNone(old_order.suspend_at)
        self.assertIsNone(old_order.delete_at)
        self.assertIsNone(old_order.ip_recycle_at)
        self.assertIsNotNone(old_order.expired_at)
        self.assertEqual(asset.order_id, new_order.id)
        self.assertEqual(server.order_id, new_order.id)
        self.assertEqual(asset.user_id, new_user.id)
        self.assertEqual(server.user_id, new_user.id)
        self.assertEqual(new_order.user_id, new_user.id)
        self.assertEqual(asset.actual_expires_at, new_expiry)
        self.assertEqual(server.expires_at, new_expiry)
        self.assertEqual(new_order.service_expires_at, new_expiry)
        self.assertEqual(new_order.replacement_for_id, old_order.id)

    def test_manual_admin_replace_order_aggregates_price_change_into_same_order(self):
        old_expiry = timezone.now() + timezone.timedelta(days=10)
        new_expiry = timezone.now() + timezone.timedelta(days=40)
        old_order = CloudServerOrder.objects.create(
            order_no='MANUAL-REPLACE-PRICE-OLD-1',
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
            public_ip='8.8.4.4',
            service_started_at=timezone.now(),
            service_expires_at=old_expiry,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=old_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-price-proxy',
            public_ip='8.8.4.4',
            actual_expires_at=new_expiry,
            price='29.00',
        )

        new_order, err = replace_cloud_asset_order_by_admin(
            asset,
            new_expires_at=new_expiry,
            new_price=asset.price,
            previous_user=self.user,
            previous_expires_at=old_expiry,
            previous_price='19.00',
        )

        self.assertIsNone(err)
        self.assertIsNotNone(new_order)
        old_order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(old_order.status, 'cancelled')
        self.assertEqual(asset.order_id, new_order.id)
        self.assertEqual(Decimal(str(new_order.total_amount)), Decimal('29.00'))
        self.assertEqual(Decimal(str(new_order.pay_amount)), Decimal('29.00'))
        self.assertIn('到期时间', new_order.provision_note or '')
        self.assertIn('价格 19.00 -> 29.00', new_order.provision_note or '')
        tags = _cloud_order_source_tags(new_order)
        self.assertEqual(
            [item[0] for item in tags],
            ['manual_expiry_change', 'manual_price_change'],
        )

    def test_update_cloud_asset_for_aws_creates_single_replace_order_for_expiry_and_price(self):
        old_expiry = timezone.now() + timezone.timedelta(days=10)
        new_expiry = timezone.now() + timezone.timedelta(days=20)
        order = CloudServerOrder.objects.create(
            order_no='MANUAL-UPDATE-PRICE-OLD-1',
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
            public_ip='4.4.4.4',
            service_started_at=timezone.now(),
            service_expires_at=old_expiry,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-update-price-proxy',
            public_ip='4.4.4.4',
            actual_expires_at=old_expiry,
            price='19.00',
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_price_replace', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({
                'price': '29.00',
                'actual_expires_at': new_expiry.isoformat(),
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        self.assertEqual(asset.price, Decimal('29.00'))
        self.assertEqual(asset.actual_expires_at, new_expiry)
        replace_orders = CloudServerOrder.objects.filter(replacement_for=order).order_by('id')
        self.assertEqual(replace_orders.count(), 1)
        new_order = replace_orders.get()
        self.assertTrue(new_order.order_no.startswith('SRVADMIN'))
        self.assertEqual(new_order.total_amount, Decimal('29.00'))
        self.assertEqual(new_order.pay_amount, Decimal('29.00'))
        self.assertIn('价格 19.00 -> 29.00', new_order.provision_note or '')
        self.assertEqual(
            CloudServerOrder.objects.filter(order_no__startswith='SRVMANUAL', replacement_for=order).count(),
            0,
        )

    def test_dashboard_order_expiry_update_recomputes_lifecycle_plan(self):
        old_expiry = timezone.now() + timezone.timedelta(days=1)
        new_expiry = timezone.now() + timezone.timedelta(days=20)
        order = CloudServerOrder.objects.create(
            order_no='DASH-ORDER-EXPIRY-UPDATE-1',
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
            public_ip='4.4.4.5',
            service_started_at=timezone.now(),
            service_expires_at=old_expiry,
        )
        old_suspend_at = order.suspend_at
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='dash-order-expiry-update-asset',
            public_ip='4.4.4.5',
            actual_expires_at=old_expiry,
        )
        Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='dash-order-expiry-update-server',
            public_ip='4.4.4.5',
            expires_at=old_expiry,
        )
        staff_user = get_user_model().objects.create_user(username='staff_order_expiry_update', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-orders/{order.id}/',
            data=json.dumps({'service_expires_at': new_expiry.isoformat()}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = cloud_order_detail(request, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset = CloudAsset.objects.get(order=order)
        server = Server.objects.get(order=order)
        self.assertEqual(order.service_expires_at, CloudServerOrder.normalize_expiry_time(new_expiry))
        self.assertGreater(order.suspend_at, old_suspend_at)
        self.assertEqual(order.renew_grace_expires_at, order.suspend_at)
        self.assertGreaterEqual(order.delete_at, order.suspend_at)
        self.assertGreater(order.ip_recycle_at, order.delete_at)
        self.assertEqual(asset.actual_expires_at, order.service_expires_at)
        self.assertEqual(server.expires_at, order.service_expires_at)
