from .common import *


class CloudServerReconcileNoticeTasksMixin:
    def test_reconcile_cloud_assets_skips_deleted_server_residual(self):
        order = CloudServerOrder.objects.create(
            order_no='RECONCILE-DELETED-SERVER-1',
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
            previous_public_ip='7.7.7.7',
            instance_id='i-reconcile-deleted-server',
            provider_resource_id='res-reconcile-deleted-server',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='reconcile-deleted-server',
            public_ip=None,
            previous_public_ip='7.7.7.7',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=Server.STATUS_DELETED,
            provider_status='云上未找到实例/IP',
            is_active=False,
            note='状态: 云上未找到实例/IP',
        )

        call_command('reconcile_cloud_assets_from_servers')

        self.assertFalse(
            CloudAsset.objects.filter(
                instance_id='i-reconcile-deleted-server',
                provider_resource_id='res-reconcile-deleted-server',
            ).exists()
        )

    def test_reconcile_cloud_assets_does_not_match_cross_provider_instance_id(self):
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            user=self.user,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            asset_name='aliyun-shared-instance',
            instance_id='shared-instance-id',
            provider_resource_id='shared-instance-id',
            public_ip=None,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='aws-shared-instance',
            instance_id='shared-instance-id',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/shared-instance-id',
            public_ip=None,
            status=Server.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )

        call_command('reconcile_cloud_assets_from_servers')

        self.assertTrue(CloudAsset.objects.filter(provider='aliyun_simple', instance_id='shared-instance-id').exists())
        self.assertTrue(CloudAsset.objects.filter(provider='aws_lightsail', instance_id='shared-instance-id').exists())

    def test_reconcile_cloud_assets_preserves_server_account_label(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='reconcile-account',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        label = cloud_account_label(account)
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=label,
            region_code='ap-southeast-1',
            region_name='新加坡',
            server_name='reconcile-account-server',
            instance_id='reconcile-account-server',
            public_ip='13.250.30.200',
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        call_command('reconcile_cloud_assets_from_servers')

        asset = CloudAsset.objects.get(instance_id='reconcile-account-server')
        self.assertEqual(asset.account_label, label)
        self.assertEqual(asset.cloud_account, account)

    def test_reconcile_cloud_assets_skips_inactive_cloud_account_server(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='reconcile-inactive-account',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=False,
        )
        label = cloud_account_label(account)
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=label,
            region_code='ap-southeast-1',
            region_name='新加坡',
            server_name='inactive-account-server',
            instance_id='inactive-account-server',
            public_ip='13.250.30.203',
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        call_command('reconcile_cloud_assets_from_servers')

        self.assertFalse(CloudAsset.objects.filter(instance_id='inactive-account-server').exists())

    def test_reconcile_cloud_assets_skips_server_marked_cloud_missing(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='reconcile-missing-account',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        label = cloud_account_label(account)
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=label,
            region_code='ap-southeast-1',
            region_name='新加坡',
            server_name='missing-account-server',
            instance_id='missing-account-server',
            public_ip='13.250.30.204',
            status=Server.STATUS_RUNNING,
            provider_status='云上未找到实例/IP',
            note='服务器校验发现云上不存在，已标记删除',
            is_active=True,
        )

        call_command('reconcile_cloud_assets_from_servers')

        self.assertFalse(CloudAsset.objects.filter(instance_id='missing-account-server').exists())

    def test_reconcile_cloud_assets_matches_legacy_account_label_variants(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='reconcile-legacy-account',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        current_label = cloud_account_label(account)
        legacy_label = 'aws_lightsail+123456789012+reconcile-legacy-account'
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=current_label,
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='reconcile-legacy-instance',
            instance_id='reconcile-legacy-instance',
            public_ip='13.250.30.201',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        original_asset_id = asset.id
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=legacy_label,
            region_code='ap-southeast-1',
            region_name='新加坡',
            server_name='reconcile-legacy-instance',
            instance_id='reconcile-legacy-instance',
            public_ip='13.250.30.201',
            status=Server.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )

        call_command('reconcile_cloud_assets_from_servers')

        self.assertEqual(CloudAsset.objects.filter(instance_id='reconcile-legacy-instance').count(), 1)
        asset.refresh_from_db()
        self.assertEqual(asset.id, original_asset_id)
        self.assertEqual(asset.cloud_account, account)
        self.assertEqual(asset.account_label, legacy_label)

    def test_reconcile_cloud_assets_does_not_match_cross_region_same_instance_without_ip(self):
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='us-east-1',
            region_name='弗吉尼亚',
            asset_name='reconcile-same-instance',
            instance_id='reconcile-same-instance',
            public_ip=None,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            server_name='reconcile-same-instance',
            instance_id='reconcile-same-instance',
            public_ip=None,
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        call_command('reconcile_cloud_assets_from_servers')

        self.assertEqual(CloudAsset.objects.filter(instance_id='reconcile-same-instance').count(), 2)
        self.assertTrue(CloudAsset.objects.filter(instance_id='reconcile-same-instance', region_code='us-east-1').exists())
        self.assertTrue(CloudAsset.objects.filter(instance_id='reconcile-same-instance', region_code='ap-southeast-1').exists())

    def test_delete_server_only_removes_server_record(self):
        order = CloudServerOrder.objects.create(
            order_no='DELETE-SERVER-ONLY-1',
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
            instance_id='i-delete-server-only',
            provider_resource_id='res-delete-server-only',
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
            asset_name='delete-server-only-asset',
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
            server_name='delete-server-only',
            public_ip=order.public_ip,
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=Server.STATUS_RUNNING,
        )
        staff_user = get_user_model().objects.create_user(username='staff_server_delete_only', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post(f'/api/dashboard/servers/{server.id}/delete/')
        request.user = staff_user

        response = delete_server(request, server.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertFalse(Server.objects.filter(id=server.id).exists())
        self.assertTrue(CloudAsset.objects.filter(id=asset.id).exists())
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.public_ip, '9.9.9.9')
        self.assertEqual(order.instance_id, 'i-delete-server-only')

    def test_delete_server_does_not_fallback_to_asset_id(self):
        order = CloudServerOrder.objects.create(
            order_no='DELETE-SERVER-NO-FALLBACK-1',
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
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='delete-server-no-fallback',
            price='19.00',
            status=CloudAsset.STATUS_RUNNING,
        )
        staff_user = get_user_model().objects.create_user(username='staff_server_delete_no_fallback', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post(f'/api/dashboard/servers/{asset.id}/delete/')
        request.user = staff_user

        response = delete_server(request, asset.id)

        self.assertEqual(response.status_code, 404)
        self.assertTrue(CloudAsset.objects.filter(id=asset.id).exists())

    def test_servers_list_excludes_unattached_static_ip_rows(self):
        unattached = Server.objects.create(
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='unattached-static-ip-row',
            public_ip='9.9.9.10',
            instance_id='',
            status=Server.STATUS_RUNNING,
            provider_status='未附加固定IP-续费保留中',
            note='未附加固定IP',
        )
        attached = Server.objects.create(
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='attached-server-row',
            public_ip='9.9.9.11',
            instance_id='i-attached-server-row',
            status=Server.STATUS_RUNNING,
            provider_status='运行中',
        )
        staff_user = get_user_model().objects.create_user(username='staff_servers_list_unattached', password='x', is_staff=True)
        request = RequestFactory().get('/api/dashboard/servers/')
        request.user = staff_user

        response = servers_list(request)
        data = json.loads(response.content)['data']
        ids = {item['id'] for item in data}

        self.assertNotIn(unattached.id, ids)
        self.assertIn(attached.id, ids)

    def test_send_logged_cloud_notice_deduplicates_same_event_and_order(self):
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-DEDUPE-1',
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
            public_ip='8.8.8.9',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=12),
        )
        sent = []

        async def fake_notify(user_id, text, reply_markup=None):
            sent.append((user_id, text))
            return True

        result1 = async_to_sync(_send_logged_cloud_notice)('renew_notice', fake_notify, self.user.id, 'hello', None, order=order, notice={'ip': '8.8.8.9'})
        result2 = async_to_sync(_send_logged_cloud_notice)('renew_notice', fake_notify, self.user.id, 'hello', None, order=order, notice={'ip': '8.8.8.9'})

        self.assertTrue(result1)
        self.assertFalse(result2)
        self.assertEqual(len(sent), 1)
        self.assertEqual(CloudUserNoticeLog.objects.filter(event_type='renew_notice', user=self.user, order=order, delivered=True).count(), 1)

    def test_group_cloud_server_list_is_scoped_to_current_group(self):
        first_user = TelegramUser.objects.create(tg_user_id=991997001, username='group_scope_first')
        second_user = TelegramUser.objects.create(tg_user_id=991997002, username='group_scope_second')
        first_group = TelegramGroupFilter.objects.create(chat_id=-1001887001, title='Scope First', enabled=True)
        second_group = TelegramGroupFilter.objects.create(chat_id=-1001887002, title='Scope Second', enabled=True)
        first_order = CloudServerOrder.objects.create(order_no='GROUP-SCOPE-FIRST-1', user=first_user, plan=self.plan, provider=self.plan.provider, region_code=self.plan.region_code, region_name=self.plan.region_name, plan_name=self.plan.plan_name, quantity=1, currency='USDT', total_amount='19.00', pay_amount='19.00', pay_method='balance', status='completed', public_ip='8.8.8.40', service_expires_at=timezone.now() + timezone.timedelta(days=5))
        second_order = CloudServerOrder.objects.create(order_no='GROUP-SCOPE-SECOND-1', user=second_user, plan=self.plan, provider=self.plan.provider, region_code=self.plan.region_code, region_name=self.plan.region_name, plan_name=self.plan.plan_name, quantity=1, currency='USDT', total_amount='19.00', pay_amount='19.00', pay_method='balance', status='completed', public_ip='8.8.8.41', service_expires_at=timezone.now() + timezone.timedelta(days=5))
        first_asset = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=first_order, user=first_user, provider=first_order.provider, region_code=first_order.region_code, region_name=first_order.region_name, asset_name='group-scope-first', public_ip='8.8.8.40', actual_expires_at=first_order.service_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=first_group)
        second_asset = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=second_order, user=second_user, provider=second_order.provider, region_code=second_order.region_code, region_name=second_order.region_name, asset_name='group-scope-second', public_ip='8.8.8.41', actual_expires_at=second_order.service_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=second_group)

        first_items = async_to_sync(list_group_cloud_servers)(first_group.chat_id)
        second_items = async_to_sync(list_group_cloud_servers)(second_group.chat_id)
        first_detail = async_to_sync(get_group_proxy_asset_detail)(first_asset.id, first_group.chat_id, 'asset')
        denied_detail = async_to_sync(get_group_proxy_asset_detail)(second_asset.id, first_group.chat_id, 'asset')

        self.assertEqual([item.public_ip for item in first_items], ['8.8.8.40'])
        self.assertEqual([item.public_ip for item in second_items], ['8.8.8.41'])
        self.assertIsNotNone(first_detail)
        self.assertIsNone(denied_detail)

    def test_group_auto_renew_bulk_toggle_is_scoped_to_current_group(self):
        first_user = TelegramUser.objects.create(tg_user_id=991997101, username='group_auto_first')
        second_user = TelegramUser.objects.create(tg_user_id=991997102, username='group_auto_second')
        first_group = TelegramGroupFilter.objects.create(chat_id=-1001887101, title='Auto Scope First', enabled=True)
        second_group = TelegramGroupFilter.objects.create(chat_id=-1001887102, title='Auto Scope Second', enabled=True)
        first_order = CloudServerOrder.objects.create(order_no='GROUP-AUTO-FIRST-1', user=first_user, plan=self.plan, provider=self.plan.provider, region_code=self.plan.region_code, region_name=self.plan.region_name, plan_name=self.plan.plan_name, quantity=1, currency='USDT', total_amount='19.00', pay_amount='19.00', pay_method='balance', status='completed', public_ip='8.8.8.42', service_expires_at=timezone.now() + timezone.timedelta(days=5), auto_renew_enabled=False)
        second_order = CloudServerOrder.objects.create(order_no='GROUP-AUTO-SECOND-1', user=second_user, plan=self.plan, provider=self.plan.provider, region_code=self.plan.region_code, region_name=self.plan.region_name, plan_name=self.plan.plan_name, quantity=1, currency='USDT', total_amount='19.00', pay_amount='19.00', pay_method='balance', status='completed', public_ip='8.8.8.43', service_expires_at=timezone.now() + timezone.timedelta(days=5), auto_renew_enabled=False)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=first_order, user=first_user, provider=first_order.provider, region_code=first_order.region_code, region_name=first_order.region_name, asset_name='group-auto-first', public_ip='8.8.8.42', actual_expires_at=first_order.service_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=first_group)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=second_order, user=second_user, provider=second_order.provider, region_code=second_order.region_code, region_name=second_order.region_name, asset_name='group-auto-second', public_ip='8.8.8.43', actual_expires_at=second_order.service_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=second_group)

        result = async_to_sync(set_group_cloud_server_auto_renew)(first_group.chat_id, True)
        first_order.refresh_from_db()
        second_order.refresh_from_db()

        self.assertEqual(result['updated'], 1)
        self.assertTrue(first_order.auto_renew_enabled)
        self.assertFalse(second_order.auto_renew_enabled)

    def test_auto_renew_candidates_exclude_admin_notice_users(self):
        admin_user = TelegramUser.objects.create(tg_user_id=991998001, username='auto_admin', balance='999.00')
        other_user = TelegramUser.objects.create(tg_user_id=991998002, username='auto_group_member', balance='88.00')
        self.user.balance = '50.00'
        self.user.save(update_fields=['balance'])
        SiteConfig.set('bot_admin_chat_id', str(admin_user.tg_user_id))
        group = TelegramGroupFilter.objects.create(chat_id=-1001888991, title='Auto Renew Group', enabled=True)
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-EXCLUDE-ADMIN-1',
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
            public_ip='8.8.8.30',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=2),
            auto_renew_enabled=True,
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=order, user=self.user, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-owner', public_ip='8.8.8.30', status=CloudAsset.STATUS_RUNNING, telegram_group=group)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, user=admin_user, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-admin', public_ip='8.8.8.31', status=CloudAsset.STATUS_RUNNING, telegram_group=group)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, user=other_user, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-member', public_ip='8.8.8.32', status=CloudAsset.STATUS_RUNNING, telegram_group=group)

        candidates = _auto_renew_candidate_users(order)
        candidate_ids = [user.id for user in candidates]
        balance_text = '\n'.join(_group_balance_lines_for_orders([order]))

        self.assertNotIn(admin_user.id, candidate_ids)
        self.assertIn(self.user.id, candidate_ids)
        self.assertIn(other_user.id, candidate_ids)
        self.assertNotIn('auto_admin', balance_text)
        self.assertIn('svc_test', balance_text)
        self.assertIn('auto_group_member', balance_text)

    def test_auto_renew_candidates_exclude_primary_admin_user(self):
        admin_user = TelegramUser.objects.create(tg_user_id=991998003, username='primary_admin', balance='999.00')
        member_user = TelegramUser.objects.create(tg_user_id=991998004, username='primary_group_member', balance='50.00')
        SiteConfig.set('bot_admin_chat_id', str(admin_user.tg_user_id))
        group = TelegramGroupFilter.objects.create(chat_id=-1001888992, title='Primary Admin Group', enabled=True)
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-EXCLUDE-PRIMARY-ADMIN-1',
            user=admin_user,
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
            public_ip='8.8.8.33',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=2),
            auto_renew_enabled=True,
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=order, user=admin_user, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-primary-admin', public_ip='8.8.8.33', status=CloudAsset.STATUS_RUNNING, telegram_group=group)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, user=member_user, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-primary-member', public_ip='8.8.8.34', status=CloudAsset.STATUS_RUNNING, telegram_group=group)

        candidates = _auto_renew_candidate_users(order)

        self.assertEqual([user.id for user in candidates], [member_user.id])

    def test_auto_renew_group_member_can_pay_when_owner_balance_insufficient(self):
        owner = self.user
        owner.balance = Decimal('0.00')
        owner.save(update_fields=['balance', 'updated_at'])
        member = TelegramUser.objects.create(tg_user_id=991998005, username='payer_group_member', balance=Decimal('100.00'))
        group = TelegramGroupFilter.objects.create(chat_id=-1001888993, title='Auto Renew Payer Group', enabled=True)
        expires_at = timezone.now() + timezone.timedelta(hours=8)
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-GROUP-PAYER-1',
            user=owner,
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
            public_ip='8.8.8.35',
            instance_id='auto-renew-group-payer',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=expires_at,
            suspend_at=expires_at + timezone.timedelta(days=1),
            auto_renew_enabled=True,
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=order, user=owner, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-group-owner', public_ip='8.8.8.35', actual_expires_at=expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=group)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, user=member, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-group-member', public_ip='8.8.8.36', status=CloudAsset.STATUS_RUNNING, telegram_group=group)

        renewed, err, balance_change = async_to_sync(_run_auto_renew)(order.id)

        order.refresh_from_db()
        owner.refresh_from_db()
        member.refresh_from_db()
        self.assertIsNone(err)
        self.assertEqual(getattr(renewed, 'id', None), order.id)
        self.assertEqual(order.status, 'completed')
        self.assertEqual(owner.balance, Decimal('0.000000'))
        self.assertEqual(member.balance, Decimal('81.000000'))
        self.assertEqual(balance_change['payer_user_id'], member.id)

    def test_send_order_notice_batch_prefers_bound_group_and_skips_private(self):
        group = TelegramGroupFilter.objects.create(
            chat_id=-1001888001,
            title='Notice Group',
            username='notice_group',
            enabled=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-GROUP-FIRST-1',
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
            public_ip='8.8.8.10',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=3),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='notice-group-first-asset',
            public_ip='8.8.8.10',
            status=CloudAsset.STATUS_RUNNING,
            telegram_group=group,
        )
        private_sent = []
        group_sent = []

        async def fake_notify(user_id, text, reply_markup=None):
            private_sent.append((user_id, text))
            return True

        async def fake_notify_target(chat_id, text, reply_markup=None):
            group_sent.append((chat_id, text))
            return True

        result = async_to_sync(_send_order_notice_batch)(
            event='renew_notice_batch',
            field_name='renew_notice_sent_at',
            notify=fake_notify,
            notify_target=fake_notify_target,
            target_chat_id=group.chat_id,
            user_id=self.user.id,
            orders=[order],
            payload={'text': 'hello group', 'order_ids': [order.id], 'first_order_id': order.id, 'count': 1},
        )

        order.refresh_from_db()
        self.assertTrue(result)
        self.assertEqual(group_sent, [(group.chat_id, 'hello group')])
        self.assertEqual(private_sent, [])
        self.assertIsNotNone(order.renew_notice_sent_at)
        log = CloudUserNoticeLog.objects.get(event_type='renew_notice_batch', order=order)
        self.assertTrue(log.delivered)
        self.assertEqual(log.target_chat_id, group.chat_id)
        self.assertEqual(log.extra['notice_target'], 'telegram_group')

    def test_send_order_notice_batch_falls_back_private_when_group_fails(self):
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-GROUP-FALLBACK-1',
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
            public_ip='8.8.8.11',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=3),
        )
        private_sent = []
        group_sent = []

        async def fake_notify(user_id, text, reply_markup=None):
            private_sent.append((user_id, text))
            return True

        async def fake_notify_target(chat_id, text, reply_markup=None):
            group_sent.append((chat_id, text))
            return False

        result = async_to_sync(_send_order_notice_batch)(
            event='renew_notice_batch',
            field_name='renew_notice_sent_at',
            notify=fake_notify,
            notify_target=fake_notify_target,
            target_chat_id=-1001888002,
            user_id=self.user.id,
            orders=[order],
            payload={'text': 'hello fallback', 'order_ids': [order.id], 'first_order_id': order.id, 'count': 1},
        )

        order.refresh_from_db()
        self.assertTrue(result)
        self.assertEqual(group_sent, [(-1001888002, 'hello fallback')])
        self.assertEqual(private_sent, [(self.user.id, 'hello fallback')])
        self.assertIsNotNone(order.renew_notice_sent_at)
        logs = list(CloudUserNoticeLog.objects.filter(event_type='renew_notice_batch', order=order).order_by('id'))
        self.assertEqual(len(logs), 2)
        self.assertFalse(logs[0].delivered)
        self.assertEqual(logs[0].target_chat_id, -1001888002)
        self.assertTrue(logs[1].delivered)
        self.assertIsNone(logs[1].target_chat_id)
        self.assertEqual(logs[1].extra['notice_target'], 'private')

    def test_daily_expiry_summary_uses_real_cloud_status_and_target_config(self):
        self.user.first_name = '张三'
        self.user.save(update_fields=['first_name'])
        now = timezone.now()
        today_expires_at = timezone.make_aware(
            timezone.datetime.combine(timezone.localdate(now), timezone.datetime.min.time().replace(hour=9)),
            timezone.get_current_timezone(),
        )
        today_order = CloudServerOrder.objects.create(
            order_no='DAILY-EXPIRY-TODAY-1',
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
            public_ip='10.10.10.10',
            service_started_at=now - timezone.timedelta(days=30),
            service_expires_at=today_expires_at,
        )
        today_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=today_order,
            user=self.user,
            provider=today_order.provider,
            region_code=today_order.region_code,
            region_name=today_order.region_name,
            asset_name='daily-expiry-today',
            public_ip='10.10.10.10',
            actual_expires_at=today_order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='running',
        )
        expired_order = CloudServerOrder.objects.create(
            order_no='DAILY-EXPIRY-EXPIRED-1',
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
            public_ip='10.10.10.11',
            service_started_at=now - timezone.timedelta(days=60),
            service_expires_at=now - timezone.timedelta(days=1),
        )
        expired_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=expired_order,
            user=self.user,
            provider=expired_order.provider,
            region_code=expired_order.region_code,
            region_name=expired_order.region_name,
            asset_name='daily-expiry-expired',
            public_ip='10.10.10.11',
            actual_expires_at=expired_order.service_expires_at,
            status=CloudAsset.STATUS_STOPPED,
            provider_status='stopped',
        )
        SiteConfig.set('cloud_daily_expiry_summary_enabled', '1')
        SiteConfig.set('cloud_daily_expiry_summary_chat_ids', '10001')
        sent = []

        async def fake_notify_target(chat_id, text, reply_markup=None):
            sent.append((chat_id, text))
            return True

        with patch('cloud.lifecycle.sync_server_status_tick', new_callable=AsyncMock) as sync_mock:
            result = async_to_sync(daily_expiry_summary_tick)(notify_target=fake_notify_target)

        self.assertEqual(result['sent'], 1)
        sync_mock.assert_not_called()
        self.assertEqual(len(sent), 2)
        self.assertIn('🟡 今日到期服务器', sent[0][1])
        self.assertIn('状态来自数据库当前记录。', sent[0][1])
        self.assertIn('今日到期: 1 台｜已经到期: 1 台', sent[0][1])
        self.assertIn('所属用户: svc_test｜姓名: 张三', sent[0][1])
        self.assertIn('IP: <code>10.10.10.10</code>', sent[0][1])
        self.assertIn('状态: 正在运行', sent[0][1])
        self.assertIn('🔴 已经过期服务器', sent[1][1])
        self.assertIn('所属用户: svc_test｜姓名: 张三', sent[1][1])
        self.assertIn('IP: <code>10.10.10.11</code>', sent[1][1])
        self.assertIn('状态: 已关机', sent[1][1])
        self.assertNotIn('已截断', '\n'.join(text for _, text in sent))
        log = CloudUserNoticeLog.objects.get(event_type='daily_expiry_summary')
        self.assertTrue(log.delivered)
        self.assertEqual(log.target_chat_id, 10001)
        self.assertEqual(log.extra['today_count'], 1)
        self.assertEqual(log.extra['expired_count'], 1)

    def test_tasks_overview_exposes_click_paths_for_entry_and_order_number(self):
        order = CloudServerOrder.objects.create(
            order_no='TASK-LINK-1',
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
            public_ip='1.1.1.1',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=5),
            auto_renew_enabled=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_2', password='x', is_staff=True)
        request = RequestFactory().get('/api/dashboard/tasks/')
        request.user = staff_user

        response = tasks_overview(request)
        payload = json.loads(response.content)
        items = payload.get('data') or payload
        pinned = next(item for item in items if item['id'] == -10001)
        regular = next(item for item in items if item['id'] == order.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(pinned['detail_path'], '/admin/tasks/auto-renew')
        self.assertEqual(pinned['order_link_path'], '/admin/tasks/auto-renew')
        self.assertEqual(regular['detail_path'], f'/admin/cloud-orders/{order.id}')
        self.assertEqual(regular['order_detail_path'], f'/admin/cloud-orders/{order.id}')

    def test_auto_renew_retry_task_waits_for_recharge_then_retries(self):
        expires_at = timezone.now() + timezone.timedelta(hours=8)
        self.user.balance = Decimal('0.00')
        self.user.save(update_fields=['balance', 'updated_at'])
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-RETRY-AFTER-RECHARGE-1',
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
            public_ip='6.6.6.20',
            instance_id='auto-renew-retry-instance',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=expires_at,
            suspend_at=expires_at + timezone.timedelta(days=1),
            expired_at=timezone.now() + timezone.timedelta(minutes=30),
            auto_renew_enabled=True,
        )
        self._create_auto_renew_asset(order)

        enqueued = async_to_sync(_enqueue_auto_renew_retry)(order.id, ip=order.public_ip, error='USDT 余额不足', balance_change={'candidate_count': 1})
        self.assertTrue(enqueued)
        task = CloudAutoRenewRetryTask.objects.get(order=order, status=CloudAutoRenewRetryTask.STATUS_PENDING)
        task.next_check_at = timezone.now() - timezone.timedelta(seconds=1)
        task.save(update_fields=['next_check_at', 'updated_at'])

        retried = async_to_sync(_process_auto_renew_retry_tasks)()
        task.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(retried, 0)
        self.assertEqual(task.status, CloudAutoRenewRetryTask.STATUS_PENDING)
        self.assertEqual(order.status, 'renew_pending')

        self.user.balance = Decimal('100.00')
        self.user.save(update_fields=['balance', 'updated_at'])
        task.next_check_at = timezone.now() - timezone.timedelta(seconds=1)
        task.save(update_fields=['next_check_at', 'updated_at'])

        retried = async_to_sync(_process_auto_renew_retry_tasks)()
        task.refresh_from_db()
        order.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(retried, 1)
        self.assertEqual(task.status, CloudAutoRenewRetryTask.STATUS_SUCCEEDED)
        self.assertEqual(order.status, 'completed')
        self.assertEqual(self.user.balance, Decimal('81.000000'))
        self.assertTrue(CloudAutoRenewPatrolLog.objects.filter(order=order, is_success=True).exists())

    def test_update_cloud_asset_price_restores_auto_renew_pending_state(self):
        expires_at = timezone.now() + timezone.timedelta(hours=8)
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-PRICE-FIX-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='0.00',
            pay_amount='0.00',
            pay_method='address',
            status='renew_pending',
            public_ip='6.6.6.10',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=expires_at,
            suspend_at=expires_at + timezone.timedelta(days=1),
            expired_at=timezone.now() + timezone.timedelta(minutes=30),
            auto_renew_enabled=True,
            auto_renew_failure_notice_sent_at=timezone.now(),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='auto-renew-price-fix-proxy',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
        )
        CloudAutoRenewPatrolLog.objects.create(
            order=order,
            user=self.user,
            batch_id='price-missing-batch',
            order_no=order.order_no,
            ip=order.public_ip,
            provider=order.provider,
            user_display_name='svc_test',
            username_label='@svc_test',
            tg_user_id=self.user.tg_user_id,
            is_success=False,
            failure_reason='该代理缺少续费价格，请先在后台代理列表填写人工价格。',
        )
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_price_fix', password='x', is_staff=True, is_superuser=True)
        before_request = RequestFactory().get('/api/dashboard/tasks/')
        before_request.user = staff_user
        before_payload = json.loads(tasks_overview(before_request).content)
        before_pinned = next(item for item in (before_payload.get('data') or before_payload) if item['id'] == -10001)
        self.assertEqual(before_pinned['execution_status'], 'auto_renew_failed')

        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'price': '29.00'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user
        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal('29.00'))
        self.assertEqual(order.pay_amount, Decimal('29.00'))
        self.assertIsNone(order.auto_renew_failure_notice_sent_at)
        after_request = RequestFactory().get('/api/dashboard/tasks/')
        after_request.user = staff_user
        after_payload = json.loads(tasks_overview(after_request).content)
        after_pinned = next(item for item in (after_payload.get('data') or after_payload) if item['id'] == -10001)
        self.assertEqual(after_pinned['execution_status'], 'auto_renew_pending')

    def test_renewal_balance_payment_uses_latest_proxy_price(self):
        self.user.balance = Decimal('100.000000')
        self.user.save(update_fields=['balance', 'updated_at'])
        expires_at = timezone.now() + timezone.timedelta(hours=8)
        order = CloudServerOrder.objects.create(
            order_no='RENEW-LATEST-PROXY-PRICE-1',
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
            public_ip='6.6.6.11',
            instance_id='i-renew-latest-price',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=expires_at,
            suspend_at=expires_at + timezone.timedelta(days=1),
            expired_at=timezone.now() + timezone.timedelta(minutes=30),
            auto_renew_enabled=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='renew-latest-proxy-price',
            public_ip=order.public_ip,
            instance_id=order.instance_id,
            actual_expires_at=expires_at,
            price=Decimal('29.00'),
        )

        renewed, err = async_to_sync(pay_cloud_server_renewal_with_balance)(order.id, self.user.id, 'USDT', 31)

        self.assertIsNone(err)
        self.assertIsNotNone(renewed)
        order.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal('29.00'))
        self.assertEqual(order.pay_amount, Decimal('29.00'))
        self.assertEqual(self.user.balance, Decimal('71.000000'))

    def test_cloud_asset_detail_exposes_related_order_click_path(self):
        order = CloudServerOrder.objects.create(
            order_no='ASSET-DETAIL-ORDER-1',
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
            public_ip='2.2.2.2',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=8),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='asset-detail-proxy',
            public_ip='2.2.2.2',
            actual_expires_at=order.service_expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_3', password='x', is_staff=True)
        request = RequestFactory().get(f'/api/dashboard/cloud-assets/{asset.id}/')
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)
        payload = json.loads(response.content)
        data = payload.get('data') or payload

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['order_detail_path'], f'/admin/cloud-orders/{order.id}')
        self.assertEqual(data['order_link_path'], f'/admin/cloud-orders/{order.id}')
        self.assertEqual(data['related_order']['order_link_path'], f'/admin/cloud-orders/{order.id}')

    def test_cloud_asset_detail_exposes_history_orders_with_click_paths(self):
        root_order = CloudServerOrder.objects.create(
            order_no='ASSET-HISTORY-ROOT-1',
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
            status='cancelled',
            public_ip='3.3.3.3',
            service_started_at=timezone.now() - timezone.timedelta(days=20),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
        )
        newer_order = CloudServerOrder.objects.create(
            order_no='ASSET-HISTORY-NEW-1',
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
            service_started_at=timezone.now() - timezone.timedelta(days=4),
            service_expires_at=timezone.now() + timezone.timedelta(days=20),
            replacement_for=root_order,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=newer_order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='asset-history-proxy',
            public_ip='3.3.3.3',
            actual_expires_at=newer_order.service_expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_4', password='x', is_staff=True)
        request = RequestFactory().get(f'/api/dashboard/cloud-assets/{asset.id}/')
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)
        payload = json.loads(response.content)
        data = payload.get('data') or payload
        history_orders = data['history_orders']

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(history_orders), 2)
        self.assertEqual(history_orders[0]['order_link_path'], f"/admin/cloud-orders/{history_orders[0]['id']}")
        self.assertTrue(any(item['id'] == root_order.id for item in history_orders))
        root_item = next(item for item in history_orders if item['id'] == root_order.id)
        self.assertEqual(root_item['order_detail_path'], f'/admin/cloud-orders/{root_order.id}')
