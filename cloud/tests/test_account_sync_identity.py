from .common import *


class CloudServerAccountSyncIdentityMixin:
    def test_cloud_account_label_variants_cover_legacy_colon_labels(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='legacy-label-account',
            external_account_id='123456789012',
            access_key='AKIALEGACYLABEL1234',
            secret_key='legacy-secret-key-value-long-enough-1234567890',
            is_active=True,
        )

        variants = cloud_account_label_variants(account)

        self.assertIn(cloud_account_label(account), variants)
        self.assertIn(f'aws:{account.id}:legacy-label-account', variants)
        self.assertIn('aws:123456789012:legacy-label-account', variants)
        self.assertNotIn('aws', variants)

    def test_account_load_does_not_count_provider_only_legacy_label_for_every_account(self):
        first = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='load-account-a',
            external_account_id='111111111111',
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        second = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='load-account-b',
            external_account_id='222222222222',
            access_key='C' * 20,
            secret_key='D' * 40,
            is_active=True,
        )
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label=cloud_account_label(first),
            region_code='ap-southeast-1',
            server_name='load-a-current',
            instance_id='load-a-current',
            public_ip='8.8.8.81',
        )
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws',
            region_code='ap-southeast-1',
            server_name='legacy-provider-only',
            instance_id='legacy-provider-only',
            public_ip='8.8.8.82',
        )

        ordered = list_cloud_accounts_by_server_load('aws', 'ap-southeast-1')

        self.assertEqual([item.id for item in ordered[:2]], [second.id, first.id])

    def test_aliyun_desired_plan_id_is_preferred_without_locking_candidates(self):
        from cloud.aliyun_simple import _prefer_plan_id

        plans = [
            {'PlanId': 'fallback-plan', 'OriginPrice': '$5'},
            {'PlanId': 'desired-plan', 'OriginPrice': '$4'},
            {'PlanId': 'larger-plan', 'OriginPrice': '$8'},
        ]

        ordered = _prefer_plan_id(plans, 'desired-plan')

        self.assertEqual([item['PlanId'] for item in ordered], ['desired-plan', 'fallback-plan', 'larger-plan'])

    def test_aws_sync_server_resolution_accepts_legacy_account_label(self):
        from cloud.management.commands.sync_aws_assets import _resolve_server

        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='legacy-sync-account',
            external_account_id='123456789012',
            access_key='AKIALEGACYSYNC1234',
            secret_key='legacy-secret-key-value-long-enough-1234567890',
            is_active=True,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label=f'aws:{account.id}:legacy-sync-account',
            region_code='ap-southeast-1',
            server_name='legacy-sync-instance',
            instance_id='legacy-sync-instance',
            public_ip='8.8.8.88',
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        resolved = _resolve_server('legacy-sync-instance', '', '', None, account)

        self.assertEqual(resolved, server)

    def test_aws_sync_resolution_does_not_match_cross_region_same_instance_without_ip(self):
        from cloud.management.commands.sync_aws_assets import _resolve_asset, _resolve_server

        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='region-scope-account',
            external_account_id='123456789012',
            access_key='AKIAREGIONSCOPE123',
            secret_key='region-scope-secret-key-value-long-enough',
            is_active=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='us-east-1',
            asset_name='same-name-no-ip',
            instance_id='same-name-no-ip',
            public_ip='',
        )
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label=cloud_account_label(account),
            region_code='us-east-1',
            server_name='same-name-no-ip',
            instance_id='same-name-no-ip',
            public_ip='',
        )

        self.assertIsNone(_resolve_asset('same-name-no-ip', '', '', None, account, 'ap-southeast-1'))
        self.assertIsNone(_resolve_server('same-name-no-ip', '', '', None, account, 'ap-southeast-1'))

    def test_aliyun_sync_resolution_does_not_match_cross_region_same_instance_without_ip(self):
        from cloud.management.commands.sync_aliyun_assets import _resolve_asset, _resolve_server

        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-region-scope-account',
            external_account_id='aliyun-region-scope-id',
            access_key='aliyun-region-ak',
            secret_key='aliyun-region-sk',
            is_active=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            provider='aliyun_simple',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='cn-shanghai',
            asset_name='aliyun-same-name-no-ip',
            instance_id='aliyun-same-name-no-ip',
            public_ip='',
        )
        Server.objects.create(
            source=Server.SOURCE_ALIYUN,
            provider='aliyun_simple',
            account_label=cloud_account_label(account),
            region_code='cn-shanghai',
            server_name='aliyun-same-name-no-ip',
            instance_id='aliyun-same-name-no-ip',
            public_ip='',
        )

        self.assertIsNone(_resolve_asset('aliyun-same-name-no-ip', '', account, 'cn-hongkong'))
        self.assertIsNone(_resolve_server('aliyun-same-name-no-ip', '', account, 'cn-hongkong'))

    def test_aliyun_audit_inventory_uses_asset_account(self):
        from cloud.management.commands.audit_cloud_asset_ip_presence import Command

        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-audit-account',
            external_account_id='aliyun-audit-account-id',
            access_key='aliyun-ak',
            secret_key='aliyun-sk',
            is_active=True,
        )
        captured = {}

        class FakeClient:
            def list_instances_with_options(self, request, runtime_options):
                captured['request'] = request
                return SimpleNamespace(body=SimpleNamespace(to_map=lambda: {'Instances': []}))

        fake_aliyun_module = SimpleNamespace(models=SimpleNamespace(ListInstancesRequest=lambda **kwargs: kwargs))
        with patch.dict(sys.modules, {'alibabacloud_swas_open20200601': fake_aliyun_module}), \
            patch('cloud.management.commands.audit_cloud_asset_ip_presence._build_client', return_value=FakeClient()) as build_client:
            inventory = Command()._load_aliyun_inventory('cn-hongkong', account)

        self.assertEqual(inventory, {'instances': {}})
        build_client.assert_called_once()
        self.assertIs(build_client.call_args.kwargs['account'], account)

    def test_daily_address_stats_are_separated_by_account_key(self):
        user = TelegramUser.objects.create(tg_user_id=9901001, username='daily_stat_scope')
        stats_date = timezone.localdate()

        first = bump_daily_address_stat(
            user_id=user.id,
            address='TAddressScope',
            currency='USDT',
            direction='income',
            amount=Decimal('1.5'),
            account_scope=DailyAddressStat.ACCOUNT_SCOPE_CLOUD,
            account_key='cloud-account-a',
            stats_date=stats_date,
        )
        second = bump_daily_address_stat(
            user_id=user.id,
            address='TAddressScope',
            currency='USDT',
            direction='income',
            amount=Decimal('2.5'),
            account_scope=DailyAddressStat.ACCOUNT_SCOPE_CLOUD,
            account_key='cloud-account-b',
            stats_date=stats_date,
        )

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(
            DailyAddressStat.objects.filter(
                user=user,
                address='TAddressScope',
                currency='USDT',
                stats_date=stats_date,
                account_scope=DailyAddressStat.ACCOUNT_SCOPE_CLOUD,
            ).count(),
            2,
        )

    def test_server_connection_ip_guard_rejects_mismatch_before_ssh(self):
        ok, note = validate_server_connection_ip('54.151.227.23', ['13.228.232.184'], context='test_mismatch')

        self.assertFalse(ok)
        self.assertIn('目标 IP 54.151.227.23 与预期 IP 13.228.232.184 不一致', note)

    def test_cloud_created_server_name_uses_actual_aws_instance_name_only(self):
        aws_result = SimpleNamespace(instance_id='requested-node-1')
        aliyun_result = SimpleNamespace(instance_id='i-aliyun-resource-id')

        self.assertEqual(_cloud_created_server_name('aws_lightsail', 'requested-node', aws_result), 'requested-node-1')
        self.assertEqual(_cloud_created_server_name('aliyun_simple', 'requested-node', aliyun_result), 'requested-node')

    def test_cloud_orders_list_exposes_auto_renew_enabled(self):
        order = CloudServerOrder.objects.create(
            order_no='ORDER-LIST-AUTO-RENEW-1',
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
            public_ip='13.250.20.21',
            auto_renew_enabled=True,
            service_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        staff_user = get_user_model().objects.create_user(username='staff_order_list_auto_renew', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-orders/')
        request.user = staff_user

        response = cloud_orders_list(request)

        self.assertEqual(response.status_code, 200)
        rows = json.loads(response.content)['data']
        row = next(item for item in rows if item['id'] == order.id)
        self.assertTrue(row['auto_renew_enabled'])

    def test_server_connection_ip_guard_requires_public_ipv4(self):
        ok, note = validate_server_connection_ip('127.0.0.1', ['127.0.0.1'], context='test_loopback')

        self.assertFalse(ok)
        self.assertIn('目标 IP 无效', note)

    def test_server_connection_ip_guard_retries_mismatch_until_refreshed(self):
        refreshed = iter(['54.151.227.23', '13.228.232.184'])

        ok, note, final_ip = async_to_sync(validate_server_connection_ip_with_retry)(
            '54.151.227.23',
            ['13.228.232.184'],
            context='test_retry_mismatch',
            attempts=3,
            delay_seconds=0,
            refresh_target=lambda: next(refreshed),
        )

        self.assertTrue(ok)
        self.assertEqual(final_ip, '13.228.232.184')
        self.assertIn('第 3 次校验通过', note)

    def test_server_connection_ip_guard_does_not_retry_invalid_target(self):
        refresh = AsyncMock(return_value='13.228.232.184')

        ok, note, final_ip = async_to_sync(validate_server_connection_ip_with_retry)(
            '127.0.0.1',
            ['13.228.232.184'],
            context='test_retry_invalid',
            attempts=3,
            delay_seconds=0,
            refresh_target=refresh,
        )

        self.assertFalse(ok)

        self.assertEqual(final_ip, '')
        self.assertIn('目标 IP 无效', note)
        refresh.assert_not_called()

    def test_aws_expected_ip_existence_check_passes_when_static_ip_exists(self):
        class Client:
            def get_static_ips(self):
                return {'staticIps': [{'ipAddress': '13.228.232.184'}]}

            def get_instances(self):
                return {'instances': []}

        with patch('cloud.aws_lightsail._aws_client_from_order_data', return_value=(Client(), '')):
            ok, note = _public_ip_exists_sync({'order_no': 'TEST'}, ['13.228.232.184'])

        self.assertTrue(ok)
        self.assertIn('存在于固定 IP', note)

    def test_aws_expected_ip_existence_check_fails_when_ip_missing(self):
        class Client:
            def get_static_ips(self):
                return {'staticIps': [{'ipAddress': '54.151.227.23'}]}

            def get_instances(self):
                return {'instances': [{'publicIpAddress': '54.151.227.24'}]}

        with patch('cloud.aws_lightsail._aws_client_from_order_data', return_value=(Client(), '')):
            ok, note = _public_ip_exists_sync({'order_no': 'TEST'}, ['13.228.232.184'])

        self.assertFalse(ok)
        self.assertIn('在当前云账号中不存在', note)

    def test_manual_order_delete_bypasses_schedule_limits(self):
        from bot.api import _run_shutdown_order_sync

        account = self._aws_test_account()
        account.shutdown_enabled = False
        account.save(update_fields=['shutdown_enabled', 'updated_at'])
        order = CloudServerOrder.objects.create(
            order_no='MANUAL-DELETE-BYPASS-ORDER-1',
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
            status='deleting',
            public_ip='52.77.18.241',
            delete_at=timezone.now() + timezone.timedelta(days=1),
        )
        with patch('bot.api._is_cloud_delete_safe_time', return_value=False) as safe_time, \
            patch('bot.api._delete_instance', new=AsyncMock(return_value=(True, 'manual delete ok'))), \
            patch('bot.api._mark_deleted', new=AsyncMock()):
            result = _run_shutdown_order_sync(order.id, enforce_schedule=False)

        self.assertTrue(result['ok'])
        safe_time.assert_not_called()

    def test_manual_orphan_asset_delete_bypasses_schedule_limits(self):
        from bot.api import _run_orphan_asset_delete_sync

        account = self._aws_test_account()
        account.shutdown_enabled = False
        account.save(update_fields=['shutdown_enabled', 'updated_at'])
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-owner-asset',
            instance_id='manual-owner-asset-instance',
            public_ip='52.77.18.241',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        original_asset_id = asset.id
        with patch('bot.api._is_cloud_delete_safe_time', return_value=False) as safe_time, \
            patch('bot.api._delete_orphan_asset_instance', new=AsyncMock(return_value=(True, 'manual asset delete ok'))), \
            patch('bot.api._mark_orphan_asset_deleted', new=AsyncMock()):
            result = _run_orphan_asset_delete_sync(asset.id, enforce_schedule=False)

        self.assertTrue(result['ok'])
        safe_time.assert_not_called()

    def test_manual_unattached_ip_delete_writes_log_and_history_item(self):
        from bot.api import _run_unattached_ip_delete_sync

        account = self._aws_test_account()
        account.shutdown_enabled = False
        account.save(update_fields=['shutdown_enabled', 'updated_at'])
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-unattached-ip-delete',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/manual-unattached-ip-delete',
            public_ip='52.77.18.244',
            actual_expires_at=timezone.now() + timezone.timedelta(days=3),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        with patch('bot.api._release_unattached_static_ip', new=AsyncMock(return_value=(True, 'manual release ok'))):
            result = _run_unattached_ip_delete_sync(asset.id, enforce_schedule=False)

        asset.refresh_from_db()
        self.assertTrue(result['ok'])
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertTrue(CloudIpLog.objects.filter(asset=asset, event_type=CloudIpLog.EVENT_RECYCLED).exists())
        items = _unattached_ip_delete_items(limit=20)
        history = [item for item in items if item.get('is_history') and item.get('public_ip') == '52.77.18.244']
        self.assertTrue(history)
        self.assertIn('manual release ok', history[0]['note'])
        self.assertEqual(history[0]['deletion_source_label'], '人工手动删除')

    def test_legacy_unattached_ip_delete_log_without_known_note_shows_history(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='legacy-unattached-ip-delete',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/legacy-unattached-ip-delete',
            previous_public_ip='52.77.18.245',
            status=CloudAsset.STATUS_DELETED,
            provider_status='未附加固定IP-已到期删除',
            is_active=False,
        )
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_RECYCLED,
            asset=asset,
            previous_public_ip='52.77.18.245',
            public_ip=None,
            note='旧版本释放成功',
        )

        items = _unattached_ip_delete_items(limit=20)
        history = [item for item in items if item.get('is_history') and item.get('public_ip') == '52.77.18.245']
        self.assertTrue(history)
        self.assertIn('旧版本释放成功', history[0]['note'])
        self.assertEqual(history[0]['deletion_source_label'], '到期自动删除')

    def test_manual_order_delete_writes_server_history_item(self):
        from bot.api import _run_shutdown_order_sync

        order = CloudServerOrder.objects.create(
            order_no='MANUAL-DELETE-HISTORY-ORDER-1',
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
            public_ip='52.77.18.246',
            previous_public_ip='52.77.18.246',
            service_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='manual-delete-history-order-asset',
            public_ip=order.public_ip,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_DELETING,
            is_active=True,
        )
        with patch('bot.api._delete_instance', new=AsyncMock(return_value=(True, 'manual server delete ok'))):
            result = _run_shutdown_order_sync(order.id, enforce_schedule=False)

        self.assertTrue(result['ok'])
        self.assertTrue(CloudIpLog.objects.filter(order=order, event_type=CloudIpLog.EVENT_DELETED).exists())
        items = _shutdown_log_items(limit=20)
        history = [item for item in items if item.get('public_ip') == '52.77.18.246']
        self.assertTrue(history)
        self.assertIn('manual server delete ok', history[0]['note'])
        self.assertEqual(history[0]['deletion_source_label'], '人工手动删除')

    def test_missing_aws_instance_delete_marks_order_history(self):
        from bot.api import _run_shutdown_order_sync

        class Client:
            def delete_instance(self, instanceName):
                raise Exception('NotFoundException: instance does not exist')

        order = CloudServerOrder.objects.create(
            order_no='MANUAL-MISSING-DELETE-ORDER-1',
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
            server_name='missing-instance',
            public_ip='52.77.18.241',
        )
        with patch('cloud.lifecycle._aws_client', return_value=Client()):
            result = _run_shutdown_order_sync(order.id, enforce_schedule=False)

        order.refresh_from_db()
        self.assertTrue(result['ok'])
        self.assertEqual(order.status, 'deleted')
        self.assertTrue(CloudIpLog.objects.filter(order=order, event_type='deleted').exists())

    def test_missing_aws_orphan_asset_delete_marks_asset_history(self):
        from bot.api import _run_orphan_asset_delete_sync

        class Client:
            def delete_instance(self, instanceName):
                raise Exception('NotFoundException: instance does not exist')

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='missing-orphan-asset',
            instance_id='missing-orphan-asset',
            public_ip='52.77.18.242',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        with patch('cloud.lifecycle._aws_client', return_value=Client()):
            result = _run_orphan_asset_delete_sync(asset.id, enforce_schedule=False)

        asset.refresh_from_db()
        self.assertTrue(result['ok'])
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertTrue(CloudIpLog.objects.filter(asset=asset, event_type='deleted').exists())

    def test_dashboard_shutdown_plan_run_respects_delete_at(self):
        from bot.api import run_shutdown_plan_order

        SiteConfig.set('cloud_server_delete_enabled', '1')
        order = CloudServerOrder.objects.create(
            order_no='PLAN-RUN-FUTURE-DELETE-ORDER-1',
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
            server_name='future-delete-order-instance',
            public_ip='52.77.18.247',
            delete_at=timezone.now() + timezone.timedelta(days=1),
        )
        staff_user = get_user_model().objects.create_user(
            username='staff_plan_run_future_delete_order',
            password='x',
            is_staff=True,
            is_superuser=True,
        )
        request = self.factory.post(f'/api/admin/tasks/plans/orders/{order.id}/run/')
        request.user = staff_user

        with patch('bot.api._delete_instance', new=AsyncMock()) as delete_mock:
            response = run_shutdown_plan_order(request, order.id)

        data = json.loads(response.content)['data']
        delete_mock.assert_not_awaited()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['success_count'], 0)
        self.assertIn('服务器删除时间未到', data['message'])

    def test_dashboard_orphan_asset_plan_run_respects_computed_delete_time(self):
        from bot.api import run_orphan_asset_delete_plan

        SiteConfig.set('cloud_server_delete_enabled', '1')
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
            asset_name='future-orphan-plan-run',
            instance_id='future-orphan-plan-run',
            public_ip='52.77.18.248',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(
            username='staff_plan_run_future_orphan_asset',
            password='x',
            is_staff=True,
            is_superuser=True,
        )
        request = self.factory.post(f'/api/admin/tasks/plans/orphan-assets/{asset.id}/run/')
        request.user = staff_user

        with patch('bot.api._delete_orphan_asset_instance', new=AsyncMock()) as delete_mock:
            response = run_orphan_asset_delete_plan(request, asset.id)

        data = json.loads(response.content)['data']
        delete_mock.assert_not_awaited()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['success_count'], 0)
        self.assertIn('未到服务器删除时间', data['message'])

    def test_dashboard_unattached_ip_plan_run_respects_delete_time(self):
        from bot.api import run_unattached_ip_delete_plan

        SiteConfig.set('cloud_ip_delete_enabled', '1')
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='future-unattached-ip-plan-run',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/future-unattached-ip-plan-run',
            public_ip='52.77.18.249',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(
            username='staff_plan_run_future_unattached_ip',
            password='x',
            is_staff=True,
            is_superuser=True,
        )
        request = self.factory.post(f'/api/admin/tasks/plans/unattached-ips/{asset.id}/run/')
        request.user = staff_user

        with patch('bot.api._release_unattached_static_ip', new=AsyncMock()) as release_mock:
            response = run_unattached_ip_delete_plan(request, asset.id)

        data = json.loads(response.content)['data']
        release_mock.assert_not_awaited()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['success_count'], 0)
        self.assertIn('未到 IP 删除时间', data['message'])

    def test_dashboard_unattached_ip_plan_run_uses_ip_delete_time_window(self):
        from bot.api import run_unattached_ip_delete_plan

        SiteConfig.set('cloud_ip_delete_enabled', '1')
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='window-unattached-ip-plan-run',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/window-unattached-ip-plan-run',
            public_ip='52.77.18.251',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(
            username='staff_plan_run_window_unattached_ip',
            password='x',
            is_staff=True,
            is_superuser=True,
        )
        request = self.factory.post(f'/api/admin/tasks/plans/unattached-ips/{asset.id}/run/')
        request.user = staff_user

        with patch('bot.api._is_cloud_unattached_ip_delete_time', return_value=False) as safe_time, \
            patch('bot.api._release_unattached_static_ip', new=AsyncMock()) as release_mock:
            response = run_unattached_ip_delete_plan(request, asset.id)

        data = json.loads(response.content)['data']
        safe_time.assert_called_once()
        release_mock.assert_not_awaited()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['success_count'], 0)
        self.assertIn('IP 删除执行时间窗口', data['message'])

    def test_unattached_ip_delete_respects_shutdown_disabled_account(self):
        from bot.api import _run_unattached_ip_delete_sync, _unattached_ip_delete_items

        SiteConfig.set('cloud_ip_delete_enabled', '1')
        account = self._aws_test_account()
        account.shutdown_enabled = False
        account.save(update_fields=['shutdown_enabled', 'updated_at'])
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='disabled-static-ip',
            public_ip='52.77.18.250',
            provider_status='未附加固定IP',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        due_ids = {item.id for item in async_to_sync(_get_unattached_static_ip_delete_due)()}
        result = _run_unattached_ip_delete_sync(asset.id, enforce_schedule=True)
        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('asset_id') == asset.id)

        self.assertNotIn(asset.id, due_ids)
        self.assertFalse(result['ok'])
        self.assertIn('关机计划已关闭', result['error'])
        self.assertEqual(row['queue_status'], 'shutdown_disabled')

    def test_lifecycle_aws_client_requires_bound_account(self):
        from cloud.lifecycle import _aws_client

        with self.assertRaisesMessage(ValueError, '缺少绑定的 AWS 云账号'):
            _aws_client(self.plan.region_code, None)

    def test_aws_create_client_requires_bound_account(self):
        from cloud.aws_lightsail import _aws_client_from_order_data

        client, error = _aws_client_from_order_data({
            'provider': 'aws_lightsail',
            'region_code': self.plan.region_code,
            'order_no': 'AWS-CREATE-NO-ACCOUNT-1',
        })

        self.assertIsNone(client)
        self.assertIn('缺少绑定的 AWS 云账号', error)

    def test_aliyun_create_and_renew_require_bound_account(self):
        from cloud.aliyun_simple import _create_instance_sync

        order = CloudServerOrder.objects.create(
            order_no='ALIYUN-NO-ACCOUNT-1',
            user=self.user,
            plan=self.plan,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='香港',
            plan_name='基础型',
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='47.1.1.1',
            instance_id='aliyun-instance-without-account',
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
        )

        create_result = _create_instance_sync(order, 'aliyun-no-account')
        with self.assertRaisesMessage(ValueError, '缺少订单绑定的启用阿里云账号'):
            apply_cloud_server_renewal.__wrapped__(order.id, 31, False)
        self.assertFalse(create_result.ok)
        self.assertIn('缺少订单绑定的启用云账号', create_result.note)

    def test_renewal_aws_runtime_check_requires_bound_account(self):
        from cloud.services import _aws_lightsail_client_for_order

        order = CloudServerOrder.objects.create(
            order_no='AWS-RUNTIME-NO-ACCOUNT',
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
            pay_method='balance',
            status='completed',
            public_ip='44.44.44.44',
        )

        with self.assertRaisesMessage(ValueError, '缺少绑定的 AWS 云账号'):
            _aws_lightsail_client_for_order(order)

    def test_sync_servers_missing_state_does_not_bypass_provider_confirmation(self):
        order = CloudServerOrder.objects.create(
            order_no='SYNC-SERVERS-NO-INSTANT-DELETE',
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
            pay_method='balance',
            status='completed',
            server_name='sync-servers-still-confirming',
            instance_id='sync-servers-still-confirming',
            public_ip='44.44.44.45',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        updated = _apply_server_missing_state('aws_lightsail', self.plan.region_code, [], None)

        self.assertEqual(updated, 0)
        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(order.status, 'completed')
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(server.status, Server.STATUS_RUNNING)

    def test_dedupe_cloud_assets_does_not_merge_cross_account_same_ip(self):
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws+111+primary',
            region_code='ap-southeast-1',
            asset_name='asset-account-a',
            public_ip='13.250.30.10',
            status=CloudAsset.STATUS_RUNNING,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws+222+secondary',
            region_code='ap-southeast-1',
            asset_name='asset-account-b',
            public_ip='13.250.30.10',
            status=CloudAsset.STATUS_RUNNING,
        )

        call_command('dedupe_cloud_assets')

        self.assertEqual(CloudAsset.objects.filter(public_ip='13.250.30.10').count(), 2)

    def test_dedupe_cloud_assets_merges_same_cloud_account_label_variants(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='dedupe-label-variant',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        old_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account,
            account_label='aws_lightsail+123456789012+dedupe-label-variant',
            region_code='ap-southeast-1',
            asset_name='dedupe-label-variant-old',
            public_ip='13.250.30.13',
            status=CloudAsset.STATUS_RUNNING,
        )
        keep_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='ap-southeast-1',
            asset_name='dedupe-label-variant-new',
            public_ip='13.250.30.13',
            status=CloudAsset.STATUS_RUNNING,
        )
        log = CloudIpLog.objects.create(
            event_type=CloudIpLog.EVENT_CHANGED,
            asset=old_asset,
            public_ip='13.250.30.13',
            note='old duplicate log',
        )

        call_command('dedupe_cloud_assets')

        self.assertEqual(CloudAsset.objects.filter(public_ip='13.250.30.13').count(), 1)
        self.assertTrue(CloudAsset.objects.filter(id=keep_asset.id).exists())
        log.refresh_from_db()
        self.assertEqual(log.asset_id, keep_asset.id)

    def test_cloud_assets_list_dedupes_same_cloud_account_label_variants(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='ui-dedupe-label-variant',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account,
            account_label='aws_lightsail+123456789012+ui-dedupe-label-variant',
            region_code='ap-southeast-1',
            asset_name='ui-dedupe-old',
            public_ip='13.250.30.14',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        keep_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='ap-southeast-1',
            asset_name='ui-dedupe-new',
            public_ip='13.250.30.14',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        admin = get_user_model().objects.create_user(username='ui_dedupe_admin', password='x', is_staff=True)
        request = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1'})
        request.user = admin

        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['items'][0]['id'], keep_asset.id)

    def test_dedupe_cloud_assets_does_not_merge_cross_region_same_instance(self):
        for region, public_ip in [('ap-southeast-1', '13.250.30.15'), ('ap-northeast-1', '13.250.30.16')]:
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                provider='aws_lightsail',
                account_label='aws+111+primary',
                region_code=region,
                asset_name='asset-region-scope',
                instance_id='same-instance-name',
                public_ip=public_ip,
                status=CloudAsset.STATUS_RUNNING,
            )

        call_command('dedupe_cloud_assets')

        self.assertEqual(CloudAsset.objects.filter(instance_id='same-instance-name').count(), 2)

    def test_dedupe_cloud_assets_keeps_same_instance_with_different_ips(self):
        for public_ip in ['13.250.31.15', '13.250.31.16']:
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                provider='aws_lightsail',
                account_label='aws+111+primary',
                region_code='ap-southeast-1',
                asset_name='asset-same-instance-different-ip',
                instance_id='same-instance-different-ip',
                public_ip=public_ip,
                status=CloudAsset.STATUS_RUNNING,
            )

        call_command('dedupe_cloud_assets')

        self.assertEqual(CloudAsset.objects.filter(instance_id='same-instance-different-ip').count(), 2)

    def test_dedupe_servers_does_not_delete_cross_account_instance_id(self):
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws+111+primary',
            region_code='ap-southeast-1',
            server_name='server-account-a',
            instance_id='same-instance-name',
            public_ip='13.250.30.11',
        )
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws+222+secondary',
            region_code='ap-southeast-1',
            server_name='server-account-b',
            instance_id='same-instance-name',
            public_ip='13.250.30.12',
        )

        call_command('dedupe_servers')

        self.assertEqual(Server.objects.filter(instance_id='same-instance-name').count(), 2)

    def test_dedupe_servers_does_not_delete_cross_region_instance_id(self):
        for region, public_ip in [('ap-southeast-1', '13.250.30.17'), ('ap-northeast-1', '13.250.30.18')]:
            Server.objects.create(
                source=Server.SOURCE_AWS_SYNC,
                provider='aws_lightsail',
                account_label='aws+111+primary',
                region_code=region,
                server_name='server-region-scope',
                instance_id='same-region-instance-name',
                public_ip=public_ip,
            )

        call_command('dedupe_servers')

        self.assertEqual(Server.objects.filter(instance_id='same-region-instance-name').count(), 2)

    def test_dedupe_servers_keeps_same_instance_with_different_ips(self):
        for public_ip in ['13.250.31.17', '13.250.31.18']:
            Server.objects.create(
                source=Server.SOURCE_AWS_SYNC,
                provider='aws_lightsail',
                account_label='aws+111+primary',
                region_code='ap-southeast-1',
                server_name='server-same-instance-different-ip',
                instance_id='server-same-instance-different-ip',
                public_ip=public_ip,
            )

        call_command('dedupe_servers')

        self.assertEqual(Server.objects.filter(instance_id='server-same-instance-different-ip').count(), 2)

    def test_upsert_cloud_asset_keeps_server_records_separated_by_account(self):
        for account_label, public_ip in [('aws+111+primary', '13.250.30.13'), ('aws+222+secondary', '13.250.30.14')]:
            call_command(
                'upsert_cloud_asset',
                kind=CloudAsset.KIND_SERVER,
                provider='aws_lightsail',
                account_label=account_label,
                region_code='ap-southeast-1',
                instance_id='manual-same-instance',
                public_ip=public_ip,
            )

        self.assertEqual(CloudAsset.objects.filter(instance_id='manual-same-instance').count(), 2)
        self.assertEqual(Server.objects.filter(instance_id='manual-same-instance').count(), 2)

    def test_upsert_cloud_asset_keeps_same_instance_with_different_ips(self):
        for public_ip in ['13.250.31.19', '13.250.31.20']:
            call_command(
                'upsert_cloud_asset',
                kind=CloudAsset.KIND_SERVER,
                provider='aws_lightsail',
                account_label='aws+111+primary',
                region_code='ap-southeast-1',
                instance_id='manual-same-instance-different-ip',
                public_ip=public_ip,
            )

        self.assertEqual(CloudAsset.objects.filter(instance_id='manual-same-instance-different-ip').count(), 2)
        self.assertEqual(Server.objects.filter(instance_id='manual-same-instance-different-ip').count(), 2)
        self.assertTrue(Server.objects.filter(instance_id='manual-same-instance-different-ip', public_ip='13.250.31.19').exists())
        self.assertTrue(Server.objects.filter(instance_id='manual-same-instance-different-ip', public_ip='13.250.31.20').exists())
