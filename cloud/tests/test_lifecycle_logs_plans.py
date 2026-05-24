from .common import *


class CloudServerLifecycleLogsPlansMixin:
    def test_manual_order_source_tags_support_multiple_labels_on_same_order(self):
        order = CloudServerOrder.objects.create(
            order_no='SRVMANUAL-MULTI-1',
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
            provision_note='后台人工编辑：人工编辑用户 old -> new；人工编辑价格 19.00 -> 29.00。',
        )

        tags = _cloud_order_source_tags(order)

        self.assertEqual(
            [item[0] for item in tags],
            ['manual_owner_change', 'manual_price_change'],
        )
        self.assertEqual(
            [item[1] for item in tags],
            ['人工改用户', '人工改价格'],
        )

    def test_shutdown_log_items_skip_assets_hidden_from_cloud_asset_list(self):
        inactive_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='inactive-shutdown',
            external_account_id='acct-shutdown-inactive',
            access_key='ak',
            secret_key='sk',
            is_active=False,
        )
        active_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='active-shutdown',
            external_account_id='acct-shutdown-active',
            access_key='ak2',
            secret_key='sk2',
            is_active=True,
        )
        old_expiry = timezone.now() + timezone.timedelta(days=3)
        hidden_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=inactive_account,
            account_label='acct-shutdown-inactive',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='hidden-shutdown-asset',
            public_ip='6.6.6.6',
            actual_expires_at=old_expiry,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        visible_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=active_account,
            account_label='acct-shutdown-active',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-shutdown-asset',
            public_ip='6.6.6.7',
            actual_expires_at=old_expiry,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        items = _shutdown_log_items(limit=20)
        asset_ids = {item.get('asset_id') for item in items}

        self.assertIn(visible_asset.id, asset_ids)
        self.assertNotIn(hidden_asset.id, asset_ids)

    def test_shutdown_log_items_prefer_order_lifecycle_schedule(self):
        expires_at = timezone.now() + timezone.timedelta(days=1)
        order = CloudServerOrder.objects.create(
            order_no='SHUTDOWN-SCHEDULE-ORDER-1',
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
            public_ip='6.6.6.8',
            service_started_at=timezone.now(),
            service_expires_at=expires_at,
        )
        custom_suspend_at = timezone.now() + timezone.timedelta(days=9)
        custom_delete_at = custom_suspend_at + timezone.timedelta(hours=2)
        CloudServerOrder.objects.filter(id=order.id).update(suspend_at=custom_suspend_at, delete_at=custom_delete_at)
        order.refresh_from_db()
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='shutdown-schedule-asset',
            public_ip='6.6.6.8',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        items = _shutdown_log_items(limit=20)
        row = next(item for item in items if item.get('order_id') == order.id)

        self.assertEqual(parse_datetime(row['suspend_at']), order.suspend_at)
        self.assertEqual(parse_datetime(row['delete_at']), order.delete_at)

    def test_cloud_ip_log_note_aggregates_into_single_ip_trace(self):
        expires_at = timezone.now() + timezone.timedelta(days=2)
        order = CloudServerOrder.objects.create(
            order_no='LOG-CONTEXT-ORDER-1',
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
            service_expires_at=expires_at,
            suspend_at=expires_at + timezone.timedelta(days=3),
            delete_at=expires_at + timezone.timedelta(days=4),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='log-context-asset',
            public_ip='8.8.8.8',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        first = record_cloud_ip_log(event_type=CloudIpLog.EVENT_CREATED, order=order, asset=asset, public_ip=None, note='开始创建，暂未分配IP')
        second = record_cloud_ip_log(event_type=CloudIpLog.EVENT_CREATED, order=order, asset=asset, public_ip='8.8.8.8', note='第一次创建')
        third = record_cloud_ip_log(event_type=CloudIpLog.EVENT_CREATED, order=order, asset=asset, public_ip='8.8.8.8', note='同秒重复创建')
        fourth = record_cloud_ip_log(event_type=CloudIpLog.EVENT_DELETED, order=order, asset=asset, previous_public_ip='8.8.8.8', public_ip=None, note='实例已删除')

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.id, third.id)
        self.assertEqual(first.id, fourth.id)
        self.assertEqual(CloudIpLog.objects.filter(public_ip='8.8.8.8').count(), 1)
        first.refresh_from_db()
        self.assertEqual(first.event_type, CloudIpLog.EVENT_DELETED)
        self.assertIn('IP：8.8.8.8', first.note)
        self.assertIn('用户：', first.note)
        self.assertIn('执行时间：', first.note)
        self.assertIn('到期时间：', first.note)
        self.assertIn('执行计划：', first.note)
        self.assertIn('执行内容：开始创建，暂未分配IP', first.note)
        self.assertIn('执行内容：第一次创建', first.note)
        self.assertIn('执行内容：同秒重复创建', first.note)
        self.assertIn('执行内容：实例已删除', first.note)

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.id, third.id)
        self.assertEqual(CloudIpLog.objects.filter(public_ip='8.8.8.8').count(), 1)
        first.refresh_from_db()
        self.assertEqual(first.event_type, CloudIpLog.EVENT_DELETED)
        self.assertIn('IP：8.8.8.8', first.note)
        self.assertIn('用户：', first.note)
        self.assertIn('执行时间：', first.note)
        self.assertIn('到期时间：', first.note)
        self.assertIn('执行计划：', first.note)
        self.assertIn('执行内容：第一次创建', first.note)
        self.assertIn('执行内容：同秒重复创建', first.note)
        self.assertIn('执行内容：实例已删除', first.note)

    def test_cloud_ip_log_rebinds_trace_to_latest_replacement_order(self):
        expires_at = timezone.now() + timezone.timedelta(days=2)
        source_order = CloudServerOrder.objects.create(
            order_no='LOG-TRACE-REPLACE-SOURCE',
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
            service_expires_at=expires_at,
        )
        source_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=source_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='trace-source',
            public_ip='9.9.9.9',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        replacement_order = CloudServerOrder.objects.create(
            order_no='LOG-TRACE-REPLACE-NEW',
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
            public_ip='5.5.5.5',
            previous_public_ip='9.9.9.9',
            replacement_for=source_order,
            service_expires_at=expires_at,
        )
        replacement_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=replacement_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='trace-replacement',
            public_ip='5.5.5.5',
            previous_public_ip='9.9.9.9',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_PENDING,
            is_active=True,
        )

        source_log = record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_CREATED,
            order=source_order,
            asset=source_asset,
            public_ip='9.9.9.9',
            note='源订单创建成功',
        )
        replacement_log = record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_CREATED,
            order=replacement_order,
            asset=replacement_asset,
            public_ip='5.5.5.5',
            previous_public_ip='9.9.9.9',
            note='替换订单创建成功',
        )

        self.assertEqual(source_log.id, replacement_log.id)
        source_log.refresh_from_db()
        self.assertEqual(source_log.order_id, replacement_order.id)
        self.assertEqual(source_log.asset_id, replacement_asset.id)
        self.assertEqual(source_log.public_ip, '5.5.5.5')
        self.assertEqual(source_log.previous_public_ip, '9.9.9.9')
        self.assertIn('执行内容：源订单创建成功', source_log.note)
        self.assertIn('执行内容：替换订单创建成功', source_log.note)

    def test_shutdown_log_items_include_execution_detail_and_links(self):
        expires_at = timezone.now() - timezone.timedelta(hours=2)
        order = CloudServerOrder.objects.create(
            order_no='SHUTDOWN-DETAIL-ORDER-1',
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
            public_ip='7.7.7.7',
            service_expires_at=expires_at,
            provision_note='关机执行失败：余额不足',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='shutdown-detail-asset',
            public_ip='7.7.7.7',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            note='关机执行失败：余额不足',
        )

        items = _shutdown_log_items(limit=20)
        row = next(item for item in items if item.get('asset_id') == asset.id)

        self.assertEqual(row['order_detail_path'], f'/admin/cloud-orders/{order.id}')
        self.assertEqual(row['asset_detail_path'], f'/admin/cloud-assets/{asset.id}')
        self.assertIn('执行状态：', row['note'])
        self.assertIn('是否成功：失败', row['note'])
        self.assertIn('执行时间：', row['note'])
        self.assertIn('执行内容：', row['note'])
        self.assertIn('失败原因：关机执行失败：余额不足', row['note'])

    def test_unattached_ip_delete_items_use_asset_note(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unattached-shared-note',
            public_ip='5.5.5.31',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            provider_status='未附加固定IP',
            note='代理列表备注：删除计划也使用我',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('id') == asset.id and not item.get('is_history'))
        self.assertEqual(row['note'], '代理列表备注：删除计划也使用我')
        self.assertIn('代理列表备注', row['display_note'])

        CloudLifecyclePlanNote.objects.create(
            plan_kind=CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE,
            asset=asset,
            note='旧版删除计划备注：现在不再使用',
        )
        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('id') == asset.id and not item.get('is_history'))
        self.assertEqual(row['note'], '代理列表备注：删除计划也使用我')
        self.assertNotIn('旧版删除计划备注', row['display_note'])

        staff_user = get_user_model().objects.create_user(username='staff_plan_table_ip', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/tasks/plans/', {'limit': 20, 'refresh': 1})
        request.user = staff_user
        response = lifecycle_plans(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CloudLifecyclePlan.objects.filter(plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE, asset=asset, data_group='active').exists())

    def test_update_lifecycle_plan_note_updates_asset_note(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unattached-shared-note-save',
            public_ip='5.5.5.32',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            provider_status='未附加固定IP',
            note='代理列表原备注',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        staff_user = get_user_model().objects.create_user(username='staff_plan_note_asset', password='x', is_staff=True)
        sync_request = self.factory.get('/api/admin/tasks/plans/', {'limit': 20, 'refresh': 1})
        sync_request.user = staff_user
        sync_response = lifecycle_plans(sync_request)
        self.assertEqual(sync_response.status_code, 200)

        request = RequestFactory().post(
            '/api/admin/tasks/plans/notes/',
            data=json.dumps({'asset_id': asset.id, 'item_type': 'asset', 'note': '删除计划新备注'}),
            content_type='application/json',
        )
        request.user = staff_user

        response = update_lifecycle_plan_note(request)

        self.assertEqual(response.status_code, 200)
        asset.refresh_from_db()
        self.assertEqual(asset.note, '删除计划新备注')
        self.assertFalse(CloudLifecyclePlanNote.objects.filter(
            plan_kind=CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE,
            asset=asset,
        ).exists())
        plan_row = CloudLifecyclePlan.objects.get(
            plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
            asset=asset,
            data_group='active',
        )
        self.assertEqual(plan_row.note, '删除计划新备注')

    def test_lifecycle_plans_use_separate_order_plan_note(self):
        delete_at = timezone.now() + timezone.timedelta(hours=1)
        order = CloudServerOrder.objects.create(
            order_no='SHUTDOWN-INDEPENDENT-NOTE-1',
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
            status='suspended',
            public_ip='7.7.7.31',
            service_expires_at=delete_at - timezone.timedelta(days=3),
            suspend_at=delete_at - timezone.timedelta(days=1),
            delete_at=delete_at,
            provision_note='订单原备注：不要复用我',
        )
        plan_note = CloudLifecyclePlanNote.objects.create(
            plan_kind=CloudLifecyclePlanNote.PLAN_KIND_SHUTDOWN_ORDER,
            order=order,
            note='删机计划备注：单独保存',
        )
        staff_user = get_user_model().objects.create_user(username='staff_plan_note_order', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/tasks/plans/', {'limit': 20, 'refresh': 1})
        request.user = staff_user

        response = lifecycle_plans(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['code'], 0)
        plan_note.refresh_from_db()
        self.assertEqual(plan_note.note, '删机计划备注：单独保存')
        self.assertEqual(order.provision_note, '订单原备注：不要复用我')

    def test_unattached_ip_delete_items_include_name_expiry_and_detail_path(self):
        delete_due_at = timezone.now() + timezone.timedelta(days=3)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-unattached-name-expiry',
            public_ip='5.5.5.9',
            actual_expires_at=delete_due_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('id') == asset.id)

        self.assertEqual(row['asset_name'], 'visible-unattached-name-expiry')
        self.assertEqual(row['detail_path'], f'/admin/cloud-assets/{asset.id}')
        self.assertEqual(parse_datetime(row['service_expires_at']), delete_due_at)

    def test_cloud_orders_list_keeps_renew_pending_visible(self):
        order = CloudServerOrder.objects.create(
            order_no='CLOUD-ORDER-LIST-RENEW-PENDING-1',
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
            pay_method='address',
            status='renew_pending',
            public_ip='6.6.6.9',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(hours=8),
            expired_at=timezone.now() + timezone.timedelta(minutes=30),
        )
        staff_user = get_user_model().objects.create_user(username='staff_cloud_order_list', password='x', is_staff=True)
        request = RequestFactory().get('/api/dashboard/cloud-orders/')
        request.user = staff_user

        response = cloud_orders_list(request)
        payload = json.loads(response.content)
        data = payload.get('data') or []
        row = next(item for item in data if item.get('id') == order.id)

        self.assertEqual(row['renew_status'], 'renew_pending')
        self.assertEqual(row['renew_status_label'], '续费待支付')
        self.assertTrue(row['can_renew'])

    def test_unattached_ip_delete_items_compact_display_note(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-unattached-compact-note',
            public_ip='5.5.5.8',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP\nGet: apt noise\ntg://proxy?server=1.1.1.1&port=9528&secret=x\nsocks5://u:p@1.1.1.1:9534\n人工备注保留',
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('id') == asset.id)

        self.assertIn('未附加固定IP', row['display_note'])
        self.assertIn('人工备注保留', row['display_note'])
        self.assertNotIn('tg://proxy?', row['display_note'])
        self.assertNotIn('socks5://', row['display_note'])
        self.assertNotIn('Get:', row['display_note'])
        self.assertEqual(row['note'], asset.note)
        self.assertIn('tg://proxy?', row['source_note'])

    def test_unattached_ip_delete_items_use_actual_expiry_as_delete_plan(self):
        delete_due_at = timezone.now() + timezone.timedelta(days=3)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-unattached-direct-delete-plan',
            public_ip='5.5.5.7',
            actual_expires_at=delete_due_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('id') == asset.id)

        self.assertEqual(parse_datetime(row['delete_at']), delete_due_at)

    def test_unattached_ip_delete_items_skip_inactive_cloud_account_assets(self):
        inactive_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='inactive-unattached',
            external_account_id='acct-unattached-inactive',
            access_key='ak3',
            secret_key='sk3',
            is_active=False,
        )
        active_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='active-unattached',
            external_account_id='acct-unattached-active',
            access_key='ak4',
            secret_key='sk4',
            is_active=True,
        )
        hidden_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=inactive_account,
            account_label='acct-unattached-inactive',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='hidden-unattached-asset',
            public_ip='5.5.5.5',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=False,
            provider_status='未附加固定IP',
            note='未附加固定IP',
        )
        visible_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=active_account,
            account_label='acct-unattached-active',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-unattached-asset',
            public_ip='5.5.5.6',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
        )

        items = _unattached_ip_delete_items(limit=20)
        asset_ids = {item.get('id') for item in items}

        self.assertIn(visible_asset.id, asset_ids)
        self.assertNotIn(hidden_asset.id, asset_ids)

    def test_unattached_ip_delete_items_include_sync_deleted_history(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='sync-deleted-unattached-ip',
            public_ip=None,
            previous_public_ip='5.5.5.10',
            status=CloudAsset.STATUS_DELETED,
            is_active=False,
            provider_status='云上未找到实例/IP',
            note='未附加固定IP；状态: 云上未找到实例/IP',
        )
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_DELETED,
            asset=asset,
            previous_public_ip='5.5.5.10',
            public_ip=None,
            note='IP校验发现云上不存在，已标记删除',
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('asset_name') == 'sync-deleted-unattached-ip')

        self.assertEqual(row['public_ip'], '5.5.5.10')
        self.assertIn('IP校验发现云上不存在，已标记删除', row['note'])
        self.assertTrue(row['is_overdue'])

    def test_unattached_ip_delete_items_exclude_cloud_missing_active_plan(self):
        missing_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='missing-unattached-active-plan',
            public_ip='5.5.5.11',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='云上未找到实例/IP-待确认',
            note='未附加固定IP；IP校验发现云上不存在，已标记删除',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        note_deleted_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='note-deleted-unattached-active-plan',
            public_ip='5.5.5.13',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            note='未附加固定IP；IP校验发现云上不存在，已标记删除',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        visible_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-unattached-active-plan',
            public_ip='5.5.5.12',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )

        items = _unattached_ip_delete_items(limit=20)
        active_asset_ids = {item.get('id') for item in items if not item.get('is_history')}

        self.assertNotIn(missing_asset.id, active_asset_ids)
        self.assertNotIn(note_deleted_asset.id, active_asset_ids)
        self.assertIn(visible_asset.id, active_asset_ids)

    def test_unattached_ip_delete_items_prefer_asset_note_over_trace_note(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-note-unattached-active-plan',
            public_ip='5.5.5.18',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            provider_status='未附加固定IP',
            note='人工备注：先生已确认保留',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        CloudLifecyclePlanNote.objects.create(
            plan_kind=CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE,
            asset=asset,
            note='旧版删除计划备注：不要显示我',
        )
        CloudIpLog.objects.create(
            asset=asset,
            user=self.user,
            public_ip=asset.public_ip,
            event_type=CloudIpLog.EVENT_DELETED,
            note='未附加固定IP；IP校验发现云上不存在，已标记删除',
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('id') == asset.id and not item.get('is_history'))

        self.assertEqual(row['note'], '人工备注：先生已确认保留')
        self.assertIn('人工备注', row['display_note'])
        self.assertNotIn('旧版删除计划备注', row['display_note'])
        self.assertEqual(row['deletion_source_label'], '同步校验删除')

    def test_ip_log_delete_keeps_previous_ip_from_change_chain(self):
        order = CloudServerOrder.objects.create(
            order_no='IP-LOG-CHAIN-DELETE-1',
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
            public_ip='6.6.6.2',
            previous_public_ip='6.6.6.1',
        )
        first = record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_CHANGED,
            order=order,
            public_ip='6.6.6.2',
            previous_public_ip='6.6.6.1',
            note='更换IP，6.6.6.1 -> 6.6.6.2',
        )
        second = record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_DELETED,
            order=order,
            public_ip=None,
            previous_public_ip='6.6.6.2',
            note='IP校验发现云上不存在，已标记删除',
        )
        first.refresh_from_db()

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.public_ip, '6.6.6.2')
        self.assertEqual(first.previous_public_ip, '6.6.6.1')
        self.assertEqual(first.event_type, CloudIpLog.EVENT_DELETED)
        self.assertIn('IP校验发现云上不存在，已标记删除', first.note)

    def test_unattached_ip_delete_items_dedupe_same_ip_and_mark_covered(self):
        old_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='old-duplicate-static-ip',
            public_ip='5.5.5.20',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        latest_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='latest-duplicate-static-ip',
            public_ip='5.5.5.20',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=2),
        )
        CloudAsset.objects.filter(id=old_asset.id).update(updated_at=timezone.now() - timezone.timedelta(days=1))
        CloudAsset.objects.filter(id=latest_asset.id).update(updated_at=timezone.now())

        items = _unattached_ip_delete_items(limit=20)
        rows = [item for item in items if item.get('public_ip') == '5.5.5.20']

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['asset_id'], latest_asset.id)
        self.assertIn('covered_duplicates', rows[0].get('quality_flags') or [])
        self.assertIn('已覆盖 1 条同 IP 旧记录', rows[0].get('quality_label') or '')

    def test_unattached_ip_delete_items_mark_cloud_missing_history(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='cloud-missing-history-static-ip',
            public_ip=None,
            previous_public_ip='5.5.5.21',
            status=CloudAsset.STATUS_DELETED,
            is_active=False,
            provider_status='云上未找到实例/IP',
            note='未附加固定IP；IP校验发现云上不存在，已标记删除',
        )
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_DELETED,
            asset=asset,
            previous_public_ip='5.5.5.21',
            public_ip=None,
            note='IP校验发现云上不存在，已标记删除',
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('public_ip') == '5.5.5.21')

        self.assertIn('cloud_missing', row.get('quality_flags') or [])
        self.assertIn('云上已不存在', row.get('quality_label') or '')
        self.assertIn('云上已不存在', row.get('execution_status') or '')

    def test_unattached_ip_delete_items_skip_assets_attached_to_instance(self):
        attached_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='attached-static-ip-asset',
            public_ip='5.5.5.8',
            instance_id='attached-instance-1',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='旧同步残留：未附加固定IP',
        )

        items = _unattached_ip_delete_items(limit=20)
        asset_ids = {item.get('id') for item in items}

        self.assertNotIn(attached_asset.id, asset_ids)

    def test_sync_cloud_assets_runs_enabled_accounts_and_merges_results(self):
        aliyun_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-sync-assets-all',
            external_account_id='acct-aliyun-sync-assets-all',
            access_key='ak',
            secret_key='sk',
            region_hint='cn-hongkong',
            is_active=True,
        )
        aws_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-sync-assets-all',
            external_account_id='acct-aws-sync-assets-all',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_sync_assets_all', password='x', is_staff=True, is_superuser=True)
        calls = []

        class AwsCommand:
            synced_regions = ['ap-southeast-1']
            sync_errors = []

        class AliyunCommand:
            pass

        def fake_call_command(command_name, **kwargs):
            calls.append((command_name, kwargs))
            if command_name == 'sync_aws_assets':
                return AwsCommand(), f'aws account {kwargs.get("account_id")} ok\n'
            return AliyunCommand(), f'aliyun account {kwargs.get("account_id")} ok\n'

        request = RequestFactory().post('/api/dashboard/cloud-assets/sync/', data='{}', content_type='application/json')
        request.user = staff_user
        with patch('cloud.api._call_command_capture_threaded', side_effect=fake_call_command), patch('cloud.api._call_command_capture', return_value=(object(), 'reconcile ok\n')):
            response = sync_cloud_assets(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)['data']
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['synced']['aliyun'])
        self.assertTrue(payload['synced']['aws'])
        self.assertTrue(payload['synced']['reconcile'])
        self.assertIn('ap-southeast-1', payload['aws_regions'])
        self.assertIn(('sync_aliyun_assets', {'region': 'cn-hongkong', 'account_id': str(aliyun_account.id)}), calls)
        self.assertIn(('sync_aws_assets', {'region': '', 'account_id': str(aws_account.id)}), calls)

    def test_sync_cloud_asset_status_uses_asset_scope(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='single-asset-sync',
            external_account_id='acct-single-asset-sync',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=cloud_account_label(account),
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='single-asset-sync',
            public_ip='3.3.3.3',
            instance_id='i-single-asset-sync',
            provider_resource_id='res-single-asset-sync',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_sync_one', password='x', is_staff=True, is_superuser=True)
        with patch('cloud.api._call_command_capture', return_value=(object(), None)) as mocked:
            request = RequestFactory().post(f'/api/dashboard/cloud-assets/{asset.id}/sync/', data='{}', content_type='application/json')
            request.user = staff_user
            response = sync_cloud_asset_status(request, asset.id)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload['data']['ok'])
        self.assertEqual(payload['data']['asset']['id'], asset.id)
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.args[0], 'sync_aws_assets')
        self.assertEqual(mocked.call_args.kwargs['account_id'], str(account.id))
        self.assertEqual(mocked.call_args.kwargs['region'], 'ap-southeast-1')

    def test_sync_retained_ip_asset_uses_order_account_and_static_ip_scope(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='single-retained-ip-sync',
            external_account_id='acct-single-retained-ip-sync',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='SINGLE-RETAINED-IP-SYNC-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='3.3.3.44',
            previous_public_ip='3.3.3.44',
            static_ip_name='StaticIp-single-retained-sync',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=10),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='stale-deleted-instance-name',
            public_ip='3.3.3.44',
            previous_public_ip='3.3.3.44',
            actual_expires_at=order.ip_recycle_at,
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(username='staff_retained_asset_sync_one', password='x', is_staff=True, is_superuser=True)
        with patch('cloud.api._call_command_capture', return_value=(object(), None)) as mocked:
            request = RequestFactory().post(f'/api/dashboard/cloud-assets/{asset.id}/sync/', data='{}', content_type='application/json')
            request.user = staff_user
            response = sync_cloud_asset_status(request, asset.id)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)['data']
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['scope']['instance_id'], 'StaticIp-single-retained-sync')
        self.assertEqual(payload['scope']['public_ip'], '3.3.3.44')
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.args[0], 'sync_aws_assets')
        self.assertEqual(mocked.call_args.kwargs['account_id'], str(account.id))
        self.assertEqual(mocked.call_args.kwargs['instance_id'], 'StaticIp-single-retained-sync')
        self.assertEqual(mocked.call_args.kwargs['public_ip'], '3.3.3.44')

    def test_proxy_asset_ip_query_exposes_manual_expiry_for_admin_and_user(self):
        expires_at = timezone.now() + timezone.timedelta(days=12)
        visible_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='manual-query-visible',
            public_ip='3.3.3.33',
            actual_expires_at=expires_at,
            price='19.00',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        other_user = TelegramUser.objects.create(tg_user_id=990002, username='other_query_user')

        admin_asset = async_to_sync(get_proxy_asset_by_ip_for_admin)('3.3.3.33')
        user_asset = async_to_sync(get_proxy_asset_by_ip_for_user)('3.3.3.33', self.user.id)
        hidden_asset = async_to_sync(get_proxy_asset_by_ip_for_user)('3.3.3.33', other_user.id)

        self.assertEqual(admin_asset.id, visible_asset.id)
        self.assertEqual(user_asset.id, visible_asset.id)
        self.assertEqual(admin_asset.service_expires_at, expires_at)
        self.assertEqual(user_asset.service_expires_at, expires_at)
        self.assertIsNone(hidden_asset)

    def test_proxy_asset_ip_query_skips_cloud_missing_asset(self):
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='manual-query-missing',
            public_ip='3.3.3.34',
            actual_expires_at=timezone.now() + timezone.timedelta(days=12),
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='云上未找到实例/IP-待确认',
            note='未附加固定IP；IP校验发现云上不存在，已标记删除',
        )
        visible_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='manual-query-visible-fallback',
            public_ip='3.3.3.34',
            actual_expires_at=timezone.now() + timezone.timedelta(days=15),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='运行中',
        )

        admin_asset = async_to_sync(get_proxy_asset_by_ip_for_admin)('3.3.3.34')

        self.assertEqual(admin_asset.id, visible_asset.id)

    def test_cloud_server_ip_query_requires_owner_identity(self):
        other_user = TelegramUser.objects.create(tg_user_id=990003, username='other_order_query_user')
        order = CloudServerOrder.objects.create(
            order_no='IP-OWNER-QUERY-1',
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
            public_ip='4.4.4.44',
            service_expires_at=timezone.now() + timezone.timedelta(days=10),
        )

        owned_order = async_to_sync(get_cloud_server_by_ip_for_user)('4.4.4.44', self.user.id)
        hidden_order = async_to_sync(get_cloud_server_by_ip_for_user)('4.4.4.44', other_user.id)

        self.assertEqual(owned_order.id, order.id)
        self.assertIsNone(hidden_order)

    def test_cloud_server_public_renewal_allows_stranger_payment_entry(self):
        other_user = TelegramUser.objects.create(tg_user_id=990004, username='other_order_renew_user')
        order = CloudServerOrder.objects.create(
            order_no='IP-OWNER-RENEW-1',
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
            public_ip='4.4.4.45',
            service_started_at=timezone.now() - timezone.timedelta(days=20),
            service_expires_at=timezone.now() + timezone.timedelta(days=10),
        )

        user_scoped = async_to_sync(create_cloud_server_renewal_for_user)(order.id, other_user.id, 31)
        public_renewal = async_to_sync(create_cloud_server_renewal_by_public_query)(order.id, 31)

        self.assertIsNone(user_scoped)
        self.assertIsNotNone(public_renewal)
        self.assertEqual(public_renewal.user_id, self.user.id)

    def test_public_unattached_asset_renewal_plans_are_available(self):
        other_user = TelegramUser.objects.create(tg_user_id=990006, username='other_unattached_asset_renew_user')
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='strict-unattached-account',
            external_account_id='acct-strict-unattached',
            access_key='ak',
            secret_key='sk',
            region_hint=self.plan.region_code,
            is_active=True,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label='aws+acct-strict-unattached+strict-unattached-account',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='public-unattached-asset-renewal',
            public_ip='4.4.4.47',
            actual_expires_at=timezone.now() + timezone.timedelta(days=15),
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:test:StaticIp/public-unattached-asset-renewal',
        )

        denied_asset, denied_plans, denied_err = async_to_sync(list_cloud_asset_renewal_plans)(asset.id, other_user.id)
        public_asset, public_plans, public_err = async_to_sync(list_cloud_asset_renewal_plans)(asset.id, other_user.id, public=True)

        self.assertIsNone(denied_asset)
        self.assertEqual(denied_plans, [])
        self.assertEqual(denied_err, '代理记录不存在')
        self.assertEqual(public_asset.id, asset.id)
        self.assertGreaterEqual(len(public_plans), 1)
        self.assertIsNone(public_err)

    def test_retained_deleted_asset_renewal_plans_are_available_by_asset_button(self):
        now = timezone.now()
        order = CloudServerOrder.objects.create(
            order_no='RETAINED-ASSET-BUTTON-1',
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
            public_ip='4.4.4.49',
            previous_public_ip='4.4.4.49',
            instance_id='',
            static_ip_name='retained-asset-button-ip',
            ip_recycle_at=now + timezone.timedelta(days=10),
            service_started_at=now - timezone.timedelta(days=40),
            service_expires_at=now - timezone.timedelta(days=5),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='retained-asset-button-ip',
            public_ip='4.4.4.49',
            previous_public_ip='4.4.4.49',
            actual_expires_at=order.ip_recycle_at,
            status=CloudAsset.STATUS_DELETED,
            is_active=False,
            provider_status='固定IP保留中-实例已删除',
            note='实例删除后固定IP保留中',
        )

        detail = async_to_sync(get_user_proxy_asset_detail)(asset.id, self.user.id, 'asset')
        retained_order, plans, err = async_to_sync(list_retained_ip_renewal_plans_by_asset)(asset.id, self.user.id)

        self.assertIsNone(detail)
        self.assertEqual(retained_order.id, order.id)
        self.assertGreaterEqual(len(plans), 1)
        self.assertIsNone(err)

    def test_public_unattached_asset_renewal_requires_original_account(self):
        other_user = TelegramUser.objects.create(tg_user_id=990007, username='other_unattached_asset_no_account')
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='public-unattached-asset-no-account',
            public_ip='4.4.4.48',
            actual_expires_at=timezone.now() + timezone.timedelta(days=15),
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:test:StaticIp/public-unattached-asset-no-account',
        )

        public_asset, public_plans, public_err = async_to_sync(list_cloud_asset_renewal_plans)(asset.id, other_user.id, public=True)

        self.assertEqual(public_asset.id, asset.id)
        self.assertEqual(public_plans, [])
        self.assertEqual(public_err, '原固定 IP 所属云账号不可用，暂时无法自助续费，请联系人工客服。')

    def test_asset_recovery_candidates_only_original_account(self):
        other_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='other-strict-unattached-account',
            external_account_id='acct-other-strict-unattached',
            access_key='ak',
            secret_key='sk',
            region_hint=self.plan.region_code,
            is_active=True,
        )
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='source-strict-unattached-account',
            external_account_id='acct-source-strict-unattached',
            access_key='ak',
            secret_key='sk',
            region_hint=self.plan.region_code,
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='ASSET-RECOVERY-STRICT-ACCOUNT',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            account_label='aws+acct-source-strict-unattached+source-strict-unattached-account',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='paid',
            public_ip='4.4.4.49',
            static_ip_name='strict-static-ip',
            provision_note='未绑定代理资产续费：来源资产 #999。',
        )

        account_ids = async_to_sync(_candidate_cloud_account_ids)(order.id)

        self.assertEqual(account_ids, [account.id])
        self.assertNotIn(other_account.id, account_ids)

    def test_proxy_link_query_extracts_server_ip_only(self):
        from bot.handlers import _extract_proxy_links_by_ip, _extract_query_ips

        raw = 'https://t.me/proxy?server=3.0.162.212&port=443&secret=ee78fbdf52d2713cced14f283718ab6917617a7572652e6d6963726f736f66742e636f6d'

        self.assertEqual(_extract_query_ips(raw), ['3.0.162.212'])
        self.assertEqual(_extract_proxy_links_by_ip(raw)['3.0.162.212']['port'], '443')

    def test_tg_proxy_link_query_extracts_server_ip_only(self):
        from bot.handlers import _extract_query_ips

        raw = 'tg://proxy?server=3.0.162.213&port=443&secret=abc'

        self.assertEqual(_extract_query_ips(raw), ['3.0.162.213'])

    def test_ip_query_displays_matched_asset_ip_not_order_ip(self):
        order = CloudServerOrder.objects.create(
            order_no='IP-MATCH-ASSET-ORDER-1',
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
            public_ip='54.151.227.23',
            service_expires_at=timezone.now() + timezone.timedelta(days=10),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='ip-match-asset-order-1',
            public_ip='3.0.162.212',
            actual_expires_at=timezone.now() + timezone.timedelta(days=15),
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
        )

        result = async_to_sync(get_cloud_server_by_ip)('3.0.162.212')

        self.assertEqual(result.matched_query_ip, '3.0.162.212')
        self.assertEqual(result.public_ip, '3.0.162.212')

    def test_ip_query_displays_matched_previous_ip_not_order_ip(self):
        order = CloudServerOrder.objects.create(
            order_no='IP-MATCH-PREVIOUS-ORDER-1',
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
            public_ip='54.151.227.24',
            service_expires_at=timezone.now() + timezone.timedelta(days=10),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='ip-match-previous-order-1',
            previous_public_ip='3.0.162.213',
            actual_expires_at=timezone.now() + timezone.timedelta(days=15),
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
        )

        result = async_to_sync(get_cloud_server_by_ip)('3.0.162.213')

        self.assertEqual(result.matched_query_ip, '3.0.162.213')

    def test_cloud_server_ip_change_requires_owner_identity(self):
        other_user = TelegramUser.objects.create(tg_user_id=990005, username='other_order_ip_change_user')
        order = CloudServerOrder.objects.create(
            order_no='IP-OWNER-CHANGE-1',
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
            public_ip='4.4.4.46',
            mtproxy_port=9528,
            mtproxy_secret='abcdef',
            ip_change_quota=1,
            service_expires_at=timezone.now() + timezone.timedelta(days=10),
        )

        denied = async_to_sync(mark_cloud_server_ip_change_requested)(order.id, other_user.id, self.plan.region_code, 9528)
        allowed = async_to_sync(mark_cloud_server_ip_change_requested)(order.id, self.user.id, self.plan.region_code, 9528)

        self.assertIsNone(denied)
        self.assertIsNotNone(allowed)
        self.assertEqual(allowed.user_id, self.user.id)
        self.assertEqual(allowed.replacement_for_id, order.id)

    def test_lifecycle_plans_excludes_cloud_missing_orphan_server(self):
        missing_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='missing-orphan-server-plan',
            public_ip='3.3.3.35',
            instance_id='i-missing-orphan-server-plan',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='云上未找到实例/IP-待确认',
            note='服务器校验发现云上不存在，已标记删除',
        )
        visible_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='visible-orphan-server-plan',
            public_ip='3.3.3.36',
            instance_id='i-visible-orphan-server-plan',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='运行中',
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_plan_missing', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': 1})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        due_ids = {item.get('asset_id') for item in data['due_items']}

        self.assertNotIn(missing_asset.id, due_ids)
        self.assertIn(visible_asset.id, due_ids)
        self.assertFalse(CloudLifecyclePlan.objects.filter(plan_kind=CloudLifecyclePlan.PLAN_KIND_ORPHAN_ASSET_DELETE, asset=missing_asset, data_group='active').exists())
        self.assertTrue(CloudLifecyclePlan.objects.filter(plan_kind=CloudLifecyclePlan.PLAN_KIND_ORPHAN_ASSET_DELETE, asset=visible_asset, data_group='active').exists())

    def test_lifecycle_plans_keeps_asset_remarks_out_of_execution_status(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='orphan-plan-note-columns',
            instance_id='i-orphan-plan-note-columns',
            public_ip='3.3.3.37',
            actual_expires_at=timezone.now() - timezone.timedelta(days=7),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='运行中',
            note='人工备注：这是一段很长的业务备注，不应该侵占执行状态列。\nGet: apt noise\ntg://proxy?server=1.1.1.1&port=9528&secret=x',
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_plan_columns', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': 1})
        request.user = staff_user

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        row = next(item for item in data['due_items'] if item.get('asset_id') == asset.id)

        self.assertEqual(row['execution_status'], '无订单同步资产已到期，待执行删除服务器')
        self.assertEqual(row['execution_plan'][:5], '删除服务器')
        self.assertEqual(row['resource_state_label'], '实例仍存在')
        self.assertEqual(row['plan_state_label'], '待执行')
        self.assertTrue(row['should_execute'])
        self.assertIn('人工备注', row['display_note'])
        self.assertNotIn('tg://proxy?', row['display_note'])
        self.assertNotIn('Get:', row['display_note'])
