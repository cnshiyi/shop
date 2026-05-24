from .common import *


class CloudServerLifecyclePlanTablesMixin:
    def test_lifecycle_plans_show_shutdown_disabled_plan_state(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='shutdown-disabled-plan-state',
            external_account_id='acct-shutdown-disabled-plan-state',
            access_key='ak',
            secret_key='sk',
            region_hint=self.plan.region_code,
            shutdown_enabled=False,
        )
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-SHUTDOWN-DISABLED-STATE-1',
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
            status='suspended',
            public_ip='3.3.3.38',
            service_expires_at=timezone.now() - timezone.timedelta(days=3),
            suspend_at=timezone.now() - timezone.timedelta(days=2),
            delete_at=timezone.now() - timezone.timedelta(hours=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='shutdown-disabled-plan-state-asset',
            instance_id='shutdown-disabled-plan-state-asset',
            public_ip=order.public_ip,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_shutdown_disabled_state', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        row = next(item for item in data['due_items'] if item.get('order_id') == order.id)

        self.assertEqual(row['queue_status'], 'shutdown_disabled')
        self.assertEqual(row['plan_state'], 'shutdown_disabled')
        self.assertEqual(row['plan_state_label'], '关机计划关闭')
        self.assertFalse(row['should_execute'])
        self.assertIn('关机计划关闭', row['blocked_reason'])

    def test_lifecycle_plans_move_deleted_orphan_server_out_of_future(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='deleted-orphan-should-not-stay-future',
            public_ip='3.3.3.88',
            instance_id='i-deleted-orphan-should-not-stay-future',
            actual_expires_at=timezone.now() + timezone.timedelta(days=3),
            status=CloudAsset.STATUS_RUNNING,
            is_active=False,
            provider_status='运行中',
            note='无订单 AWS 资产到期，已执行真实删机。 状态: 固定IP仍存在但未附加；公网IP: 3.3.3.88；计划释放时间: 2026-05-24 18:00:00',
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_plan_deleted_orphan', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']

        future_asset_ids = {item.get('asset_id') for item in data['future_plan_items']}
        self.assertNotIn(asset.id, future_asset_ids)

        history_row = next(item for item in data['history_items'] if item.get('asset_id') == asset.id)
        self.assertEqual(history_row['resource_state_label'], '实例已删除（固定IP保留中）')
        self.assertEqual(history_row['plan_state_label'], '等待IP回收')
        self.assertFalse(history_row['should_execute'])

    def test_manual_order_delete_enters_lifecycle_success_history(self):
        from bot.api import _run_shutdown_order_sync

        order = CloudServerOrder.objects.create(
            order_no='MANUAL-DELETE-LIFECYCLE-HISTORY-1',
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
            public_ip='52.77.18.247',
            previous_public_ip='52.77.18.247',
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
            asset_name='manual-delete-lifecycle-history-asset',
            public_ip=order.public_ip,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_DELETING,
            is_active=True,
        )
        with patch('bot.api._delete_instance', new=AsyncMock(return_value=(True, 'manual lifecycle delete ok'))):
            result = _run_shutdown_order_sync(order.id, enforce_schedule=False)

        self.assertTrue(result['ok'])
        staff_user = get_user_model().objects.create_user(username='staff_manual_delete_lifecycle_history', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        history_row = next(item for item in data['history_items'] if item.get('order_id') == order.id)

        self.assertEqual(history_row['result_label'], '成功')
        self.assertEqual(history_row['deletion_source_label'], '人工手动删除')
        self.assertIn('manual lifecycle delete ok', history_row['note'])
        self.assertEqual(history_row['execution_status'], '已删除')
        ip_delete_rows = [
            item for item in data['ip_delete_items']
            if item.get('order_id') == order.id or item.get('public_ip') == '52.77.18.247'
        ]
        self.assertFalse(ip_delete_rows)

    def test_lifecycle_plans_compact_request_keeps_ip_delete_history_item(self):
        now = timezone.now()
        for index in range(60):
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'compact-active-ip-{index}',
                public_ip=f'10.0.0.{index}',
                provider_status='未附加固定IP',
                instance_id='',
                actual_expires_at=now + timezone.timedelta(days=10),
                is_active=True,
            )

        history_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='compact-ip-history-visible',
            previous_public_ip='52.77.18.250',
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
        )
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_DELETED,
            asset=history_asset,
            previous_public_ip='52.77.18.250',
            public_ip=None,
            note='人工手动删除；执行内容：固定 IP 已释放；IP校验发现云上不存在，已标记删除',
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_ip_history_compact', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'compact': 1, 'limit': 50})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']

        self.assertGreaterEqual(data['ip_delete_history_count'], 1)
        self.assertTrue(any(item.get('is_history') and item.get('public_ip') == '52.77.18.250' for item in data['ip_delete_items']))

    def test_lifecycle_plans_include_ip_delete_history_item(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='lifecycle-ip-history-visible',
            previous_public_ip='52.77.18.248',
            status=CloudAsset.STATUS_DELETED,
            provider_status='未附加固定IP-已释放',
            is_active=False,
        )
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_RECYCLED,
            asset=asset,
            previous_public_ip='52.77.18.248',
            public_ip=None,
            note='人工手动删除；执行内容：释放固定IP成功',
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_ip_history_visible', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        rows = [item for item in data['ip_delete_items'] if item.get('is_history') and item.get('public_ip') == '52.77.18.248']

        self.assertTrue(rows)
        self.assertGreaterEqual(data['ip_delete_history_count'], 1)
        self.assertEqual(rows[0]['deletion_source_label'], '人工手动删除')

    def test_lifecycle_plans_sort_shutdown_items_by_delete_time(self):
        later_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='sort-delete-plan-later',
            instance_id='sort-delete-plan-later',
            public_ip='5.5.5.61',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        earlier_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='sort-delete-plan-earlier',
            instance_id='sort-delete-plan-earlier',
            public_ip='5.5.5.62',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=timezone.now() + timezone.timedelta(days=10),
        )
        middle_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='sort-delete-plan-middle',
            instance_id='sort-delete-plan-middle',
            public_ip='5.5.5.63',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_sort_delete_time', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': 1})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        rows = [
            item for item in data['shutdown_items']
            if item.get('asset_id') in {later_asset.id, earlier_asset.id, middle_asset.id}
        ]

        self.assertEqual([item['asset_id'] for item in rows], [earlier_asset.id, middle_asset.id, later_asset.id])
        delete_times = [parse_datetime(item['delete_at']) for item in rows]
        self.assertEqual(delete_times, sorted(delete_times))

    def test_lifecycle_plans_group_same_delete_time_by_user(self):
        second_user = TelegramUser.objects.create(tg_user_id=990002, username='svc_test_two')
        same_delete_at = timezone.now() + timezone.timedelta(days=10)
        assets = []
        for public_ip, user, label in [
            ('5.5.5.71', second_user, 'second-a'),
            ('5.5.5.72', self.user, 'first-a'),
            ('5.5.5.73', second_user, 'second-b'),
            ('5.5.5.74', self.user, 'first-b'),
        ]:
            assets.append(CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'sort-user-group-{label}',
                instance_id=f'sort-user-group-{label}',
                public_ip=public_ip,
                status=CloudAsset.STATUS_RUNNING,
                is_active=True,
                actual_expires_at=same_delete_at,
            ))
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_sort_user_group', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': 1})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        rows = [
            item for item in data['shutdown_items']
            if item.get('asset_id') in {asset.id for asset in assets}
        ]

        grouped_user_ids = [item['user_id'] for item in rows]
        self.assertEqual(len(grouped_user_ids), 4)
        self.assertIn(grouped_user_ids, [
            [self.user.id, self.user.id, second_user.id, second_user.id],
            [second_user.id, second_user.id, self.user.id, self.user.id],
        ])

    def test_lifecycle_plans_move_deleted_unattached_ip_active_row_to_history(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='completed-unattached-ip-active-row',
            public_ip='5.5.5.64',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        call_command('refresh_lifecycle_plans', limit=20)
        self.assertTrue(CloudLifecyclePlan.objects.filter(
            plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
            asset=asset,
            data_group='active',
        ).exists())
        asset.status = CloudAsset.STATUS_DELETED
        asset.is_active = False
        asset.provider_status = '已删除'
        asset.note = '固定 IP 已释放'
        asset.save(update_fields=['status', 'is_active', 'provider_status', 'note', 'updated_at'])
        call_command('refresh_lifecycle_plans', limit=20)

        self.assertTrue(CloudLifecyclePlan.objects.filter(
            plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
            asset=asset,
            data_group='history',
        ).exists())
        self.assertFalse(CloudLifecyclePlan.objects.filter(
            plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
            asset=asset,
            data_group='active',
        ).exists())

        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_ip_active_to_history', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        history_rows = [
            item for item in data['ip_delete_items']
            if item.get('asset_id') == asset.id and item.get('is_history')
        ]
        active_rows = [
            item for item in data['ip_delete_items']
            if item.get('asset_id') == asset.id and not item.get('is_history')
        ]

        self.assertFalse(active_rows)
        self.assertTrue(history_rows)
        self.assertFalse(CloudLifecyclePlan.objects.filter(
            plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
            asset=asset,
            data_group='active',
        ).exists())
        self.assertTrue(CloudLifecyclePlan.objects.filter(
            plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
            asset=asset,
            data_group='history',
        ).exists())
        self.assertGreaterEqual(data['ip_delete_history_count'], 1)

    def test_lifecycle_plans_include_future_server_plan_item(self):
        delete_at = timezone.now() + timezone.timedelta(days=9)
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-FUTURE-SERVER-PLAN-1',
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
            status='suspended',
            public_ip='52.77.18.249',
            service_expires_at=timezone.now() + timezone.timedelta(days=6),
            suspend_at=timezone.now() + timezone.timedelta(days=8),
            delete_at=delete_at,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='future-server-plan-asset',
            instance_id='future-server-plan-asset',
            public_ip=order.public_ip,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        call_command('refresh_lifecycle_plans', limit=1000)
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_future_server_visible', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        rows = [item for item in data['shutdown_items'] if item.get('order_id') == order.id]

        self.assertTrue(rows)
        self.assertEqual(rows[0]['queue_status'], 'scheduled_future')
        self.assertEqual(rows[0]['plan_state_label'], '已排期')

    def test_lifecycle_plans_compute_orphan_server_delete_after_suspend_window(self):
        SiteConfig.set('cloud_suspend_after_days', '3')
        SiteConfig.set('cloud_suspend_time', '17:00')
        SiteConfig.set('cloud_delete_after_days', '3')
        SiteConfig.set('cloud_delete_time', '18:00')
        expires_at = timezone.localtime(timezone.now()).replace(hour=16, minute=50, second=33, microsecond=0)
        if expires_at <= timezone.now():
            expires_at += timezone.timedelta(days=1)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='orphan-server-lifecycle-offset',
            public_ip='52.77.18.251',
            instance_id='i-orphan-server-lifecycle-offset',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_orphan_lifecycle_offset', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        row = next(item for item in data['due_items'] if item.get('asset_id') == asset.id)
        suspend_at = parse_datetime(row['suspend_at'])
        delete_at = parse_datetime(row['delete_at'])

        self.assertEqual(suspend_at, expires_at + timezone.timedelta(days=3, minutes=10, seconds=-33))
        self.assertEqual(delete_at, suspend_at + timezone.timedelta(days=3, hours=1))
        self.assertGreater(delete_at, suspend_at)
        self.assertNotEqual(delete_at, expires_at)
        self.assertIn(timezone.localtime(delete_at).strftime('%Y-%m-%d %H:%M:%S'), row['execution_plan'])

    def test_orphan_server_not_due_until_computed_delete_time(self):
        SiteConfig.set('cloud_suspend_after_days', '3')
        SiteConfig.set('cloud_suspend_time', '17:00')
        SiteConfig.set('cloud_delete_after_days', '3')
        SiteConfig.set('cloud_delete_time', '18:00')
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='orphan-server-not-delete-at-expiry',
            public_ip='52.77.18.252',
            instance_id='i-orphan-server-not-delete-at-expiry',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        due_ids = {item.id for item in async_to_sync(_get_orphan_asset_delete_due)()}

        self.assertNotIn(asset.id, due_ids)

    def test_refresh_lifecycle_plans_command_populates_cloud_lifecycle_plan(self):
        order = CloudServerOrder.objects.create(
            order_no='CMD-LIFECYCLE-PLAN-1',
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
            status='suspended',
            public_ip='7.7.7.61',
            service_expires_at=timezone.now() - timezone.timedelta(days=2),
            suspend_at=timezone.now() - timezone.timedelta(days=1),
            delete_at=timezone.now() + timezone.timedelta(hours=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='cmd-lifecycle-plan-asset',
            instance_id='cmd-lifecycle-plan-asset',
            public_ip=order.public_ip,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        call_command('refresh_lifecycle_plans', limit=20)

        self.assertTrue(CloudLifecyclePlan.objects.filter(
            plan_kind=CloudLifecyclePlan.PLAN_KIND_ORPHAN_ASSET_DELETE,
            asset__order=order,
            data_group='active',
        ).exists())

    def test_refresh_lifecycle_plan_table_api_populates_cloud_lifecycle_plan(self):
        order = CloudServerOrder.objects.create(
            order_no='API-LIFECYCLE-REFRESH-1',
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
            status='suspended',
            public_ip='7.7.7.63',
            service_expires_at=timezone.now() - timezone.timedelta(days=2),
            suspend_at=timezone.now() - timezone.timedelta(days=1),
            delete_at=timezone.now() + timezone.timedelta(hours=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='api-lifecycle-refresh-asset',
            instance_id='api-lifecycle-refresh-asset',
            public_ip=order.public_ip,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_lifecycle_refresh', password='x', is_staff=True)
        request = self.factory.post('/api/admin/tasks/plans/refresh/', data=json.dumps({'limit': 20}), content_type='application/json')
        request.user = staff_user

        response = refresh_lifecycle_plan_table(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(CloudLifecyclePlan.objects.filter(
            plan_kind=CloudLifecyclePlan.PLAN_KIND_ORPHAN_ASSET_DELETE,
            asset__order=order,
            data_group='active',
        ).exists())

    def test_update_cloud_asset_expiry_refreshes_delete_plan_snapshot(self):
        old_expiry = timezone.now() - timezone.timedelta(days=10)
        new_expiry = timezone.now() - timezone.timedelta(days=1)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='expiry-refresh-delete-plan',
            instance_id='expiry-refresh-delete-plan',
            public_ip='7.7.7.64',
            actual_expires_at=old_expiry,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        call_command('refresh_lifecycle_plans', limit=20)
        row = CloudLifecyclePlan.objects.get(plan_kind=CloudLifecyclePlan.PLAN_KIND_ORPHAN_ASSET_DELETE, asset=asset, data_group='active')
        old_delete_at = row.delete_at
        staff_user = get_user_model().objects.create_user(username='staff_asset_expiry_refresh_plan', password='x', is_staff=True, is_superuser=True)
        request = self.factory.patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'actual_expires_at': new_expiry.isoformat()}),
            content_type='application/json',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(asset.actual_expires_at, new_expiry)
        self.assertNotEqual(row.delete_at, old_delete_at)
        self.assertEqual(row.service_expires_at, new_expiry)

    def test_update_unattached_ip_release_time_refreshes_delete_plan_snapshot(self):
        old_release_at = timezone.now() + timezone.timedelta(days=1)
        new_release_at = timezone.now() + timezone.timedelta(days=3)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-expiry-refresh-plan',
            public_ip='7.7.7.65',
            actual_expires_at=old_release_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=True,
        )
        call_command('refresh_lifecycle_plans', limit=20)
        row = CloudLifecyclePlan.objects.get(plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE, asset=asset, data_group='active')
        self.assertEqual(row.delete_at, old_release_at)
        staff_user = get_user_model().objects.create_user(username='staff_ip_expiry_refresh_plan', password='x', is_staff=True, is_superuser=True)
        request = self.factory.patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'actual_expires_at': new_release_at.isoformat()}),
            content_type='application/json',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(asset.actual_expires_at, new_release_at)
        self.assertEqual(row.delete_at, new_release_at)
        self.assertEqual(row.next_run_at, new_release_at)

    def test_refresh_notice_plans_command_populates_cloud_notice_plan(self):
        now = timezone.now()
        order = CloudServerOrder.objects.create(
            order_no='CMD-NOTICE-PLAN-1',
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
            public_ip='7.7.7.62',
            service_expires_at=now + timezone.timedelta(days=1),
            cloud_reminder_enabled=True,
        )
        self._create_auto_renew_asset(order)

        call_command('refresh_notice_plans', limit=20, future_limit=20, history_limit=20)

        self.assertTrue(CloudNoticePlan.objects.filter(notice_type='renew_notice', order=order, data_group='active').exists())

    def test_refresh_notice_plan_table_api_populates_cloud_notice_plan(self):
        now = timezone.now()
        order = CloudServerOrder.objects.create(
            order_no='API-NOTICE-REFRESH-1',
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
            public_ip='7.7.7.64',
            service_expires_at=now + timezone.timedelta(days=1),
            cloud_reminder_enabled=True,
        )
        self._create_auto_renew_asset(order)
        staff_user = get_user_model().objects.create_user(username='staff_api_notice_refresh', password='x', is_staff=True)
        request = self.factory.post('/api/admin/tasks/notices/refresh/', data=json.dumps({'limit': 20, 'future_limit': 20, 'history_limit': 20}), content_type='application/json')
        request.user = staff_user

        response = refresh_notice_plan_table(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(CloudNoticePlan.objects.filter(notice_type='renew_notice', order=order, data_group='active').exists())

    def test_notice_task_detail_uses_cloud_notice_plan_table(self):
        now = timezone.now()
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-PLAN-TABLE-RENEW-1',
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
            public_ip='7.7.7.71',
            service_expires_at=now + timezone.timedelta(days=1),
            cloud_reminder_enabled=True,
        )
        self._create_auto_renew_asset(order)
        staff_user = get_user_model().objects.create_user(username='staff_notice_plan_table', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/notices/', {'limit': 20, 'future_limit': 20, 'history_limit': 20})
        request.user = staff_user

        response = notice_task_detail(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        row = next(item for item in data['due_items'] if item.get('order_id') == order.id and item.get('notice_type') == 'renew_notice')
        self.assertEqual(row['ip'], '7.7.7.71')
        self.assertTrue(CloudNoticePlan.objects.filter(notice_type='renew_notice', order=order, data_group=CloudNoticePlan.DATA_GROUP_ACTIVE).exists())

    def test_notice_write_actions_require_superuser(self):
        staff_user = get_user_model().objects.create_user(username='staff_notice_write_blocked', password='x', is_staff=True)
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-WRITE-BLOCKED-1',
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
            public_ip='7.7.7.73',
        )
        log = CloudUserNoticeLog.objects.create(
            user=self.user,
            order=order,
            batch_id='notice-write-blocked-1',
            event_type='renew_notice_batch',
            target_chat_id=123456,
            order_no=order.order_no,
            ip=order.public_ip,
            is_batch=True,
            delivered=True,
            text_preview='到期提醒：测试权限拦截',
            extra={'order_ids': [order.id]},
        )

        switch_request = self.factory.post(
            '/api/admin/tasks/notices/switches/',
            data=json.dumps({'switches': [{'key': 'cloud_daily_expiry_summary_enabled', 'enabled': False}]}),
            content_type='application/json',
        )
        switch_request.user = staff_user
        self.assertEqual(update_notice_switches(switch_request).status_code, 403)

        text_request = self.factory.post(
            '/api/admin/tasks/notices/text/',
            data=json.dumps({'notice_event': 'renew_notice', 'order_ids': [order.id], 'notice_text': 'blocked'}),
            content_type='application/json',
        )
        text_request.user = staff_user
        self.assertEqual(update_notice_plan_text(text_request).status_code, 403)

        delete_request = self.factory.post(f'/api/admin/tasks/notices/history/{log.id}/delete/')
        delete_request.user = staff_user
        self.assertEqual(delete_notice_history(delete_request, str(log.id)).status_code, 403)
        self.assertTrue(CloudUserNoticeLog.objects.filter(id=log.id).exists())

    def test_delete_notice_history_removes_cloud_notice_plan_history_row(self):
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-PLAN-HISTORY-DELETE-1',
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
            public_ip='7.7.7.72',
        )
        log = CloudUserNoticeLog.objects.create(
            user=self.user,
            order=order,
            batch_id='notice-batch-delete-1',
            event_type='renew_notice_batch',
            target_chat_id=123456,
            order_no=order.order_no,
            ip=order.public_ip,
            is_batch=True,
            delivered=True,
            text_preview='到期提醒：测试历史删除',
            extra={'order_ids': [order.id], 'send_attempts': [{'channel': 'bot', 'channel_label': 'Bot', 'ok': True, 'error': ''}]},
        )
        staff_user = get_user_model().objects.create_user(username='staff_notice_plan_history_delete', password='x', is_staff=True, is_superuser=True)
        sync_request = self.factory.get('/api/admin/tasks/notices/', {'limit': 20, 'future_limit': 20, 'history_limit': 20})
        sync_request.user = staff_user
        sync_response = notice_task_detail(sync_request)
        self.assertEqual(sync_response.status_code, 200)
        self.assertTrue(CloudNoticePlan.objects.filter(notice_type='renew_notice', data_group=CloudNoticePlan.DATA_GROUP_HISTORY, log_id=log.id).exists())

        request = self.factory.post(f'/api/admin/tasks/notices/history/{log.id}/delete/')
        request.user = staff_user
        response = delete_notice_history(request, str(log.id))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CloudNoticePlan.objects.filter(notice_type='renew_notice', data_group=CloudNoticePlan.DATA_GROUP_HISTORY, log_id=log.id).exists())

    def test_cloud_ip_query_keyboard_limits_non_owner_to_renewal(self):
        from bot.keyboards import cloud_ip_query_result

        markup = cloud_ip_query_result([], [{
            'ip': '4.4.4.44',
            'order_id': 123,
            'asset_id': 0,
            'can_change_ip': False,
            'can_reinit': False,
            'can_config': False,
            'can_support': False,
        }], include_start=False, include_reinit=False)
        labels = [button.text for row in markup.inline_keyboard for button in row]

        self.assertIn('🔄 续费IP', labels)
        self.assertNotIn('🌐 更换IP', labels)
        self.assertNotIn('🛠 重新安装', labels)
        self.assertNotIn('⚙️ 修改配置', labels)
        self.assertNotIn('⚡ 开启自动续费', labels)
        self.assertNotIn('⛔ 关闭自动续费', labels)
        self.assertNotIn('👩‍💻 联系客服', labels)

    def test_lifecycle_aws_sync_scans_all_regions_without_env_region(self):
        aws_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-lifecycle-all-region-sync',
            external_account_id='acct-aws-lifecycle-all',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        aliyun_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-lifecycle-region-sync',
            external_account_id='acct-aliyun-lifecycle',
            access_key='ak',
            secret_key='sk',
            region_hint='cn-hongkong',
            is_active=True,
        )
        calls = []

        def fake_call_command(command_name, **kwargs):
            calls.append((command_name, kwargs))

        SiteConfig.set('cloud_asset_sync_next_account_cursor', '')
        with patch.dict(os.environ, {'AWS_REGION': '', 'ALIYUN_REGION': ''}, clear=False), patch('cloud.lifecycle.call_command', side_effect=fake_call_command):
            async_to_sync(sync_server_status_tick)()
            async_to_sync(sync_server_status_tick)()

        aliyun_call = calls[0]
        aws_call = calls[1]
        self.assertEqual(aliyun_call[0], 'sync_aliyun_assets')
        self.assertEqual(aliyun_call[1]['account_id'], str(aliyun_account.id))
        self.assertEqual(aliyun_call[1]['region'], 'cn-hongkong')
        self.assertEqual(aws_call[0], 'sync_aws_assets')
        self.assertEqual(aws_call[1]['account_id'], str(aws_account.id))
        self.assertNotIn('region', aws_call[1])

    def test_lifecycle_sync_rotates_one_active_account_per_tick(self):
        first = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-lifecycle-rotate-1',
            external_account_id='acct-rotate-1',
            access_key='ak1',
            secret_key='sk1',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        second = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-lifecycle-rotate-2',
            external_account_id='acct-rotate-2',
            access_key='ak2',
            secret_key='sk2',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        SiteConfig.set('cloud_asset_sync_next_account_cursor', '')
        calls = []

        def fake_call_command(command_name, **kwargs):
            calls.append((command_name, kwargs))

        with patch.dict(os.environ, {'AWS_REGION': ''}, clear=False), patch('cloud.lifecycle.call_command', side_effect=fake_call_command):
            async_to_sync(sync_server_status_tick)()
            async_to_sync(sync_server_status_tick)()

        self.assertEqual([item[1]['account_id'] for item in calls], [str(first.id), str(second.id)])
        self.assertTrue(all(item[0] == 'sync_aws_assets' for item in calls))
        self.assertTrue(all('region' not in item[1] for item in calls))

    def test_delete_cloud_asset_only_removes_asset_record(self):
        order = CloudServerOrder.objects.create(
            order_no='DELETE-ASSET-ONLY-1',
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
            instance_id='i-delete-asset-only',
            provider_resource_id='res-delete-asset-only',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='delete-asset-only',
            public_ip=order.public_ip,
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=order.service_expires_at,
            price='19.00',
            status=CloudAsset.STATUS_RUNNING,
        )
        server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='delete-asset-only-server',
            public_ip=order.public_ip,
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=Server.STATUS_RUNNING,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_delete_only', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post(f'/api/dashboard/cloud-assets/{asset.id}/delete/')
        request.user = staff_user

        response = delete_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        order.refresh_from_db()
        self.assertFalse(CloudAsset.objects.filter(id=asset.id).exists())
        self.assertFalse(Server.objects.filter(id=server.id).exists())
        self.assertEqual(order.status, 'completed')
        self.assertIsNone(order.public_ip)
        self.assertIsNone(order.previous_public_ip)
        self.assertEqual(order.instance_id, '')
        self.assertEqual(order.provider_resource_id, '')
        self.assertEqual(order.static_ip_name, '')
        self.assertEqual(order.mtproxy_port, 0)
        self.assertEqual(order.mtproxy_link, '')
        self.assertEqual(order.proxy_links, [])
        self.assertEqual(payload['data']['removed_servers'], 1)
        self.assertEqual(payload['data']['order_status_changed'], True)
        self.assertTrue(CloudIpLog.objects.filter(order=order, note__contains='后续云同步按全新资源处理').exists())
        self.assertTrue(CloudIpLog.objects.filter(order=order, asset_name='delete-asset-only', event_type=CloudIpLog.EVENT_DELETED, note__contains='后台手动删除代理列表记录').exists())
        from cloud.management.commands.sync_aws_assets import _resolve_order_for_ip
        self.assertIsNone(_resolve_order_for_ip('8.8.8.8'))

    def test_delete_cloud_asset_also_removes_residual_server_record(self):
        order = CloudServerOrder.objects.create(
            order_no='DELETE-ASSET-RESIDUAL-1',
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
            status='deleted',
            public_ip=None,
            previous_public_ip='8.8.4.4',
            instance_id='i-delete-asset-residual',
            provider_resource_id='res-delete-asset-residual',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='delete-asset-residual',
            public_ip=None,
            previous_public_ip='8.8.4.4',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=order.service_expires_at,
            price='19.00',
            status=CloudAsset.STATUS_DELETED,
            provider_status='云上未找到实例/IP',
            is_active=False,
            note='状态: 云上未找到实例/IP',
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='delete-asset-residual-server',
            public_ip=None,
            previous_public_ip='8.8.4.4',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=Server.STATUS_DELETED,
            provider_status='云上未找到实例/IP',
            is_active=False,
            note='状态: 云上未找到实例/IP',
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_delete_residual', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post(f'/api/dashboard/cloud-assets/{asset.id}/delete/')
        request.user = staff_user

        response = delete_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertFalse(CloudAsset.objects.filter(id=asset.id).exists())
        self.assertFalse(Server.objects.filter(id=server.id).exists())
        order.refresh_from_db()
        self.assertEqual(payload['data']['removed_servers'], 1)
        self.assertEqual(payload['data']['removed_server_ids'], [server.id])
        self.assertEqual(payload['data']['order_status_changed'], True)
        self.assertIsNone(order.public_ip)
        self.assertIsNone(order.previous_public_ip)
        self.assertEqual(order.instance_id, '')
        self.assertEqual(order.provider_resource_id, '')
        self.assertTrue(CloudIpLog.objects.filter(order=order, note__contains='后台手动删除代理列表记录').exists())
