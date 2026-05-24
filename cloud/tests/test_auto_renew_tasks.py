from .common import *


class CloudServerAutoRenewTasksMixin:
    def test_auto_renew_task_detail_includes_due_retry_and_fallback_items(self):
        due_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-DUE-1',
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
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(hours=12),
            auto_renew_enabled=True,
        )
        retry_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-RETRY-1',
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
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(days=2),
            auto_renew_enabled=True,
        )
        fallback_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-FALLBACK-1',
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
            public_ip='10.0.0.3',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(hours=1),
            auto_renew_enabled=True,
        )
        resolved_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-RESOLVED-1',
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
            public_ip='10.0.0.4',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(days=20),
            auto_renew_enabled=True,
        )
        deleted_asset_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-DELETED-ASSET-1',
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
            public_ip='10.0.0.5',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(hours=3),
            auto_renew_enabled=True,
        )
        for order in [due_order, retry_order, fallback_order, resolved_order]:
            self._create_auto_renew_asset(order)
        self._create_auto_renew_asset(deleted_asset_order, status=CloudAsset.STATUS_DELETED)
        CloudAutoRenewPatrolLog.objects.create(
            order=retry_order,
            user=self.user,
            batch_id='failed-batch-1',
            order_no=retry_order.order_no,
            ip=retry_order.public_ip,
            provider=retry_order.provider,
            user_display_name='svc_test',
            username_label='@svc_test',
            tg_user_id=self.user.tg_user_id,
            is_success=False,
            failure_reason='余额不足',
        )
        CloudAutoRenewPatrolLog.objects.create(
            order=resolved_order,
            user=self.user,
            batch_id='resolved-batch-1',
            order_no=resolved_order.order_no,
            ip=resolved_order.public_ip,
            provider=resolved_order.provider,
            user_display_name='svc_test',
            username_label='@svc_test',
            tg_user_id=self.user.tg_user_id,
            is_success=False,
            failure_reason='曾经失败',
        )
        CloudAutoRenewPatrolLog.objects.create(
            order=resolved_order,
            user=self.user,
            batch_id='resolved-batch-2',
            order_no=resolved_order.order_no,
            ip=resolved_order.public_ip,
            provider=resolved_order.provider,
            user_display_name='svc_test',
            username_label='@svc_test',
            tg_user_id=self.user.tg_user_id,
            is_success=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_detail', password='x', is_staff=True)
        request = RequestFactory().get('/api/dashboard/tasks/auto-renew/')
        request.user = staff_user

        async def fake_get_due_orders():
            return {'auto_renew': [due_order]}

        with patch('cloud.api._get_due_orders', side_effect=fake_get_due_orders):
            response = auto_renew_task_detail(request)

        payload = json.loads(response.content)
        data = payload.get('data') or payload
        due_items = data['due_items']
        queue_status_map = {item['order_no']: item['queue_status'] for item in due_items}

        self.assertEqual(response.status_code, 200)
        self.assertEqual(queue_status_map[due_order.order_no], 'due_now')
        self.assertEqual(queue_status_map[retry_order.order_no], 'retry_failed')
        self.assertEqual(queue_status_map[fallback_order.order_no], 'fallback_retry')
        self.assertNotIn(resolved_order.order_no, queue_status_map)
        self.assertNotIn(deleted_asset_order.order_no, queue_status_map)
        retry_item = next(item for item in due_items if item['order_no'] == retry_order.order_no)
        self.assertEqual(retry_item['last_failure_reason'], '余额不足')

    def test_auto_renew_detail_keeps_valid_order_without_asset(self):
        due_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-NO-ASSET-1',
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
            public_ip='10.0.9.1',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(hours=12),
            auto_renew_enabled=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_no_asset', password='x', is_staff=True)
        request = RequestFactory().get('/api/dashboard/tasks/auto-renew/')
        request.user = staff_user

        async def fake_get_due_orders():
            return {'auto_renew': [due_order]}

        with patch('cloud.api._get_due_orders', side_effect=fake_get_due_orders):
            response = auto_renew_task_detail(request)

        payload = json.loads(response.content)
        data = payload.get('data') or payload
        queue_status_map = {item['order_no']: item['queue_status'] for item in data['due_items']}

        self.assertEqual(response.status_code, 200)
        self.assertEqual(queue_status_map[due_order.order_no], 'due_now')

    def test_run_auto_renew_tasks_executes_due_retry_and_fallback_queue(self):
        due_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-RUN-DUE-1',
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
            public_ip='10.0.1.1',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(hours=8),
            auto_renew_enabled=True,
        )
        retry_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-RUN-RETRY-1',
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
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
            auto_renew_enabled=True,
        )
        fallback_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-RUN-FALLBACK-1',
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
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(hours=2),
            auto_renew_enabled=True,
        )
        for order in [due_order, retry_order, fallback_order]:
            self._create_auto_renew_asset(order)
        CloudAutoRenewPatrolLog.objects.create(
            order=retry_order,
            user=self.user,
            batch_id='failed-batch-2',
            order_no=retry_order.order_no,
            ip=retry_order.public_ip,
            provider=retry_order.provider,
            user_display_name='svc_test',
            username_label='@svc_test',
            tg_user_id=self.user.tg_user_id,
            is_success=False,
            failure_reason='上次失败',
        )
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_run', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post('/api/dashboard/tasks/auto-renew/run/', data='{}', content_type='application/json')
        request.user = staff_user

        async def fake_get_due_orders():
            return {'auto_renew': [due_order]}

        def fake_run_auto_renew(order_id):
            order = CloudServerOrder.objects.get(id=order_id)
            if order_id == retry_order.id:
                return None, '余额不足', {'currency': 'USDT', 'amount': None}
            return order, None, {'currency': 'USDT', 'amount': Decimal('19.00'), 'before': Decimal('100.00'), 'after': Decimal('81.00'), 'payer_user_id': self.user.id}

        with patch('cloud.api._get_due_orders', side_effect=fake_get_due_orders), patch('cloud.api._run_auto_renew', new=fake_run_auto_renew):
            response = run_auto_renew_tasks(request)

        payload = json.loads(response.content)
        data = payload.get('data') or payload
        items = data['items']
        item_map = {item['order_no']: item for item in items}

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['success_count'], 2)
        self.assertEqual(data['failure_count'], 1)
        self.assertTrue(item_map[due_order.order_no]['ok'])
        self.assertFalse(item_map[retry_order.order_no]['ok'])
        self.assertEqual(item_map[retry_order.order_no]['error'], '余额不足')
        self.assertEqual(item_map[fallback_order.order_no]['queue_status'], 'fallback_retry')
        self.assertEqual(CloudAutoRenewPatrolLog.objects.filter(batch_id=data['batch_id']).count(), 3)

    def test_run_auto_renew_order_executes_single_order(self):
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-SINGLE-1',
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
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(hours=4),
            auto_renew_enabled=True,
        )
        self._create_auto_renew_asset(order)
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_single', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post(f'/api/dashboard/tasks/auto-renew/orders/{order.id}/run/', data='{}', content_type='application/json')
        request.user = staff_user

        def fake_run_auto_renew(order_id):
            renewed = CloudServerOrder.objects.get(id=order_id)
            return renewed, None, {'currency': 'USDT', 'amount': Decimal('19.00'), 'before': Decimal('50.00'), 'after': Decimal('31.00'), 'payer_user_id': self.user.id}

        with patch('cloud.api._run_auto_renew', new=fake_run_auto_renew):
            response = run_auto_renew_order(request, order.id)

        payload = json.loads(response.content)
        data = payload.get('data') or payload

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['queue_status'], 'manual_single')
        self.assertTrue(data['items'][0]['ok'])
        self.assertTrue(CloudAutoRenewPatrolLog.objects.filter(batch_id=data['batch_id'], order=order).exists())

    def test_update_cloud_asset_refreshes_unattached_ip_delete_plan(self):
        old_due_at = timezone.now() + timezone.timedelta(days=2)
        old_ip_recycle_at = timezone.now() + timezone.timedelta(days=2)
        order = CloudServerOrder.objects.create(
            order_no='UNATTACHED-REFRESH-PLAN-1',
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
            public_ip='10.9.0.9',
            previous_public_ip='10.9.0.9',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=10),
            delete_at=timezone.now() - timezone.timedelta(days=7),
            ip_recycle_at=old_ip_recycle_at,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            order=order,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='refresh-unattached-ip-asset',
            provider_resource_id='aws-static-ip-refresh-1',
            public_ip='10.9.0.9',
            actual_expires_at=old_due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            order=order,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='refresh-unattached-ip-server',
            provider_resource_id='aws-static-ip-refresh-1',
            public_ip='10.9.0.9',
            expires_at=old_due_at,
            status=Server.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(username='staff_refresh_unattached_plan', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'note': '未附加固定IP\n人工刷新删除计划'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        asset.refresh_from_db()
        server.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertGreater(asset.actual_expires_at, old_due_at)
        self.assertEqual(server.expires_at, asset.actual_expires_at)
        self.assertEqual(order.ip_recycle_at, asset.actual_expires_at)

    def test_update_cloud_asset_rebinds_unattached_ip_to_instance(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='rebound-ip-asset',
            provider_resource_id='aws-static-ip-manual-1',
            public_ip='10.9.0.1',
            actual_expires_at=timezone.now() + timezone.timedelta(days=10),
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
            server_name='rebound-ip-server',
            provider_resource_id='aws-static-ip-manual-1',
            public_ip='10.9.0.1',
            expires_at=asset.actual_expires_at,
            status=Server.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(username='staff_rebound_manual', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'instance_id': 'i-rebound-manual-1'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(asset.instance_id, 'i-rebound-manual-1')
        self.assertEqual(asset.provider_status, '已重新绑定实例-待人工添加时间')
        self.assertIsNone(asset.actual_expires_at)
        self.assertTrue(asset.is_active)
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.note, '未附加固定IP')
        self.assertEqual(server.instance_id, 'i-rebound-manual-1')
        self.assertIsNone(server.expires_at)
        self.assertEqual(server.provider_status, '已重新绑定实例-待人工添加时间')
        self.assertTrue(server.is_active)
        self.assertEqual(server.status, Server.STATUS_RUNNING)

    def test_system_note_updates_preserve_manual_primary_record_notes(self):
        from cloud.services import _update_order_primary_records

        order = CloudServerOrder.objects.create(
            order_no='NOTE-APPEND-PRIMARY',
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
            provision_note='订单旧备注',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            public_ip='10.9.9.1',
            note='资产人工备注',
        )
        server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            public_ip='10.9.9.1',
            note='服务器人工备注',
        )

        _update_order_primary_records(order, asset_updates={'note': '系统追加备注'}, server_updates={'note': '系统追加备注'})

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(asset.note, '资产人工备注')
        self.assertEqual(server.note, '服务器人工备注')

    def test_sync_cloud_asset_user_binding_uses_asset_name_tg_id(self):
        user = TelegramUser.objects.create(
            tg_user_id=21989077,
            username='syira,hashyule111,sy168',
            first_name='蜗牛',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='20260522-21989077-15-o877',
            public_ip='10.9.9.10',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        resolved = sync_cloud_asset_user_binding(asset)

        asset.refresh_from_db()
        self.assertEqual(resolved.id, user.id)
        self.assertEqual(asset.user_id, user.id)

    def test_toggle_auto_renew_creates_operation_order_for_bound_asset_without_order(self):
        user = TelegramUser.objects.create(
            tg_user_id=21989078,
            username='auto_renew_user',
            first_name='自动续费用户',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='20260522-21989078-15-o878',
            instance_id='i-auto-renew-test',
            public_ip='10.9.9.11',
            actual_expires_at=timezone.now() + timezone.timedelta(days=10),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        sync_cloud_asset_user_binding(asset)
        asset.refresh_from_db()
        self.assertEqual(asset.user_id, user.id)
        order, err = async_to_sync(ensure_cloud_asset_operation_order)(asset.id, user.id, True)
        self.assertIsNone(err)
        self.assertIsNotNone(order)

        updated = async_to_sync(set_cloud_server_auto_renew_admin)(order.id, True)

        asset.refresh_from_db()
        self.assertTrue(updated.auto_renew_enabled)
        self.assertEqual(asset.order_id, order.id)

    def test_manual_cloud_asset_note_edit_still_overwrites(self):
        order = CloudServerOrder.objects.create(
            order_no='NOTE-MANUAL-OVERWRITE',
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
            public_ip='10.9.9.2',
            status='completed',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            public_ip='10.9.9.2',
            status=CloudAsset.STATUS_RUNNING,
            note='旧人工备注',
            is_active=True,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            public_ip='10.9.9.2',
            status=Server.STATUS_RUNNING,
            note='旧服务器备注',
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_manual_note_overwrite', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'note': '人工改后的备注'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(asset.note, '人工改后的备注')
        self.assertEqual(server.note, '人工改后的备注')

    def test_sync_missing_confirmation_note_preserves_existing_note(self):
        from cloud.sync_safety import mark_missing_confirmation_pending

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            public_ip='10.9.9.3',
            provider_status='running',
            note='保留人工备注',
        )

        with patch('cloud.sync_safety.get_missing_confirmation_threshold', return_value=2):
            count, threshold = mark_missing_confirmation_pending(
                asset,
                old_public_ip='10.9.9.3',
                now_iso='2026-05-08T00:00:00+08:00',
                provider_status='云上未找到实例/IP',
                pending_status='云上未找到实例/IP-待确认',
            )

        self.assertEqual((count, threshold), (1, 2))
        self.assertEqual(asset.note, '保留人工备注')
        self.assertIn('云上未找到实例/IP-待确认', asset.provider_status)
        self.assertIn('[missing_sync_count:1]', asset.provider_status)

    def test_sync_missing_delete_threshold_is_at_least_five(self):
        with patch('cloud.sync_safety.get_runtime_config', return_value='3'):
            self.assertEqual(get_missing_confirmation_threshold(), 5)
        with patch('cloud.sync_safety.get_runtime_config', return_value='0'):
            self.assertEqual(get_missing_confirmation_threshold(), 5)
        with patch('cloud.sync_safety.get_runtime_config', return_value='7'):
            self.assertEqual(get_missing_confirmation_threshold(), 7)

    def test_sync_missing_confirmation_requires_interval(self):
        from cloud.sync_safety import mark_missing_confirmation_pending, missing_confirmation_count

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            public_ip='10.9.9.4',
            provider_status='running',
            note='保留人工备注',
        )

        first_count, _ = mark_missing_confirmation_pending(
            asset,
            old_public_ip='10.9.9.4',
            now_iso='2026-05-08T00:00:00+08:00',
            provider_status='云上未找到实例/IP',
            pending_status='云上未找到实例/IP-待确认',
        )
        second_count, _ = mark_missing_confirmation_pending(
            asset,
            old_public_ip='10.9.9.4',
            now_iso='2026-05-08T00:01:00+08:00',
            provider_status='云上未找到实例/IP',
            pending_status='云上未找到实例/IP-待确认',
        )

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 1)
        self.assertEqual(asset.note, '保留人工备注')
        self.assertEqual(missing_confirmation_count(asset.provider_status), 1)

    def test_unattached_ip_delete_items_expose_missing_confirmation_state(self):
        from cloud.sync_safety import mark_missing_confirmation_pending

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='confirming-unattached-static-ip',
            public_ip='5.5.5.22',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        mark_missing_confirmation_pending(
            asset,
            old_public_ip='5.5.5.22',
            now_iso='2026-05-08T00:00:00+08:00',
            provider_status='云上未找到实例/IP',
            pending_status='云上未找到实例/IP-待确认',
        )
        asset.save(update_fields=['provider_status', 'updated_at'])

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('public_ip') == '5.5.5.22' and not item.get('is_history'))

        self.assertEqual(row['missing_confirm_count'], 1)
        self.assertGreaterEqual(row['missing_confirm_threshold'], 5)
        self.assertGreaterEqual(row['missing_confirm_remaining'], 4)
        self.assertEqual(row['missing_confirm_interval_minutes'], 60)
        self.assertTrue(row['missing_confirm_checked_at'])
        self.assertTrue(row['missing_confirm_next_check_at'])
        self.assertIn('missing_confirming', row.get('quality_flags') or [])
        self.assertIn('缺失确认 1/', row.get('quality_label') or '')

    def test_lifecycle_plans_unattached_ip_show_confirmation_progress_in_state_and_note(self):
        from cloud.sync_safety import mark_missing_confirmation_pending

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='confirming-unattached-static-ip-lifecycle',
            public_ip='5.5.5.24',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        mark_missing_confirmation_pending(
            asset,
            old_public_ip='5.5.5.24',
            now_iso='2026-05-08T00:00:00+08:00',
            provider_status='云上未找到实例/IP',
            pending_status='云上未找到实例/IP-待确认',
        )
        asset.save(update_fields=['provider_status', 'updated_at'])

        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_unattached_confirm_progress', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        row = next(item for item in data['ip_delete_items'] if item.get('public_ip') == '5.5.5.24' and not item.get('is_history'))

        self.assertEqual(row['resource_state_label'], '云上缺失待确认（第1/5次）')
        self.assertIn('第1/5次删除确认', row['display_note'])
        self.assertIn('第1/5次删除确认', row['blocked_reason'])

    def test_lifecycle_plans_unattached_ip_show_delete_attempt_in_state_and_note(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='attempt-unattached-static-ip-lifecycle',
            public_ip='5.5.5.25',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            provider_status='未附加固定IP',
            note=(
                '未附加固定IP\n'
                '未附加固定IP到期，AWS 固定 IP 真实释放失败: first\n'
                '未附加固定IP到期，AWS 固定 IP 真实释放失败: second'
            ),
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )

        items = _unattached_ip_delete_items(limit=20)
        direct_row = next(item for item in items if item.get('asset_id') == asset.id and not item.get('is_history'))
        self.assertEqual(direct_row['delete_attempt_count'], 2)
        self.assertEqual(direct_row['delete_next_attempt'], 3)
        self.assertEqual(direct_row['delete_attempt_label'], '已尝试2次，待第3次删除')

        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_unattached_delete_attempt', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        row = next(item for item in data['ip_delete_items'] if item.get('asset_id') == asset.id and not item.get('is_history'))

        self.assertIn('已尝试2次，待第3次删除', row['resource_state_label'])
        self.assertIn('删除次数：已尝试2次，待第3次删除', row['display_note'])

    def test_lifecycle_plans_read_cached_table_after_initial_refresh(self):
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='cached-unattached-static-ip-lifecycle',
            public_ip='5.5.5.26',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_cached_table', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
        request.user = staff_user
        first_response = lifecycle_plans(request)
        self.assertEqual(json.loads(first_response.content)['data']['cache_mode'], 'refreshed')

        with patch('bot.api._sync_lifecycle_plan_table') as sync_mock:
            second_request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
            second_request.user = staff_user
            second_response = lifecycle_plans(second_request)

        sync_mock.assert_not_called()
        self.assertEqual(json.loads(second_response.content)['data']['cache_mode'], 'cached')

    def test_lifecycle_plan_counts_match_proxy_list_assets(self):
        active_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='lifecycle-count-active',
            external_account_id='acct-lifecycle-count-active',
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        inactive_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='lifecycle-count-disabled',
            external_account_id='acct-lifecycle-count-disabled',
            access_key='C' * 20,
            secret_key='D' * 40,
            is_active=False,
        )
        server_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=active_account,
            account_label=cloud_account_label(active_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='proxy-count-server',
            instance_id='proxy-count-server',
            public_ip='5.5.5.41',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=timezone.now() - timezone.timedelta(days=7),
        )
        ip_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=active_account,
            account_label=cloud_account_label(active_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='proxy-count-static-ip',
            public_ip='5.5.5.42',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        aliyun_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            user=self.user,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            asset_name='proxy-count-aliyun',
            instance_id='i-proxy-count-aliyun',
            public_ip='5.5.5.43',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=inactive_account,
            account_label=cloud_account_label(inactive_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='proxy-count-disabled-account',
            instance_id='proxy-count-disabled-account',
            public_ip='5.5.5.44',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_visible_counts', password='x', is_staff=True)
        list_request = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'page_size': '100', 'risk_status': 'all'})
        list_request.user = staff_user
        list_response = cloud_assets_list(list_request)
        list_payload = json.loads(list_response.content.decode('utf-8'))['data']
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': 1})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']

        self.assertEqual(list_payload['total'], 3)
        self.assertEqual(data['source_asset_count'], list_payload['total'])
        self.assertEqual(data['server_asset_count'], 2)
        self.assertEqual(data['unattached_ip_count'], 1)
        self.assertEqual(data['source_asset_count'], data['server_asset_count'] + data['unattached_ip_count'])
        self.assertTrue(any(item.get('asset_id') == server_asset.id for item in data['due_items'] + data['future_plan_items']))
        self.assertTrue(any(item.get('asset_id') == ip_asset.id for item in data['ip_delete_items']))
        aliyun_row = next(item for item in data['due_items'] + data['future_plan_items'] if item.get('asset_id') == aliyun_asset.id)
        self.assertEqual(aliyun_row['plan_state_label'], '只同步/自然释放')
        self.assertFalse(aliyun_row['should_execute'])

    def test_unattached_ip_delete_items_hide_confirmed_missing_ip(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='confirmed-missing-unattached-static-ip',
            public_ip='5.5.5.23',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP；状态: 云上未找到实例/IP [missing_sync_count:5]',
            note='人工备注',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
        )

        items = _unattached_ip_delete_items(limit=20)

        self.assertFalse(any(item.get('public_ip') == '5.5.5.23' for item in items))

    def test_sync_aws_missing_instance_requires_five_passes_before_delete(self):
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
            order_no='AWS-MISS-CONFIRM-1',
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
            public_ip='9.9.9.9',
            previous_public_ip='9.9.9.9',
            instance_id='i-aws-missing-confirm-1',
            provider_resource_id='res-aws-missing-confirm-1',
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
            asset_name='aws-missing-confirm-asset',
            public_ip='9.9.9.9',
            previous_public_ip='9.9.9.9',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=order.service_expires_at,
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
            server_name='aws-missing-confirm-server',
            public_ip='9.9.9.9',
            previous_public_ip='9.9.9.9',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        deleted = _mark_deleted_when_missing_in_aws(self.plan.region_code, set(), set(), DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(deleted, [])
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.provider_status, '云上未找到实例/IP-待确认')
        self.assertEqual(server.status, Server.STATUS_RUNNING)
        self.assertEqual(order.status, 'completed')

        with patch('cloud.sync_safety.get_missing_confirmation_interval_minutes', return_value=0):
            for _ in range(3):
                deleted = _mark_deleted_when_missing_in_aws(self.plan.region_code, set(), set(), DummyStdout())
                asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
                self.assertEqual(deleted, [])
                self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
                self.assertEqual(server.status, Server.STATUS_RUNNING)
                self.assertEqual(order.status, 'completed')

            deleted = _mark_deleted_when_missing_in_aws(self.plan.region_code, set(), set(), DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
        self.assertTrue(deleted)
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(server.status, Server.STATUS_DELETED)
        self.assertEqual(order.status, 'deleted')
