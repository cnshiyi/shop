from .common import *


class CloudServerPaymentsRenewalsMixin:
    def test_rebind_cloud_server_user_syncs_order_asset_and_server(self):
        new_user = TelegramUser.objects.create(tg_user_id=990002, username='svc_rebind_new')
        order = CloudServerOrder.objects.create(
            order_no='REBIND-SYNC-1',
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
            server_name='rebind-sync-server',
            instance_id='rebind-sync-server',
            public_ip='13.250.10.21',
            service_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            status=CloudAsset.STATUS_RUNNING,
        )
        server = Server.objects.create(
            order=order,
            user=self.user,
            source=Server.SOURCE_ORDER,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            status=Server.STATUS_RUNNING,
        )

        rebound = async_to_sync(rebind_cloud_server_user)(order.id, new_user.id)

        self.assertEqual(rebound.user_id, new_user.id)
        self.assertEqual(rebound.last_user_id, new_user.tg_user_id)
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(asset.user_id, new_user.id)
        self.assertEqual(server.user_id, new_user.id)

    def test_cloud_order_wallet_pay_uses_total_amount_not_address_unique_amount(self):
        self.user.balance = Decimal('19.00')
        self.user.save(update_fields=['balance', 'updated_at'])
        order = CloudServerOrder.objects.create(
            order_no='WALLET-PAY-BASE-AMOUNT-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.777',
            pay_method='address',
            status='pending',
        )

        paid_order, err = async_to_sync(pay_cloud_server_order_with_balance)(order.id, self.user.id, 'USDT')

        self.assertIsNone(err)
        self.assertEqual(paid_order.status, 'paid')
        self.user.refresh_from_db()
        paid_order.refresh_from_db()
        self.assertEqual(self.user.balance, Decimal('0.000000'))
        self.assertEqual(paid_order.pay_amount, Decimal('19.000000000'))
        self.assertEqual(paid_order.currency, 'USDT')

    def test_cloud_order_wallet_pay_trx_converts_total_amount_once(self):
        self.user.balance_trx = Decimal('100.00')
        self.user.save(update_fields=['balance_trx', 'updated_at'])
        order = CloudServerOrder.objects.create(
            order_no='WALLET-PAY-TRX-BASE-AMOUNT-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='TRX',
            total_amount='19.00',
            pay_amount='101.000',
            pay_method='address',
            status='pending',
        )

        async def fake_usdt_to_trx(amount):
            self.assertEqual(amount, Decimal('19.000000'))
            return Decimal('100.00')

        with patch('cloud.services.usdt_to_trx', fake_usdt_to_trx):
            paid_order, err = async_to_sync(pay_cloud_server_order_with_balance)(order.id, self.user.id, 'TRX')

        self.assertIsNone(err)
        self.assertEqual(paid_order.status, 'paid')
        self.user.refresh_from_db()
        paid_order.refresh_from_db()
        self.assertEqual(self.user.balance_trx, Decimal('0.000000'))
        self.assertEqual(paid_order.pay_amount, Decimal('100.000000000'))
        self.assertEqual(paid_order.currency, 'TRX')

    def test_cloud_renewal_address_order_uses_usdt_even_after_trx_wallet_order(self):
        order = CloudServerOrder.objects.create(
            order_no='RENEW-TRX-SOURCE-USDT-ADDRESS-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='TRX',
            total_amount='19.00',
            pay_amount='100.00',
            pay_method='balance',
            status='completed',
            public_ip='8.8.4.80',
            service_started_at=timezone.now() - timezone.timedelta(days=20),
            service_expires_at=timezone.now() + timezone.timedelta(days=10),
        )

        renewal = async_to_sync(create_cloud_server_renewal_for_user)(order.id, self.user.id, 31)

        self.assertEqual(renewal.status, 'renew_pending')
        self.assertEqual(renewal.currency, 'USDT')
        self.assertEqual(renewal.total_amount, Decimal('19.00'))
        self.assertGreaterEqual(renewal.pay_amount, Decimal('19.001000000'))
        self.assertLess(renewal.pay_amount, Decimal('20.000000000'))

    def test_cloud_address_order_forces_usdt_when_requested_trx(self):
        order = async_to_sync(create_cloud_server_order)(self.user.id, self.plan.id, 'TRX', 1)

        self.assertEqual(order.currency, 'USDT')
        self.assertEqual(order.total_amount, Decimal('19.00'))
        self.assertGreaterEqual(order.pay_amount, Decimal('19.001000000'))
        self.assertLess(order.pay_amount, Decimal('20.000000000'))

    def test_unbound_asset_renewal_address_order_forces_usdt_from_trx_source(self):
        self.plan.currency = 'TRX'
        self.plan.save(update_fields=['currency'])
        due_at = timezone.now() + timezone.timedelta(days=9)
        account = self._aws_test_account()
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unbound-renewal-trx-source',
            public_ip='31.31.31.37',
            previous_public_ip='31.31.31.37',
            actual_expires_at=due_at,
            price='19.00',
            currency='TRX',
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.37&port=9528&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.31.37',
            'port': '9528',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        order, error = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)

        self.assertIsNone(error)
        self.assertEqual(order.currency, 'USDT')
        self.assertEqual(order.total_amount, Decimal('19.00'))
        self.assertGreaterEqual(order.pay_amount, Decimal('19.001000000'))
        self.assertLess(order.pay_amount, Decimal('20.000000000'))

    def test_retained_ip_renewal_address_order_forces_usdt_from_trx_order(self):
        self.plan.currency = 'TRX'
        self.plan.save(update_fields=['currency'])
        recycle_at = timezone.now() + timezone.timedelta(days=9)
        order = CloudServerOrder.objects.create(
            order_no='RETAINED-IP-TRX-SOURCE-USDT-ADDRESS-1',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='TRX',
            total_amount='19.00',
            pay_amount='100.00',
            pay_method='balance',
            status='deleted',
            public_ip='31.31.31.39',
            previous_public_ip='31.31.31.39',
            instance_id='',
            static_ip_name='StaticIp-retained-trx-source',
            ip_recycle_at=recycle_at,
            mtproxy_secret='eeeeeeeeeeeeeeee',
            mtproxy_port=9528,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.39&port=9528&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.31.39',
            'port': '9528',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        renewal, error = async_to_sync(prepare_retained_ip_renewal_with_link)(order.id, self.user.id, self.plan.id, link)

        self.assertIsNone(error)
        self.assertEqual(renewal.currency, 'USDT')
        self.assertEqual(renewal.total_amount, Decimal('19.00'))
        self.assertGreaterEqual(renewal.pay_amount, Decimal('19.001000000'))
        self.assertLess(renewal.pay_amount, Decimal('20.000000000'))

    def test_lifecycle_delete_notice_batches_multiple_ips_for_same_user(self):
        now = timezone.now()
        orders = []
        for index in range(2):
            order = CloudServerOrder.objects.create(
                order_no=f'BATCH-DELETE-NOTICE-{index + 1}',
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
                public_ip=f'10.66.0.{index + 1}',
                service_started_at=now - timezone.timedelta(days=35),
                service_expires_at=now - timezone.timedelta(days=2),
                suspend_at=now - timezone.timedelta(days=1),
                delete_at=now + timezone.timedelta(hours=12),
                delete_reminder_enabled=True,
            )
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_ORDER,
                order=order,
                user=self.user,
                provider=order.provider,
                region_code=order.region_code,
                region_name=order.region_name,
                asset_name=f'batch-delete-notice-{index + 1}',
                public_ip=order.public_ip,
                actual_expires_at=order.service_expires_at,
                status=CloudAsset.STATUS_RUNNING,
                is_active=True,
            )
            orders.append(order)
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': orders,
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [],
        }
        notify = AsyncMock(return_value=True)

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]):
            async_to_sync(lifecycle_tick)(notify=notify)

        notify.assert_awaited_once()
        _, text, _ = notify.await_args.args
        self.assertIn('10.66.0.1', text)
        self.assertIn('10.66.0.2', text)
        self.assertNotIn('订单号', text)
        self.assertEqual(CloudUserNoticeLog.objects.filter(event_type='delete_notice', is_batch=True).count(), 1)
        for order in orders:
            order.refresh_from_db()
            self.assertIsNotNone(order.delete_notice_sent_at)

    def test_update_cloud_asset_write_requires_superuser(self):
        staff = get_user_model().objects.create_user(username='staff_asset_update_forbidden', password='x', is_staff=True)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='staff-forbidden-update-asset',
            public_ip='11.11.10.10',
            status=CloudAsset.STATUS_RUNNING,
            price='19.00',
        )

        request = self.factory.patch(
            '/api/dashboard/cloud-assets/%s/' % asset.id,
            data=json.dumps({
                'public_ip': '11.11.10.11',
                'actual_expires_at': (timezone.now() + timezone.timedelta(days=10)).isoformat(),
                'price': '29.00',
            }),
            content_type='application/json',
        )
        request.user = staff

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content.decode('utf-8'))['message'], '需要超级管理员权限')
        asset.refresh_from_db()
        self.assertEqual(asset.public_ip, '11.11.10.10')
        self.assertEqual(asset.price, Decimal('19.00'))

    def test_update_cloud_asset_rejects_collapsed_telegram_group_binding(self):
        admin = get_user_model().objects.create_user(username='admin_bind_group', password='x', is_staff=True, is_superuser=True)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='bind-group-asset',
            public_ip='11.11.11.11',
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
        )
        visible_group = TelegramGroupFilter.objects.create(
            chat_id=-1001001,
            title='Visible Group',
            username='visible_group',
            enabled=False,
            collapsed=False,
        )
        hidden_group = TelegramGroupFilter.objects.create(
            chat_id=-1001002,
            title='Hidden Group',
            username='hidden_group',
            enabled=False,
            collapsed=True,
        )

        request = self.factory.post(
            '/api/dashboard/cloud-assets/%s/' % asset.id,
            data=json.dumps({'telegram_group_query': hidden_group.chat_id}),
            content_type='application/json',
        )
        request.user = admin
        response = update_cloud_asset(request, asset.id)
        self.assertEqual(response.status_code, 404)
        self.assertIn('绑定页隐藏', json.loads(response.content.decode('utf-8'))['message'])

        request2 = self.factory.post(
            '/api/dashboard/cloud-assets/%s/' % asset.id,
            data=json.dumps({'telegram_group_query': visible_group.chat_id}),
            content_type='application/json',
        )
        request2.user = admin
        response2 = update_cloud_asset(request2, asset.id)
        self.assertEqual(response2.status_code, 200)
        asset.refresh_from_db()
        self.assertEqual(asset.telegram_group_id, visible_group.id)

    def test_update_cloud_asset_allows_clearing_telegram_group_binding(self):
        admin = get_user_model().objects.create_user(username='admin_unbind_group', password='x', is_staff=True, is_superuser=True)
        group = TelegramGroupFilter.objects.create(
            chat_id=-1002001,
            title='Bound Group',
            username='bound_group',
            enabled=False,
            collapsed=False,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unbind-group-asset',
            public_ip='11.11.22.22',
            status=CloudAsset.STATUS_RUNNING,
            telegram_group=group,
        )

        request = self.factory.post(
            '/api/dashboard/cloud-assets/%s/' % asset.id,
            data=json.dumps({'telegram_group_id': None}),
            content_type='application/json',
        )
        request.user = admin
        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        asset.refresh_from_db()
        self.assertIsNone(asset.telegram_group_id)

    def test_update_cloud_asset_defers_snapshot_refresh(self):
        admin = get_user_model().objects.create_user(username='admin_defer_asset_refresh', password='x', is_staff=True, is_superuser=True)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='defer-refresh-asset',
            public_ip='10.88.9.9',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        request = self.factory.patch(
            '/api/dashboard/cloud-assets/%s/' % asset.id,
            data=json.dumps({'is_active': False}),
            content_type='application/json',
        )
        request.user = admin

        with patch('cloud.api._refresh_dashboard_plan_snapshots') as direct_refresh, \
            patch('cloud.api._refresh_dashboard_plan_snapshots_deferred') as deferred_refresh:
            response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        direct_refresh.assert_not_called()
        deferred_refresh.assert_called_once_with(f'cloud_asset:{asset.id}')

    def test_cloud_assets_paginated_keeps_same_user_on_same_page(self):
        admin = get_user_model().objects.create_user(username='admin_asset_pages', password='x', is_staff=True)
        first_user = TelegramUser.objects.create(tg_user_id=991001, username='page_first')
        boundary_user = TelegramUser.objects.create(tg_user_id=991002, username='page_boundary')
        tail_user = TelegramUser.objects.create(tg_user_id=991003, username='page_tail')

        def create_asset(user, index, sort_order):
            return CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'page-asset-{index}',
                public_ip=f'10.77.0.{index}',
                status=CloudAsset.STATUS_RUNNING,
                sort_order=sort_order,
            )

        for index in range(1, 50):
            create_asset(first_user, index, 200 - index)
        boundary_assets = [create_asset(boundary_user, 50, 120), create_asset(boundary_user, 51, 119)]
        create_asset(tail_user, 52, 10)

        page1 = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'page': '1', 'page_size': '50'})
        page1.user = admin
        response1 = cloud_assets_list(page1)
        payload1 = json.loads(response1.content.decode('utf-8'))['data']
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(payload1['page_size'], 50)
        self.assertGreaterEqual(payload1['total_pages'], 2)
        self.assertFalse(any(item['user_id'] == boundary_user.id for item in payload1['items']))

        page2 = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'page': '2', 'page_size': '50'})
        page2.user = admin
        response2 = cloud_assets_list(page2)
        payload2 = json.loads(response2.content.decode('utf-8'))['data']
        boundary_ids = {asset.id for asset in boundary_assets}
        page2_boundary_ids = {item['id'] for item in payload2['items'] if item['user_id'] == boundary_user.id}
        self.assertEqual(page2_boundary_ids, boundary_ids)

    def test_cloud_assets_paginated_keeps_same_telegram_group_on_same_page(self):
        admin = get_user_model().objects.create_user(username='admin_asset_group_pages', password='x', is_staff=True)
        first_group = TelegramGroupFilter.objects.create(chat_id=-1001991001, title='Page First Group', enabled=True)
        boundary_group = TelegramGroupFilter.objects.create(chat_id=-1001991002, title='Page Boundary Group', enabled=True)
        tail_group = TelegramGroupFilter.objects.create(chat_id=-1001991003, title='Page Tail Group', enabled=True)

        def create_asset(group, index, sort_order):
            return CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                telegram_group=group,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'page-group-asset-{index}',
                public_ip=f'10.78.0.{index}',
                status=CloudAsset.STATUS_RUNNING,
                sort_order=sort_order,
            )

        for index in range(1, 20):
            create_asset(first_group, index, 200 - index)
        boundary_assets = [create_asset(boundary_group, 20, 120), create_asset(boundary_group, 21, 119)]
        create_asset(tail_group, 22, 10)

        page1 = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'group_by': 'telegram_group', 'page': '1', 'page_size': '20'})
        page1.user = admin
        response1 = cloud_assets_list(page1)
        payload1 = json.loads(response1.content.decode('utf-8'))['data']
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(payload1['page_size'], 20)
        self.assertGreaterEqual(payload1['total_pages'], 2)
        self.assertFalse(any(item['telegram_group_id'] == boundary_group.id for item in payload1['items']))

        page2 = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'group_by': 'telegram_group', 'page': '2', 'page_size': '20'})
        page2.user = admin
        response2 = cloud_assets_list(page2)
        payload2 = json.loads(response2.content.decode('utf-8'))['data']
        boundary_ids = {asset.id for asset in boundary_assets}
        page2_boundary_ids = {item['id'] for item in payload2['items'] if item['telegram_group_id'] == boundary_group.id}
        self.assertEqual(page2_boundary_ids, boundary_ids)

    def test_cloud_assets_grouped_paginated_uses_twenty_user_groups_per_page(self):
        admin = get_user_model().objects.create_user(username='admin_asset_grouped_user_pages', password='x', is_staff=True)
        for index in range(1, 23):
            user = TelegramUser.objects.create(tg_user_id=992000 + index, username=f'group_page_user_{index}')
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'group-page-user-{index}',
                public_ip=f'10.79.0.{index}',
                status=CloudAsset.STATUS_RUNNING,
                sort_order=300 - index,
            )

        page1 = self.factory.get('/api/dashboard/cloud-assets/', {'grouped': '1', 'paginated': '1', 'group_by': 'user', 'page': '1', 'page_size': '20'})
        page1.user = admin
        response1 = cloud_assets_list(page1)
        payload1 = json.loads(response1.content.decode('utf-8'))['data']

        self.assertEqual(response1.status_code, 200)
        self.assertEqual(payload1['page_size'], 20)
        self.assertEqual(payload1['total'], 22)
        self.assertEqual(payload1['total_pages'], 2)
        self.assertEqual(len(payload1['groups']), 20)
        self.assertEqual(len(payload1['items']), 20)

        page2 = self.factory.get('/api/dashboard/cloud-assets/', {'grouped': '1', 'paginated': '1', 'group_by': 'user', 'page': '2', 'page_size': '20'})
        page2.user = admin
        response2 = cloud_assets_list(page2)
        payload2 = json.loads(response2.content.decode('utf-8'))['data']
        self.assertEqual(len(payload2['groups']), 2)
        self.assertEqual(len(payload2['items']), 2)

    def test_cloud_assets_list_filters_by_risk_and_searches_asset_identifiers(self):
        admin = get_user_model().objects.create_user(username='admin_asset_risk_filter', password='x', is_staff=True)
        group = TelegramGroupFilter.objects.create(chat_id=-1001993001, title='Risk Filter Group', enabled=True)
        due_expires_at = timezone.now() + timezone.timedelta(days=2)
        due_order = CloudServerOrder.objects.create(
            order_no='RISK-FILTER-ORDER-001',
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
            public_ip='10.88.0.1',
            service_expires_at=due_expires_at,
            static_ip_name='risk-static-ip-001',
        )
        due_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=due_order,
            user=self.user,
            telegram_group=group,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='risk-due-asset',
            instance_id='risk-instance-001',
            public_ip='10.88.0.1',
            actual_expires_at=due_expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )
        normal_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            telegram_group=group,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='risk-normal-asset',
            public_ip='10.88.0.2',
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
            status=CloudAsset.STATUS_RUNNING,
        )

        request = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'risk_status': 'due_soon'})
        request.user = admin
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in payload['items']], [due_asset.id])
        self.assertEqual(payload['items'][0]['risk_status'], 'due_soon')
        self.assertIn('due_soon', payload['items'][0]['risk_statuses'])
        self.assertIn('auto_renew_off', payload['items'][0]['risk_statuses'])
        self.assertEqual(payload['risk_counts']['all'], 2)
        self.assertEqual(payload['risk_counts']['due_soon'], 1)
        self.assertEqual(payload['risk_counts']['auto_renew_off'], 1)
        self.assertEqual(payload['risk_counts']['normal'], 1)

        secondary_request = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'risk_status': 'auto_renew_off'})
        secondary_request.user = admin
        secondary_response = cloud_assets_list(secondary_request)
        secondary_payload = json.loads(secondary_response.content.decode('utf-8'))['data']
        self.assertEqual([item['id'] for item in secondary_payload['items']], [due_asset.id])

        normal_request = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'risk_status': 'normal'})
        normal_request.user = admin
        normal_response = cloud_assets_list(normal_request)
        normal_payload = json.loads(normal_response.content.decode('utf-8'))['data']
        self.assertEqual([item['id'] for item in normal_payload['items']], [normal_asset.id])
        self.assertEqual(normal_payload['items'][0]['risk_label'], '运行中')

        search_request = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'keyword': 'risk-static-ip-001'})
        search_request.user = admin
        search_response = cloud_assets_list(search_request)
        search_payload = json.loads(search_response.content.decode('utf-8'))['data']
        self.assertEqual([item['id'] for item in search_payload['items']], [due_asset.id, normal_asset.id])

    def test_cloud_assets_search_filters_full_dataset_before_pagination(self):
        admin = get_user_model().objects.create_user(username='admin_asset_full_search', password='x', is_staff=True)
        target_user = TelegramUser.objects.create(tg_user_id=991900, username='target_full_search', first_name='代理昵称阿尔法')
        target_group = TelegramGroupFilter.objects.create(chat_id=-1001991900, title='Full Search Group', enabled=True)
        target_order = CloudServerOrder.objects.create(
            order_no='FULL-SEARCH-ORDER-001',
            user=target_user,
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
            server_name='full-search-order-name-alpha',
            public_ip='10.90.0.250',
            service_expires_at=timezone.now() + timezone.timedelta(days=90),
            auto_renew_enabled=True,
        )
        target_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=target_order,
            user=target_user,
            telegram_group=target_group,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='full-search-asset-alpha',
            public_ip='10.90.0.250',
            actual_expires_at=timezone.now() + timezone.timedelta(days=90),
            status=CloudAsset.STATUS_RUNNING,
            sort_order=1,
        )
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            order=target_order,
            user=target_user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='full-search-server-alias-alpha',
            public_ip='10.90.0.250',
            status=Server.STATUS_RUNNING,
            sort_order=1,
        )
        for index in range(12):
            user = TelegramUser.objects.create(tg_user_id=991910 + index, username=f'decoy_full_search_{index}')
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'decoy-full-search-{index}',
                public_ip=f'10.90.0.{index + 1}',
                actual_expires_at=timezone.now() + timezone.timedelta(days=30),
                status=CloudAsset.STATUS_RUNNING,
                sort_order=200 - index,
            )

        first_page_request = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'page': '1', 'page_size': '10'})
        first_page_request.user = admin
        first_page_response = cloud_assets_list(first_page_request)
        first_page_payload = json.loads(first_page_response.content.decode('utf-8'))['data']
        self.assertGreater(first_page_payload['total'], 10)
        self.assertNotIn(target_asset.id, {item['id'] for item in first_page_payload['items']})

        grouped_search_request = self.factory.get('/api/dashboard/cloud-assets/', {
            'grouped': '1',
            'paginated': '1',
            'group_by': 'user',
            'page': '1',
            'page_size': '10',
            'keyword': 'server-alias',
        })
        grouped_search_request.user = admin
        grouped_search_response = cloud_assets_list(grouped_search_request)
        grouped_search_payload = json.loads(grouped_search_response.content.decode('utf-8'))['data']
        self.assertEqual(grouped_search_response.status_code, 200)
        self.assertEqual(grouped_search_payload['total'], 1)
        self.assertEqual([item['id'] for item in grouped_search_payload['items']], [target_asset.id])

        nickname_search_request = self.factory.get('/api/dashboard/cloud-assets/', {
            'paginated': '1',
            'page': '1',
            'page_size': '10',
            'keyword': '昵称阿尔法',
        })
        nickname_search_request.user = admin
        nickname_search_response = cloud_assets_list(nickname_search_request)
        nickname_search_payload = json.loads(nickname_search_response.content.decode('utf-8'))['data']
        self.assertEqual([item['id'] for item in nickname_search_payload['items']], [target_asset.id])

    def test_cloud_assets_search_expands_to_all_assets_for_matched_user(self):
        admin = get_user_model().objects.create_user(username='admin_asset_user_search_expand', password='x', is_staff=True)
        target_user = TelegramUser.objects.create(
            tg_user_id=991930,
            username='alpha_search_user,backup_alpha_name',
            first_name='搜索昵称甲',
        )
        other_user = TelegramUser.objects.create(tg_user_id=991931, username='other_search_user', first_name='搜索昵称乙')
        target_assets = [
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=target_user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name='search-expand-primary',
                public_ip='10.91.0.10',
                actual_expires_at=timezone.now() + timezone.timedelta(days=30),
                status=CloudAsset.STATUS_RUNNING,
                sort_order=20,
            ),
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=target_user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name='search-expand-secondary',
                public_ip='10.91.0.11',
                actual_expires_at=timezone.now() + timezone.timedelta(days=31),
                status=CloudAsset.STATUS_RUNNING,
                sort_order=10,
            ),
        ]
        decoy_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=other_user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='search-expand-decoy',
            public_ip='10.91.0.12',
            actual_expires_at=timezone.now() + timezone.timedelta(days=32),
            status=CloudAsset.STATUS_RUNNING,
            sort_order=30,
        )

        for keyword in ['10.91.0.10', '@backup_alpha', '昵称甲']:
            request = self.factory.get('/api/dashboard/cloud-assets/', {
                'paginated': '1',
                'page': '1',
                'page_size': '10',
                'keyword': keyword,
            })
            request.user = admin
            response = cloud_assets_list(request)
            payload = json.loads(response.content.decode('utf-8'))['data']
            result_ids = {item['id'] for item in payload['items']}
            self.assertEqual(response.status_code, 200)
            self.assertEqual(result_ids, {item.id for item in target_assets})
            self.assertNotIn(decoy_asset.id, result_ids)

    def test_cloud_asset_expired_filter_excludes_unattached_ip_assets(self):
        admin = get_user_model().objects.create_user(username='admin_asset_expired_unattached_filter', password='x', is_staff=True)
        group = TelegramGroupFilter.objects.create(chat_id=-1001993002, title='Risk Filter Group 2', enabled=True)
        expired_at = timezone.now() - timezone.timedelta(days=1)
        expired_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            telegram_group=group,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='expired-running-asset',
            public_ip='10.88.0.10',
            actual_expires_at=expired_at,
            status=CloudAsset.STATUS_RUNNING,
        )
        unattached_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            telegram_group=group,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='expired-unattached-ip-asset',
            public_ip='10.88.0.11',
            actual_expires_at=expired_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )

        expired_request = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'risk_status': 'expired'})
        expired_request.user = admin
        expired_response = cloud_assets_list(expired_request)
        expired_payload = json.loads(expired_response.content.decode('utf-8'))['data']

        self.assertEqual(expired_response.status_code, 200)
        self.assertEqual([item['id'] for item in expired_payload['items']], [expired_asset.id])
        self.assertEqual(expired_payload['risk_counts']['expired'], 1)
        self.assertEqual(expired_payload['risk_counts']['unattached_ip'], 1)

        unattached_request = self.factory.get('/api/dashboard/cloud-assets/', {'paginated': '1', 'risk_status': 'unattached_ip'})
        unattached_request.user = admin
        unattached_response = cloud_assets_list(unattached_request)
        unattached_payload = json.loads(unattached_response.content.decode('utf-8'))['data']

        self.assertEqual(unattached_response.status_code, 200)
        self.assertEqual([item['id'] for item in unattached_payload['items']], [unattached_asset.id])
        self.assertEqual(unattached_payload['items'][0]['risk_status'], 'unattached_ip')
        self.assertNotIn('expired', unattached_payload['items'][0]['risk_statuses'])

    def test_create_cloud_server_renewal_rejects_deleted_or_ipless_order(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RENEW-1',
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
            public_ip='',
        )

        result = async_to_sync(create_cloud_server_renewal)(order.id, self.user.id, 31)

        self.assertFalse(result)

    def test_apply_cloud_server_renewal_keeps_original_service_started_at(self):
        original_started_at = timezone.now() - timezone.timedelta(days=20)
        original_expiry = timezone.now() + timezone.timedelta(days=10)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RENEW-KEEP-STARTED',
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
            public_ip='8.8.4.8',
            service_started_at=original_started_at,
            service_expires_at=original_expiry,
        )
        with patch('cloud.services._renew_aliyun_instance', return_value=(True, 'ok')), patch('cloud.services._ensure_aws_instance_running', return_value=(False, 'skip start')):
            renewed = async_to_sync(apply_cloud_server_renewal)(order.id, 31, False)

        renewed.refresh_from_db()
        self.assertEqual(renewed.service_started_at, original_started_at)
        self.assertGreater(renewed.service_expires_at, original_expiry)

    def test_renewal_postcheck_skips_running_records(self):
        old_expiry = timezone.now() + timezone.timedelta(days=7)
        new_expiry = timezone.now() + timezone.timedelta(days=38)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RENEW-POSTCHECK-RUNNING',
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
            server_name='renew-postcheck-running',
            instance_id='renew-postcheck-running',
            public_ip='8.8.4.9',
            service_expires_at=new_expiry,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            actual_expires_at=old_expiry,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='running',
            is_active=True,
        )
        server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            expires_at=old_expiry,
            status=Server.STATUS_RUNNING,
            provider_status='running',
            is_active=True,
        )

        with patch('cloud.services._ensure_aws_instance_running') as ensure_running, \
            patch('cloud.services._ensure_mtproxy_after_renewal') as ensure_mtproxy:
            checked, error = async_to_sync(run_cloud_server_renewal_postcheck)(order.id)

        self.assertIsNone(error)
        self.assertEqual(checked.id, order.id)
        ensure_running.assert_not_called()
        ensure_mtproxy.assert_not_called()
        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertIn('已跳过开机和 MTProxy 巡检', order.provision_note)
        self.assertEqual(asset.actual_expires_at, order.service_expires_at)
        self.assertEqual(server.expires_at, order.service_expires_at)

    def test_address_renewal_failure_rolls_back_paid_fields(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-ADDR-RENEW-FAIL',
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
            public_ip='8.8.8.8',
            instance_id='',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=3),
            lifecycle_days=31,
        )

        confirmed = async_to_sync(_confirm_cloud_server_order)(order.id, 'tx-renew-fail', 'payer', 'receiver')

        self.assertIsNone(confirmed)
        order.refresh_from_db()
        self.assertEqual(order.status, 'renew_pending')
        self.assertIsNone(order.paid_at)
        self.assertIsNone(order.tx_hash)
        self.assertEqual(order.payer_address or '', '')
        self.assertEqual(order.receive_address or '', '')

    def test_cloud_upgrade_wallet_payment_is_idempotent(self):
        self.user.balance = Decimal('100.000000')
        self.user.save(update_fields=['balance', 'updated_at'])
        target_plan = CloudServerPlan.objects.create(
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name='Large 2G 60G 3TB',
            cpu='2核',
            memory='2GB',
            storage='60GB SSD',
            bandwidth='3TB',
            price='29.00',
            currency='USDT',
            is_active=True,
            sort_order=101,
        )
        source = CloudServerOrder.objects.create(
            order_no='HB-TEST-UPGRADE-SOURCE',
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
            previous_public_ip='8.8.4.4',
            instance_id='upgrade-source-instance',
            static_ip_name='StaticIp-upgrade-source',
            mtproxy_port=9528,
            mtproxy_secret='0123456789abcdef0123456789abcdef',
            mtproxy_link='tg://proxy?server=8.8.4.4&port=9528&secret=0123456789abcdef0123456789abcdef',
            proxy_links=[{'label': '主链路', 'url': 'tg://proxy?server=8.8.4.4&port=9528&secret=0123456789abcdef0123456789abcdef'}],
            service_started_at=timezone.now() - timezone.timedelta(days=1),
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
        )

        first_order, first_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, target_plan.id)
        balance_after_first = TelegramUser.objects.get(id=self.user.id).balance
        second_order, second_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, target_plan.id)

        self.assertIsNotNone(first_order)
        self.assertIsNone(first_err)
        self.assertIsNone(second_order)
        self.assertIn('已有配置调整任务', second_err)
        self.assertEqual(CloudServerOrder.objects.filter(replacement_for=source).count(), 1)
        self.assertEqual(TelegramUser.objects.get(id=self.user.id).balance, balance_after_first)

    def test_config_change_success_does_not_steal_old_server_record(self):
        source = CloudServerOrder.objects.create(
            order_no='HB-TEST-UPGRADE-SOURCE-SERVER',
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
            public_ip='8.8.4.44',
            previous_public_ip='8.8.4.44',
            server_name='old-config-instance',
            instance_id='old-config-instance',
            provider_resource_id='old-config-instance',
            static_ip_name='StaticIp-config-source',
            mtproxy_port=9528,
            mtproxy_secret='0123456789abcdef0123456789abcdef',
            service_started_at=timezone.now() - timezone.timedelta(days=1),
            service_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        old_server = Server.objects.create(
            order=source,
            user=self.user,
            provider=source.provider,
            account_label=source.provider,
            region_code=source.region_code,
            region_name=source.region_name,
            server_name=source.server_name,
            instance_id=source.instance_id,
            provider_resource_id=source.provider_resource_id,
            public_ip=source.public_ip,
            expires_at=source.service_expires_at,
            status=Server.STATUS_RUNNING,
            is_active=True,
        )
        replacement = CloudServerOrder.objects.create(
            order_no='HB-TEST-UPGRADE-NEW-SERVER',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='29.00',
            pay_amount='10.00',
            pay_method='balance',
            status='provisioning',
            public_ip='10.0.0.10',
            replacement_for=source,
            static_ip_name=source.static_ip_name,
            mtproxy_port=source.mtproxy_port,
            mtproxy_secret=source.mtproxy_secret,
            service_started_at=source.service_started_at,
            service_expires_at=source.service_expires_at,
        )

        async_to_sync(_mark_success)(
            replacement.id,
            'new-config-instance',
            'new-config-instance',
            source.public_ip,
            'ubuntu',
            'secret',
            '配置调整完成',
            source.static_ip_name,
        )

        old_server.refresh_from_db()
        replacement.refresh_from_db()
        new_server = Server.objects.filter(order=replacement).first()
        self.assertEqual(old_server.order_id, source.id)
        self.assertEqual(old_server.instance_id, source.instance_id)
        self.assertIsNotNone(new_server)
        self.assertNotEqual(new_server.id, old_server.id)
        self.assertEqual(new_server.public_ip, source.public_ip)
        self.assertEqual(new_server.expires_at, replacement.service_expires_at)

    def test_asset_renewal_mark_success_starts_new_service_period(self):
        old_release_at = timezone.now() + timezone.timedelta(days=7)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-ASSET-RENEWAL-MARK-SUCCESS',
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
            status='provisioning',
            public_ip='10.0.0.90',
            previous_public_ip='10.0.0.90',
            static_ip_name='StaticIp-asset-renewal-success',
            mtproxy_port=443,
            mtproxy_secret='secret',
            lifecycle_days=31,
            service_expires_at=old_release_at,
            ip_recycle_at=old_release_at,
            provision_note='未绑定代理资产续费：来源资产 #999；旧IP=10.0.0.90。',
        )

        async_to_sync(_mark_success)(
            order.id,
            'asset-renewal-instance',
            'asset-renewal-instance',
            order.public_ip,
            'admin',
            'secret',
            '恢复完成',
            order.static_ip_name,
        )

        order.refresh_from_db()
        asset = CloudAsset.objects.get(order=order, kind=CloudAsset.KIND_SERVER)
        self.assertEqual(order.status, 'completed')
        self.assertGreater(order.service_expires_at, old_release_at)
        self.assertEqual(order.service_expires_at.date(), (order.completed_at + timezone.timedelta(days=31)).date())
        self.assertEqual(asset.actual_expires_at, order.service_expires_at)

    def test_aws_sync_resolver_does_not_match_replacement_by_old_ip(self):
        from cloud.management.commands.sync_aws_assets import _resolve_server

        source = CloudServerOrder.objects.create(
            order_no='HB-TEST-AWS-SYNC-OLD-IP',
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
            public_ip='9.9.9.9',
            previous_public_ip='9.9.9.9',
            server_name='old-sync-instance',
            instance_id='old-sync-instance',
            provider_resource_id='old-sync-instance',
            service_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        replacement = CloudServerOrder.objects.create(
            order_no='HB-TEST-AWS-SYNC-NEW-IP',
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
            status='provisioning',
            public_ip='9.9.9.9',
            replacement_for=source,
            server_name='new-sync-instance',
            instance_id='new-sync-instance',
            provider_resource_id='new-sync-instance',
            service_expires_at=source.service_expires_at,
        )
        old_server = Server.objects.create(
            order=source,
            user=self.user,
            provider='aws_lightsail',
            account_label='aws_lightsail',
            region_code=source.region_code,
            region_name=source.region_name,
            server_name=source.server_name,
            instance_id=source.instance_id,
            provider_resource_id=source.provider_resource_id,
            public_ip=source.public_ip,
            expires_at=source.service_expires_at,
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        resolved = _resolve_server(replacement.instance_id, replacement.provider_resource_id, replacement.public_ip, replacement)

        self.assertIsNone(resolved)
        old_server.refresh_from_db()
        self.assertEqual(old_server.order_id, source.id)

    def test_aws_sync_resolver_prefers_ip_over_changed_instance_name(self):
        from cloud.management.commands.sync_aws_assets import _resolve_asset, _resolve_order_for_instance_sync, _resolve_server

        stable_ip_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-AWS-SYNC-IP-FIRST',
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
            public_ip='8.8.8.8',
            previous_public_ip='8.8.8.8',
            server_name='old-instance-name',
            instance_id='old-instance-name',
            provider_resource_id='old-instance-arn',
            service_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        dirty_name_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-AWS-SYNC-DIRTY-NAME',
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
            public_ip='7.7.7.7',
            server_name='new-instance-name',
            instance_id='new-instance-name',
            provider_resource_id='new-instance-arn',
            service_expires_at=stable_ip_order.service_expires_at,
        )
        ip_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=stable_ip_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='old-instance-name',
            instance_id='old-instance-name',
            provider_resource_id='old-instance-arn',
            public_ip='8.8.8.8',
            status=CloudAsset.STATUS_RUNNING,
        )
        dirty_name_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=dirty_name_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='new-instance-name',
            instance_id='new-instance-name',
            provider_resource_id='new-instance-arn',
            public_ip='7.7.7.7',
            status=CloudAsset.STATUS_RUNNING,
        )
        ip_server = Server.objects.create(
            order=stable_ip_order,
            user=self.user,
            provider='aws_lightsail',
            account_label='aws_lightsail',
            region_code=stable_ip_order.region_code,
            region_name=stable_ip_order.region_name,
            server_name='old-instance-name',
            instance_id='old-instance-name',
            provider_resource_id='old-instance-arn',
            public_ip='8.8.8.8',
            status=Server.STATUS_RUNNING,
            is_active=True,
        )
        Server.objects.create(
            order=dirty_name_order,
            user=self.user,
            provider='aws_lightsail',
            account_label='aws_lightsail',
            region_code=dirty_name_order.region_code,
            region_name=dirty_name_order.region_name,
            server_name='new-instance-name',
            instance_id='new-instance-name',
            provider_resource_id='new-instance-arn',
            public_ip='7.7.7.7',
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        resolved_order = _resolve_order_for_instance_sync('new-instance-name', 'new-instance-arn', '8.8.8.8')
        resolved_asset = _resolve_asset('new-instance-name', 'new-instance-arn', '8.8.8.8', resolved_order)
        resolved_server = _resolve_server('new-instance-name', 'new-instance-arn', '8.8.8.8', resolved_order)

        self.assertEqual(resolved_order.id, stable_ip_order.id)
        self.assertEqual(resolved_asset.id, ip_asset.id)
        self.assertEqual(resolved_server.id, ip_server.id)
        self.assertNotEqual(resolved_asset.id, dirty_name_asset.id)

    def test_cloud_config_change_lists_and_creates_downgrade_order(self):
        small_plan = CloudServerPlan.objects.create(
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name='Nano 512M 20G 1TB',
            cpu='1核',
            memory='512MB',
            storage='20GB SSD',
            bandwidth='1TB',
            price='10.00',
            currency='USDT',
            is_active=True,
            sort_order=99,
        )
        source = CloudServerOrder.objects.create(
            order_no='HB-TEST-DOWNGRADE-SOURCE',
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
            public_ip='8.8.4.5',
            previous_public_ip='8.8.4.5',
            instance_id='downgrade-source-instance',
            static_ip_name='StaticIp-downgrade-source',
            mtproxy_port=9528,
            mtproxy_secret='0123456789abcdef0123456789abcdef',
            mtproxy_link='tg://proxy?server=8.8.4.5&port=9528&secret=0123456789abcdef0123456789abcdef',
            proxy_links=[{'label': '主链路', 'url': 'tg://proxy?server=8.8.4.5&port=9528&secret=0123456789abcdef0123456789abcdef'}],
            service_started_at=timezone.now() - timezone.timedelta(days=1),
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
        )

        plans, err = async_to_sync(list_cloud_server_upgrade_plans)(source.id, self.user.id)
        new_order, create_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, small_plan.id)

        self.assertIsNone(err)
        self.assertTrue(any(plan['id'] == small_plan.id and plan['action'] == 'downgrade' for plan in plans))
        self.assertIsNone(create_err)
        self.assertIsNotNone(new_order)
        self.assertEqual(new_order.plan_id, small_plan.id)
        self.assertEqual(new_order.pay_amount, Decimal('0.000000000'))
        self.assertIn('DOWNGRADE', new_order.order_no)

    def test_cloud_config_change_ceil_custom_price_to_plan_tier(self):
        self.user.balance = Decimal('100.000000')
        self.user.save(update_fields=['balance', 'updated_at'])
        small_plan = CloudServerPlan.objects.create(
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name='Nano 512M 20G 1TB',
            cpu='1核',
            memory='512MB',
            storage='20GB SSD',
            bandwidth='1TB',
            price='10.00',
            currency='USDT',
            is_active=True,
            sort_order=99,
        )
        large_plan = CloudServerPlan.objects.create(
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name='Large 2G 60G 3TB',
            cpu='2核',
            memory='2GB',
            storage='60GB SSD',
            bandwidth='3TB',
            price='29.00',
            currency='USDT',
            is_active=True,
            sort_order=101,
        )
        source = CloudServerOrder.objects.create(
            order_no='HB-TEST-CEIL-PRICE-SOURCE',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='15.00',
            pay_amount='15.00',
            pay_method='balance',
            status='completed',
            public_ip='8.8.4.6',
            previous_public_ip='8.8.4.6',
            instance_id='ceil-source-instance',
            static_ip_name='StaticIp-ceil-source',
            mtproxy_port=9528,
            mtproxy_secret='0123456789abcdef0123456789abcdef',
            mtproxy_link='tg://proxy?server=8.8.4.6&port=9528&secret=0123456789abcdef0123456789abcdef',
            proxy_links=[{'label': '主链路', 'url': 'tg://proxy?server=8.8.4.6&port=9528&secret=0123456789abcdef0123456789abcdef'}],
            service_started_at=timezone.now() - timezone.timedelta(days=1),
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
        )

        plans, err = async_to_sync(list_cloud_server_upgrade_plans)(source.id, self.user.id)
        large = next(plan for plan in plans if plan['id'] == large_plan.id)
        same_order, same_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, self.plan.id)
        large_order, large_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, large_plan.id)

        self.assertIsNone(err)
        self.assertTrue(any(plan['id'] == small_plan.id and plan['action'] == 'downgrade' for plan in plans))
        self.assertEqual(large['diff'], '10.000')
        self.assertIsNone(same_order)
        self.assertEqual(same_err, '目标套餐与当前配置相同')
        self.assertIsNone(large_err)
        self.assertEqual(large_order.pay_amount, Decimal('10.000000000'))
