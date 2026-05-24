from .common import *


class CloudServerDashboardAssetApiMixin:
    def test_dashboard_order_ip_and_name_update_syncs_asset_server(self):
        order = CloudServerOrder.objects.create(
            order_no='DASH-ORDER-IP-NAME-UPDATE-1',
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
            public_ip='4.4.4.40',
            server_name='old-dashboard-name',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=20),
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
        )
        Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name=order.server_name,
            public_ip=order.public_ip,
            expires_at=order.service_expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_order_ip_name_update', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-orders/{order.id}/',
            data=json.dumps({'public_ip': '4.4.4.41', 'server_name': 'new-dashboard-name'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = cloud_order_detail(request, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset = CloudAsset.objects.get(order=order)
        server = Server.objects.get(order=order)
        self.assertEqual(order.public_ip, '4.4.4.41')
        self.assertEqual(order.previous_public_ip, '4.4.4.40')
        self.assertEqual(order.server_name, 'new-dashboard-name')
        self.assertEqual(asset.public_ip, '4.4.4.41')
        self.assertEqual(asset.previous_public_ip, '4.4.4.40')
        self.assertEqual(asset.asset_name, 'new-dashboard-name')
        self.assertEqual(server.public_ip, '4.4.4.41')
        self.assertEqual(server.previous_public_ip, '4.4.4.40')
        self.assertEqual(server.server_name, 'new-dashboard-name')

    def test_dashboard_asset_ip_update_syncs_order_previous_ip(self):
        order = CloudServerOrder.objects.create(
            order_no='DASH-ASSET-IP-UPDATE-1',
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
            public_ip='4.4.4.42',
            server_name='asset-ip-update-server',
            service_started_at=timezone.now(),
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
            public_ip=order.public_ip,
            actual_expires_at=order.service_expires_at,
        )
        Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name=order.server_name,
            public_ip=order.public_ip,
            expires_at=order.service_expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_ip_update', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'public_ip': '4.4.4.43'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        server = Server.objects.get(order=order)
        self.assertEqual(order.public_ip, '4.4.4.43')
        self.assertEqual(order.previous_public_ip, '4.4.4.42')
        self.assertEqual(asset.public_ip, '4.4.4.43')
        self.assertEqual(asset.previous_public_ip, '4.4.4.42')
        self.assertEqual(server.public_ip, '4.4.4.43')
        self.assertEqual(server.previous_public_ip, '4.4.4.42')

    def test_dashboard_asset_ip_update_uses_asset_old_ip_when_server_was_pre_synced(self):
        order = CloudServerOrder.objects.create(
            order_no='DASH-ASSET-IP-PRESYNC-1',
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
            public_ip='4.4.4.44',
            server_name='asset-ip-presync-server',
            service_started_at=timezone.now(),
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
            public_ip=order.public_ip,
            actual_expires_at=order.service_expires_at,
        )
        Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name=order.server_name,
            public_ip='4.4.4.45',
            expires_at=order.service_expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_ip_presync', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'public_ip': '4.4.4.45'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        server = Server.objects.get(order=order)
        self.assertEqual(order.public_ip, '4.4.4.45')
        self.assertEqual(order.previous_public_ip, '4.4.4.44')
        self.assertEqual(asset.public_ip, '4.4.4.45')
        self.assertEqual(asset.previous_public_ip, '4.4.4.44')
        self.assertEqual(server.public_ip, '4.4.4.45')
        self.assertEqual(server.previous_public_ip, '4.4.4.44')
        log = CloudIpLog.objects.filter(asset=asset, event_type=CloudIpLog.EVENT_CHANGED).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.previous_public_ip, '4.4.4.44')
        self.assertEqual(log.public_ip, '4.4.4.45')

    def test_dashboard_asset_update_does_not_touch_cross_account_same_instance_server(self):
        order = CloudServerOrder.objects.create(
            order_no='DASH-ASSET-SCOPED-SERVER-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            account_label='aws+111+primary',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            instance_id='same-instance-scoped',
            public_ip='4.4.4.50',
            server_name='scoped-server-primary',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            account_label=order.account_label,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            actual_expires_at=order.service_expires_at,
        )
        wrong_server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            provider=order.provider,
            account_label='aws+222+secondary',
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='scoped-server-secondary',
            instance_id=order.instance_id,
            public_ip='4.4.4.99',
        )
        right_server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            account_label=order.account_label,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            expires_at=order.service_expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_scoped_server', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'public_ip': '4.4.4.51'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        right_server.refresh_from_db()
        wrong_server.refresh_from_db()
        self.assertEqual(right_server.public_ip, '4.4.4.51')
        self.assertEqual(wrong_server.public_ip, '4.4.4.99')

    def test_dashboard_asset_update_matches_legacy_colon_account_label(self):
        account = self._aws_test_account()
        plus_label = cloud_account_label(account)
        legacy_label = f'aws:{account.id}:{account.name}'
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider=self.plan.provider,
            cloud_account=account,
            account_label=plus_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='legacy-label-server',
            instance_id='legacy-label-server',
            public_ip='4.4.4.70',
            actual_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            provider=self.plan.provider,
            account_label=legacy_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='legacy-label-server',
            instance_id='legacy-label-server',
            public_ip='4.4.4.70',
            expires_at=asset.actual_expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_legacy_label_update', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'public_ip': '4.4.4.71'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        server.refresh_from_db()
        self.assertEqual(server.public_ip, '4.4.4.71')
        self.assertEqual(server.account_label, legacy_label)

    def test_dashboard_asset_update_created_server_preserves_account_label(self):
        account = self._aws_test_account()
        label = cloud_account_label(account)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider=self.plan.provider,
            cloud_account=account,
            account_label=label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='create-server-account-scope',
            instance_id='i-create-server-account-scope',
            public_ip='4.4.4.61',
            actual_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        staff_user = get_user_model().objects.create_user(username='staff_create_server_account_label', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'note': '触发补建服务器记录'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        server = Server.objects.get(instance_id='i-create-server-account-scope')
        self.assertEqual(server.account_label, label)
        self.assertEqual(server.provider, self.plan.provider)
        self.assertEqual(server.region_code, self.plan.region_code)

    def test_aws_notice_schedule_does_not_override_manual_order_expiry(self):
        order = CloudServerOrder.objects.create(
            order_no='MANUAL-NOTICE-OLD-1',
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
            service_expires_at=timezone.now() + timezone.timedelta(days=15),
        )
        manual_expiry = order.service_expires_at
        notice_expiry = timezone.now() + timezone.timedelta(days=5)

        _apply_notice_schedule_to_order(order, {
            'expires_at': notice_expiry,
            'suspend_at': notice_expiry,
            'delete_at': notice_expiry + timezone.timedelta(days=3),
            'ip_recycle_at': notice_expiry + timezone.timedelta(days=7),
        })

        order.refresh_from_db()
        self.assertEqual(order.service_expires_at, manual_expiry)
        self.assertEqual(order.suspend_at, notice_expiry)

    def test_rebuild_payload_prefers_source_account_when_rebuild_order_is_polluted(self):
        source_account = CloudAccountConfig.objects.create(
            provider='aws',
            name='22',
            external_account_id='039612864876',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
        )
        wrong_account = CloudAccountConfig.objects.create(
            provider='aws',
            name='11',
            external_account_id='172678727708',
            access_key='ak2',
            secret_key='sk2',
            region_hint='ap-southeast-1',
        )
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-SOURCE-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=source_account,
            account_label='aws+039612864876+22',
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
            mtproxy_secret='ee1234567890abcdef1234567890abcd',
        )
        rebuild_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-SOURCE-2',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=wrong_account,
            account_label='aws+172678727708+11',
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

        payload = async_to_sync(_get_aws_create_payload)(rebuild_order.id)
        account_ids = async_to_sync(_candidate_cloud_account_ids)(rebuild_order.id)

        self.assertEqual(payload['cloud_account_id'], source_account.id)
        self.assertEqual(payload['account_label'], source_order.account_label)
        self.assertEqual(account_ids, [source_account.id])

    def test_asset_operation_order_resolves_account_from_label(self):
        account = CloudAccountConfig.objects.create(
            provider='aws',
            name='22',
            external_account_id='039612864876',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws+039612864876+22',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='Debian-1',
            instance_id='Debian-1',
            public_ip='3.1.169.183',
            user=self.user,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        order, error = async_to_sync(ensure_cloud_asset_operation_order)(asset.id, self.user.id)

        self.assertIsNone(error)
        self.assertIsNotNone(order)
        self.assertEqual(order.cloud_account_id, account.id)
        self.assertEqual(order.account_label, asset.account_label)

    def test_deleted_retained_order_without_active_static_ip_is_not_query_result(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-DELETED-RETAINED-MISSING',
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
            status='deleted',
            public_ip='54.255.96.64',
            previous_public_ip='54.255.96.64',
            static_ip_name='released-static-ip',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=10),
        )
        CloudAsset.objects.create(
            order=order,
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='released-static-ip',
            public_ip='54.255.96.64',
            status=CloudAsset.STATUS_DELETED,
            provider_status='云上未找到',
            note='云上不存在，已标记删除',
            is_active=False,
        )

        result = async_to_sync(get_cloud_server_by_ip_for_user)('54.255.96.64', self.user.id)
        retained_order, plans, err = async_to_sync(list_retained_ip_renewal_plans)(order.id, self.user.id)

        self.assertIsNone(result)
        self.assertEqual(retained_order.id, order.id)
        self.assertEqual(plans, [])
        self.assertIsNone(err)

    def test_completed_order_without_instance_and_released_static_ip_is_not_query_result(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-COMPLETED-RELEASED-IP',
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
            public_ip='54.255.96.65',
            previous_public_ip='54.255.96.65',
            static_ip_name='released-static-ip-completed',
            instance_id='',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=10),
        )
        CloudAsset.objects.create(
            order=order,
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='released-static-ip-completed',
            public_ip='54.255.96.65',
            status=CloudAsset.STATUS_DELETED,
            provider_status='云上未找到',
            note='云上不存在，已标记删除',
            is_active=False,
        )

        result = async_to_sync(get_cloud_server_by_ip_for_user)('54.255.96.65', self.user.id)
        retained_order, plans, err = async_to_sync(list_retained_ip_renewal_plans)(order.id, self.user.id)

        self.assertIsNone(result)
        self.assertEqual(retained_order.id, order.id)
        self.assertEqual(plans, [])
        self.assertIsNone(err)

    def test_unbound_asset_renewal_lists_plans_without_creating_order(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unbound-renewal-plan-list',
            public_ip='31.31.31.30',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        returned_asset, plans, error = async_to_sync(list_cloud_asset_renewal_plans)(asset.id, self.user.id)
        asset.refresh_from_db()

        self.assertIsNone(error)
        self.assertEqual(returned_asset.id, asset.id)
        self.assertTrue(plans)
        self.assertIsNone(asset.order_id)

    def test_prepare_unbound_asset_renewal_creates_pending_payment_order(self):
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
            asset_name='unbound-renewal-payment',
            public_ip='31.31.31.32',
            previous_public_ip='31.31.31.32',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.32&port=9528&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.31.32',
            'port': '9528',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        order, error = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)
        asset.refresh_from_db()

        self.assertIsNone(error)
        self.assertIsNotNone(order)
        self.assertEqual(order.status, 'pending')
        self.assertEqual(order.plan_id, self.plan.id)
        self.assertEqual(order.pay_method, 'address')
        self.assertIsNone(order.service_expires_at)
        self.assertEqual(order.ip_recycle_at, due_at)
        self.assertEqual(order.mtproxy_link, link['url'])
        self.assertEqual(asset.order_id, order.id)

    def test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery(self):
        self.user.balance = Decimal('100.000000')
        self.user.save(update_fields=['balance', 'updated_at'])
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
            asset_name='unbound-renewal-wallet-payment',
            public_ip='31.31.31.33',
            previous_public_ip='31.31.31.33',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.33&port=443&secret=eed5c148e2922f6c49611e7d53fe432a94617a7572652e6d6963726f736f66742e636f6d',
            'server': '31.31.31.33',
            'port': '443',
            'secret': 'eed5c148e2922f6c49611e7d53fe432a94617a7572652e6d6963726f736f66742e636f6d',
        }
        order, error = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)

        paid_order, pay_error = async_to_sync(pay_cloud_server_renewal_with_balance)(order.id, self.user.id, 'USDT', 31)

        self.assertIsNone(error)
        self.assertIsNone(pay_error)
        self.assertEqual(paid_order.id, order.id)
        self.assertEqual(paid_order.status, 'paid')
        self.assertEqual(paid_order.pay_method, 'balance')
        self.assertIsNotNone(paid_order.paid_at)
        self.assertIsNone(paid_order.service_expires_at)
        self.assertEqual(paid_order.ip_recycle_at, due_at)
        self.assertIn('正在恢复未绑定代理资产固定 IP', paid_order.provision_note)

    def test_unbound_asset_renewal_wallet_payment_repairs_completed_unpaid_state(self):
        self.user.balance = Decimal('100.000000')
        self.user.save(update_fields=['balance', 'updated_at'])
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
            asset_name='unbound-renewal-wallet-repair',
            public_ip='31.31.31.34',
            previous_public_ip='31.31.31.34',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.34&port=443&secret=eed5c148e2922f6c49611e7d53fe432a94617a7572652e6d6963726f736f66742e636f6d',
            'server': '31.31.31.34',
            'port': '443',
            'secret': 'eed5c148e2922f6c49611e7d53fe432a94617a7572652e6d6963726f736f66742e636f6d',
        }
        order, _ = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)
        CloudServerOrder.objects.filter(id=order.id).update(status='completed', paid_at=None, instance_id='', service_expires_at=due_at)

        paid_order, pay_error = async_to_sync(pay_cloud_server_renewal_with_balance)(order.id, self.user.id, 'USDT', 31)

        self.assertIsNone(pay_error)
        self.assertEqual(paid_order.status, 'paid')
        self.assertIsNotNone(paid_order.paid_at)
        self.assertIsNone(paid_order.service_expires_at)
        self.assertEqual(paid_order.ip_recycle_at, due_at)

    def test_completed_asset_recovery_order_renews_without_reprovisioning(self):
        self.user.balance = Decimal('100.000000')
        self.user.save(update_fields=['balance', 'updated_at'])
        completed_at = timezone.now() - timezone.timedelta(days=1)
        old_expiry = timezone.now() + timezone.timedelta(days=10)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-ASSET-RECOVERY-NORMAL-RENEW',
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
            status='renew_pending',
            public_ip='31.31.31.36',
            instance_id='recovered-instance-36',
            static_ip_name='StaticIp-recovered-36',
            mtproxy_port=443,
            mtproxy_secret='secret',
            service_started_at=completed_at,
            service_expires_at=old_expiry,
            provision_note='未绑定代理资产续费：来源资产 #999；恢复完成。',
        )

        renewed, pay_error = async_to_sync(pay_cloud_server_renewal_with_balance)(order.id, self.user.id, 'USDT', 31)

        self.assertIsNone(pay_error)
        self.assertEqual(renewed.status, 'completed')
        self.assertEqual(renewed.instance_id, 'recovered-instance-36')
        self.assertGreater(renewed.service_expires_at, old_expiry)
        self.assertIsNotNone(renewed.paid_at)

    def test_unbound_asset_renewal_chain_payment_marks_paid_for_recovery(self):
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
            asset_name='unbound-renewal-chain-payment',
            public_ip='31.31.31.35',
            previous_public_ip='31.31.31.35',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.35&port=443&secret=eed5c148e2922f6c49611e7d53fe432a94617a7572652e6d6963726f736f66742e636f6d',
            'server': '31.31.31.35',
            'port': '443',
            'secret': 'eed5c148e2922f6c49611e7d53fe432a94617a7572652e6d6963726f736f66742e636f6d',
        }
        order, error = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)

        confirmed = async_to_sync(_confirm_cloud_server_order)(order.id, '0xassetrenewalchainpayment', 'payer', 'receiver')

        self.assertIsNone(error)
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed.status, 'paid')
        self.assertIsNotNone(confirmed.paid_at)
        self.assertIsNone(confirmed.service_expires_at)
        self.assertEqual(confirmed.ip_recycle_at, due_at)
        self.assertIn('正在恢复未绑定代理资产固定 IP', confirmed.provision_note)

    def test_unsynced_deleted_aws_asset_prepares_static_ip_recovery(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='gray-zone-account',
            region_hint=self.plan.region_code,
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        due_at = timezone.now() + timezone.timedelta(days=9)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='gray-zone-stale-instance',
            instance_id='gray-zone-stale-instance',
            public_ip='31.31.31.38',
            previous_public_ip='31.31.31.38',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            note='AWS 已删机但同步未更新，DB 仍是运行中资产',
            mtproxy_port=443,
            mtproxy_link='tg://proxy?server=31.31.31.38&port=443&secret=eeeeeeeeeeeeeeee',
            mtproxy_secret='eeeeeeeeeeeeeeee',
            mtproxy_host='31.31.31.38',
            is_active=True,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.38&port=443&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.31.38',
            'port': '443',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        with patch('cloud.services._resolve_unattached_aws_static_ip_name_for_asset', return_value='StaticIp-gray-zone'):
            order, error = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)

        self.assertIsNone(error)
        self.assertIsNotNone(order)
        self.assertEqual(order.static_ip_name, 'StaticIp-gray-zone')
        self.assertEqual(order.cloud_account_id, account.id)
        self.assertIn('灰区续费：AWS 实时确认固定 IP 未附加', order.provision_note)

    def test_unattached_asset_operation_order_enters_retained_renewal_flow(self):
        due_at = timezone.now() + timezone.timedelta(days=9)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-retained-flow',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-retained-flow',
            public_ip='31.31.31.31',
            previous_public_ip='31.31.31.31',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            mtproxy_port=9528,
            mtproxy_link='tg://proxy?server=31.31.31.31&port=9528&secret=dddddddddddddddd',
            mtproxy_secret='dddddddddddddddd',
            mtproxy_host='31.31.31.31',
            is_active=False,
        )

        order, error = async_to_sync(ensure_cloud_asset_operation_order)(asset.id, self.user.id)

        self.assertIsNone(error)
        self.assertIsNotNone(order)
        self.assertEqual(order.ip_recycle_at, due_at)
        retained_order, plans, retained_err = async_to_sync(list_retained_ip_renewal_plans)(order.id, self.user.id)
        self.assertIsNone(retained_err)
        self.assertIsNotNone(retained_order)
        self.assertTrue(plans)
        self.assertEqual(retained_order.id, order.id)

    def test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing(self):
        original_expires_at = timezone.now() + timezone.timedelta(days=31)
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REPLACE-1',
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
            service_started_at=timezone.now(),
            service_expires_at=original_expires_at,
        )

        new_order = async_to_sync(mark_cloud_server_ip_change_requested)(source_order.id, self.user.id)

        source_order.refresh_from_db()
        self.assertTrue(new_order)
        self.assertEqual(new_order.plan_id, self.plan.id)
        self.assertEqual(new_order.replacement_for_id, source_order.id)
        self.assertEqual(new_order.service_expires_at, original_expires_at)
        self.assertIsNotNone(source_order.migration_due_at)
        self.assertEqual(source_order.service_expires_at, source_order.migration_due_at)
        self.assertEqual(source_order.suspend_at, source_order.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(source_order.delete_at, source_order.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(source_order.renew_grace_expires_at, source_order.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(
            source_order.ip_recycle_at,
            source_order.delete_at + timezone.timedelta(days=15),
        )

    def test_mark_cloud_server_ip_change_requested_returns_existing_replacement(self):
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REPLACE-EXISTING',
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
            public_ip='11.22.33.44',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
            ip_change_quota=1,
        )

        first_order = async_to_sync(mark_cloud_server_ip_change_requested)(source_order.id, self.user.id)
        second_order = async_to_sync(mark_cloud_server_ip_change_requested)(source_order.id, self.user.id)

        source_order.refresh_from_db()
        self.assertIsNotNone(first_order)
        self.assertIsNotNone(second_order)
        self.assertEqual(first_order.id, second_order.id)
        self.assertEqual(CloudServerOrder.objects.filter(replacement_for=source_order).count(), 1)
        self.assertEqual(source_order.ip_change_quota, 0)

    def test_mark_provisioning_start_creates_pending_asset_server_and_log(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-PROVISION-1',
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
        )

        async_to_sync(_mark_provisioning_start)(order.id, 'sg-test-node-01')

        order.refresh_from_db()
        asset = CloudAsset.objects.get(order=order, kind=CloudAsset.KIND_SERVER)
        server = Server.objects.get(order=order)
        log = CloudIpLog.objects.filter(order=order).latest('id')

        self.assertEqual(order.status, 'provisioning')
        self.assertEqual(order.server_name, 'sg-test-node-01')
        self.assertEqual(asset.status, CloudAsset.STATUS_PENDING)
        self.assertTrue(asset.is_active)
        self.assertEqual(server.status, Server.STATUS_PENDING)
        self.assertTrue(server.is_active)
        self.assertEqual(log.event_type, CloudIpLog.EVENT_CREATED)
        self.assertIn('服务器开始创建', log.note)

    def test_extract_mtproxy_fields_keeps_fake_tls_secret_and_link(self):
        link, secret, host = _extract_mtproxy_fields(
            'MTProxy 安装完成\n'
            '状态: 运行正常\n'
            '端口: 8443\n'
            'TG链接: tg://proxy?server=1.2.3.4&port=8443&secret=ee1234567890abcdef1234567890abcd617a7572652e6d6963726f736f66742e636f6d\n'
            '分享链接: https://t.me/proxy?server=1.2.3.4&port=8443&secret=ee1234567890abcdef1234567890abcd617a7572652e6d6963726f736f66742e636f6d'
        )
        self.assertEqual(host, '1.2.3.4')
        self.assertEqual(link, 'tg://proxy?server=1.2.3.4&port=8443&secret=ee1234567890abcdef1234567890abcd617a7572652e6d6963726f736f66742e636f6d')
        self.assertEqual(secret, 'ee1234567890abcdef1234567890abcd617a7572652e6d6963726f736f66742e636f6d')

    def test_mark_success_updates_existing_server_asset_instead_of_creating_duplicate(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-PROVISION-2',
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
            mtproxy_port=8443,
        )

        async_to_sync(_mark_provisioning_start)(order.id, 'sg-test-node-02')
        async_to_sync(_mark_success)(
            order.id,
            'sg-test-node-02',
            'ins-001',
            '1.2.3.4',
            'root',
            'pass',
            'TG链接: tg://proxy?server=1.2.3.4&port=8443&secret=ee1234567890abcdef1234567890abcd',
            '',
        )

        self.assertEqual(CloudAsset.objects.filter(order=order, kind=CloudAsset.KIND_SERVER).count(), 1)
        asset = CloudAsset.objects.get(order=order, kind=CloudAsset.KIND_SERVER)
        self.assertEqual(asset.instance_id, 'ins-001')
        self.assertEqual(asset.public_ip, '1.2.3.4')
        self.assertIn('tg://proxy?', asset.mtproxy_link or '')
        self.assertEqual(asset.mtproxy_port, 8443)

    def test_sync_aws_assets_requires_database_cloud_account(self):
        with self.assertRaisesMessage(CommandError, '未添加启用的 AWS 云账号'):
            call_command('sync_aws_assets', region='ap-southeast-1')

    def test_backup_ports_are_fixed(self):
        self.assertTrue(is_valid_mtproxy_main_port(443))
        self.assertFalse(is_valid_mtproxy_main_port(444))
        self.assertFalse(is_valid_mtproxy_main_port(9529))
        self.assertFalse(is_valid_mtproxy_main_port(9534))
        self.assertFalse(is_valid_mtproxy_main_port(65530))
        self.assertEqual(get_mtproxy_public_ports(443), [443, 9529, 9530, 9531, 9532, 9533, 9534])
        self.assertEqual(get_mtproxy_public_ports(8443), [8443, 9529, 9530, 9531, 9532, 9533, 9534])
        self.assertEqual(get_mtproxy_port_label(443, 9529), '备用 mtprotoproxy')
        self.assertEqual(get_mtproxy_port_label(443, 9534), 'SOCKS5')

    def test_mtproxy_script_runs_mtg_with_fake_tls_secret(self):
        script = _build_mtproxy_script(443, 'eec3bda48fee649e9ea6e32d33cd5f3dd9617a7572652e6d6963726f736f66742e636f6d')
        self.assertIn('RUN_SECRET="ee${RUN_SECRET}617a7572652e6d6963726f736f66742e636f6d"', script)
        self.assertIn('$WORKDIR/bin/mtg run $RUN_SECRET', script)

    def test_mtproxy_extra_links_exclude_main_port(self):
        links = _extract_tg_links(
            'TG链接: tg://proxy?server=1.2.3.4&port=443&secret=ee11111111111111111111111111111111\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=443&secret=ee22222222222222222222222222222222\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=9529&secret=ee33333333333333333333333333333333',
            exclude_port=443,
        )
        self.assertEqual(links, ['tg://proxy?server=1.2.3.4&port=9529&secret=ee33333333333333333333333333333333'])

    def test_extract_proxy_links_labels_custom_low_port_plan(self):
        links = _extract_proxy_links(
            'MTProxy 安装完成\n'
            '端口: 443\n'
            'TG链接: tg://proxy?server=1.2.3.4&port=443&secret=ee1234567890abcdef1234567890abcd\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=9529&secret=eeabcdefabcdefabcdefabcdefabcdefab\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=9530&secret=eeabcdefabcdefabcdefabcdefabcdefab\n'
            'SOCKS5链接: socks5://abcdefabcdefabcdefabcdefabcdefab:abcdefabcdefabcdefabcdefabcdefab@1.2.3.4:9534'
        )
        self.assertEqual([item['name'] for item in links], ['主代理 mtg', '备用 mtprotoproxy', 'Telemt A 三模式', 'SOCKS5'])
        self.assertEqual(links[-1]['username'], 'abcdefabcdefabcdefabcdefabcdefab')
        self.assertEqual(links[-1]['password'], 'abcdefabcdefabcdefabcdefabcdefab')

    def test_compact_proxy_install_note_removes_raw_links(self):
        note = (
            'AWS 实例已创建\n'
            'MTProxy 安装完成\n'
            'TG链接: tg://proxy?server=1.2.3.4&port=443&secret=ee1234\n'
            'SOCKS5链接: socks5://abcdefabcdefabcdefabcdefabcdefab:abcdefabcdefabcdefabcdefabcdefab@1.2.3.4:9534\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=9530&secret=eeabcd'
        )
        links = _extract_proxy_links(note)
        compact = _compact_proxy_install_note(note, links, 443)

        self.assertIn('AWS 实例已创建', compact)
        self.assertIn('MTProxy/SOCKS5 安装完成', compact)
        self.assertIn('SOCKS5端口: 9534', compact)
        self.assertIn('代理链接已保存到代理链路列表。', compact)
        self.assertNotIn('tg://proxy?', compact)
        self.assertNotIn('socks5://', compact)

    def test_append_status_note_replaces_old_sync_status(self):
        note = append_status_note(
            '人工备注\n状态: 运行中；公网IP: 1.1.1.1；最近同步: old',
            '状态: 运行中；公网IP: 1.1.1.1；最近同步: new',
        )

        self.assertEqual(note, '人工备注\n状态: 运行中；公网IP: 1.1.1.1；最近同步: new')

    def test_cloud_asset_note_display_hides_install_and_sync_noise(self):
        note = _display_cloud_asset_note(
            '人工备注保留\n'
            'TG链接: tg://proxy?server=1.2.3.4&port=443&secret=ee1234\n'
            'Get:1 https://cdn-aws.deb.debian.org/debian bookworm InRelease [151 kB]\n'
            'Reading package lists...\n'
            'SOCKS5链接: socks5://secret:secret@1.2.3.4:9534\n'
            'BBR 执行完成\n'
            '状态: 运行中；公网IP: 1.1.1.1；最近同步: old\n'
            '状态: 运行中；公网IP: 1.1.1.1；最近同步: new\n'
            '人工备注保留'
        )

        self.assertEqual(note, '人工备注保留\nBBR 执行完成')

    def test_cloud_asset_note_appends_clean_install_summary(self):
        note = _append_cloud_asset_note(
            '人工备注保留\nTG链接: tg://proxy?server=old&port=443&secret=old',
            'MTProxy 安装完成\nTG链接: tg://proxy?server=1.2.3.4&port=443&secret=ee1234\nSOCKS5链接: socks5://secret:secret@1.2.3.4:9534',
            [
                {'name': '主代理 mtg', 'port': '443', 'url': 'tg://proxy?server=1.2.3.4&port=443&secret=ee1234'},
                {'name': 'SOCKS5', 'port': '9534', 'url': 'socks5://secret:secret@1.2.3.4:9534'},
            ],
            443,
        )

        self.assertIn('人工备注保留', note)
        self.assertIn('TG链接: tg://proxy?server=old&port=443&secret=old', note)
        self.assertIn('MTProxy/SOCKS5 安装完成', note)
        self.assertIn('SOCKS5端口: 9534', note)
        self.assertNotIn('socks5://secret:secret@1.2.3.4:9534', note)

    def test_mark_success_preserves_existing_main_link_when_install_output_lacks_link(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-LINK',
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
            public_ip='1.2.3.4',
            mtproxy_port=443,
            mtproxy_secret='ee1234567890abcdef1234567890abcd',
            mtproxy_link='tg://proxy?server=1.2.3.4&port=443&secret=ee1234567890abcdef1234567890abcd',
            proxy_links=[{'name': '主代理 mtg', 'server': '1.2.3.4', 'port': '443', 'secret': 'ee1234567890abcdef1234567890abcd', 'url': 'tg://proxy?server=1.2.3.4&port=443&secret=ee1234567890abcdef1234567890abcd'}],
        )

        async_to_sync(_mark_success)(
            order.id,
            'sg-test-node-03',
            'ins-003',
            '1.2.3.4',
            'root',
            'pass',
            'MTProxy 安装完成\n状态: 运行正常\n端口: 443',
            '',
        )

        order.refresh_from_db()
        self.assertEqual(order.mtproxy_link, 'tg://proxy?server=1.2.3.4&port=443&secret=ee1234567890abcdef1234567890abcd')
        self.assertEqual(order.mtproxy_secret, 'ee1234567890abcdef1234567890abcd')
        self.assertEqual(order.proxy_links[0]['port'], '443')

    def test_non_aws_manual_asset_edit_updates_existing_order_in_place(self):
        old_expiry = timezone.now() + timezone.timedelta(days=10)
        new_expiry = timezone.now() + timezone.timedelta(days=35)
        aliyun_plan = CloudServerPlan.objects.create(
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            plan_name='Aliyun Lite',
            cpu='2核',
            memory='1GB',
            storage='40GB SSD',
            bandwidth='1TB',
            price='29.00',
            currency='USDT',
            is_active=True,
            sort_order=90,
        )
        order = CloudServerOrder.objects.create(
            order_no='MANUAL-ALIYUN-OLD-1',
            user=self.user,
            plan=aliyun_plan,
            provider=aliyun_plan.provider,
            region_code=aliyun_plan.region_code,
            region_name=aliyun_plan.region_name,
            plan_name=aliyun_plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='29.00',
            pay_amount='29.00',
            pay_method='balance',
            status='completed',
            public_ip='47.1.1.1',
            service_started_at=timezone.now(),
            service_expires_at=old_expiry,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aliyun_simple',
            region_code=aliyun_plan.region_code,
            region_name=aliyun_plan.region_name,
            asset_name='aliyun-proxy',
            public_ip='47.1.1.1',
            actual_expires_at=old_expiry,
            price='29.00',
        )
        new_user = TelegramUser.objects.create(tg_user_id=990003, username='aliyun_target')
        staff_user = get_user_model().objects.create_user(username='staff_api_1', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({
                'user_id': new_user.id,
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
        self.assertEqual(order.user_id, new_user.id)
        self.assertEqual(asset.order_id, order.id)
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.service_expires_at, new_expiry)
        self.assertEqual(asset.actual_expires_at, new_expiry)
        self.assertFalse(CloudServerOrder.objects.filter(order_no__startswith='SRVADMIN', replacement_for=order).exists())
        owner_audit_order = CloudServerOrder.objects.filter(order_no__startswith='SRVMANUAL', replacement_for=order, user=new_user).exclude(id=order.id).latest('id')
        self.assertEqual(owner_audit_order.service_expires_at, old_expiry)
        self.assertIn('人工编辑所属人', owner_audit_order.provision_note or '')
        self.assertNotIn('人工编辑到期时间', owner_audit_order.provision_note or '')
