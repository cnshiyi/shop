import json
import time
from decimal import Decimal

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import BalanceLedger, TelegramUser
from cloud.lifecycle import auto_renew_tick
from cloud.provisioning import _claim_order_for_provisioning
from core.models import CloudAccountConfig, SiteConfig
from dashboard_api.views import _totp_code
from finance.models import Recharge
from mall.models import CloudAsset, CloudServerOrder, CloudServerPlan, Server


class DashboardApiRegressionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username='root',
            email='root@example.test',
            password='pass123456',
        )
        self.staff = User.objects.create_user(
            username='staff',
            password='pass123456',
            is_staff=True,
        )
        self.normal = User.objects.create_user(
            username='normal',
            password='pass123456',
        )

    def _post_json(self, client, path, payload):
        return client.post(
            path,
            data=json.dumps(payload),
            content_type='application/json',
        )

    def _login(self, user):
        client = Client()
        response = self._post_json(
            client,
            '/api/admin/auth/login',
            {'username': user.username, 'password': 'pass123456'},
        )
        self.assertEqual(response.status_code, 200, response.content)
        return client

    def test_non_staff_cannot_login_dashboard(self):
        response = self._post_json(
            Client(),
            '/api/admin/auth/login',
            {'username': self.normal.username, 'password': 'pass123456'},
        )

        self.assertEqual(response.status_code, 403)

    def test_staff_read_is_allowed_but_write_requires_superuser(self):
        client = self._login(self.staff)

        read_response = client.get('/api/admin/settings/cloud-accounts/')
        write_response = self._post_json(
            client,
            '/api/admin/settings/cloud-accounts/create/',
            {
                'provider': 'aws',
                'name': 'aws-test',
                'access_key': 'AKIA_TEST_123456',
                'secret_key': 'SECRET_TEST_123456',
            },
        )

        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(write_response.status_code, 403)

    def test_cloud_account_payload_never_returns_plain_secret(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-prod',
            access_key='AKIA1234567890',
            secret_key='SECRET1234567890',
            region_hint='ap-southeast-1',
        )
        client = self._login(self.staff)

        list_payload = client.get('/api/admin/settings/cloud-accounts/').json()['data'][0]
        detail_payload = client.get(
            f'/api/admin/settings/cloud-accounts/{account.id}/',
        ).json()['data']

        for payload in (list_payload, detail_payload):
            self.assertEqual(payload['access_key'], '')
            self.assertEqual(payload['secret_key'], '')
            self.assertEqual(payload['access_key_preview'], 'AKIA***7890')
            self.assertEqual(payload['secret_key_preview'], 'SECR***7890')

    def test_sensitive_site_config_is_masked_and_preserved(self):
        config = SiteConfig.set('trongrid_api_key', 'TGSECRET123456', sensitive=True)
        client = self._login(self.superuser)

        list_payload = client.get('/api/admin/settings/site-configs/').json()['data'][0]
        group_payload = client.get(
            '/api/admin/settings/site-configs/groups/?group=payment',
        ).json()['data']
        group_item = next(
            item
            for group in group_payload
            for item in group['items']
            if item['key'] == 'trongrid_api_key'
        )

        self.assertEqual(list_payload['value'], '')
        self.assertEqual(group_item['value'], '')
        self.assertEqual(group_item['value_preview'], 'TGSE***3456')

        response = self._post_json(
            client,
            f'/api/admin/settings/site-configs/{config.id}/',
            {
                'key': 'trongrid_api_key',
                'is_sensitive': True,
                'preserve_existing': True,
                'value': '',
            },
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(SiteConfig.get('trongrid_api_key'), 'TGSECRET123456')

    def test_totp_login_and_binding_require_real_codes(self):
        secret = 'JBSWY3DPEHPK3PXP'
        SiteConfig.set('dashboard_totp_secret', secret, sensitive=True)

        missing_response = self._post_json(
            Client(),
            '/api/admin/auth/login',
            {'username': self.superuser.username, 'password': 'pass123456'},
        )
        wrong_response = self._post_json(
            Client(),
            '/api/admin/auth/login',
            {
                'username': self.superuser.username,
                'password': 'pass123456',
                'otp_token': '000000',
            },
        )
        correct_response = self._post_json(
            Client(),
            '/api/admin/auth/login',
            {
                'username': self.superuser.username,
                'password': 'pass123456',
                'otp_token': _totp_code(secret, int(time.time() // 30)),
            },
        )

        self.assertEqual(missing_response.status_code, 401)
        self.assertEqual(wrong_response.status_code, 401)
        self.assertEqual(correct_response.status_code, 200, correct_response.content)

        SiteConfig.set('dashboard_totp_secret', '', sensitive=True)
        pending_secret = 'JBSWY3DPEHPK3PXP'
        SiteConfig.set('dashboard_totp_pending_secret', pending_secret, sensitive=True)
        client = self._login(self.superuser)

        bad_bind = self._post_json(
            client,
            '/api/admin/auth/totp/bind',
            {'otp_token': '000000'},
        )
        good_bind = self._post_json(
            client,
            '/api/admin/auth/totp/bind',
            {'otp_token': _totp_code(pending_secret, int(time.time() // 30))},
        )

        self.assertEqual(bad_bind.status_code, 400)
        self.assertEqual(good_bind.status_code, 200, good_bind.content)
        self.assertEqual(SiteConfig.get('dashboard_totp_secret'), pending_secret)

    def test_clear_cloud_asset_user_keeps_required_order_user(self):
        tg_user = TelegramUser.objects.create(
            tg_user_id=990101,
            username='asset_user',
        )
        plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Micro',
            price='19.00',
            currency='USDT',
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='DASH-CLR-1',
            user=tg_user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            last_user_id=tg_user.tg_user_id,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            provider=plan.provider,
            region_code=plan.region_code,
            asset_name='asset-clear',
            order=order,
            user=tg_user,
            status=CloudAsset.STATUS_RUNNING,
        )
        server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            provider=plan.provider,
            region_code=plan.region_code,
            server_name='asset-clear',
            order=order,
            user=tg_user,
            status=Server.STATUS_RUNNING,
        )
        client = self._login(self.superuser)

        response = self._post_json(
            client,
            f'/api/admin/cloud-assets/{asset.id}/',
            {'clear_user': True},
        )

        self.assertEqual(response.status_code, 200, response.content)
        asset.refresh_from_db()
        server.refresh_from_db()
        order.refresh_from_db()
        self.assertIsNone(asset.user_id)
        self.assertIsNone(server.user_id)
        self.assertEqual(order.user_id, tg_user.id)
        self.assertIsNone(order.last_user_id)

    def test_recharge_status_updates_balance_and_ledger(self):
        tg_user = TelegramUser.objects.create(
            tg_user_id=990102,
            username='recharge_user',
            balance='0',
        )
        recharge = Recharge.objects.create(
            user=tg_user,
            currency='USDT',
            amount='12.50',
            pay_amount='12.50',
            status='pending',
        )
        client = self._login(self.superuser)

        complete_response = self._post_json(
            client,
            f'/api/admin/recharges/{recharge.id}/status/',
            {'status': 'completed'},
        )
        expire_response = self._post_json(
            client,
            f'/api/admin/recharges/{recharge.id}/status/',
            {'status': 'expired'},
        )

        self.assertEqual(complete_response.status_code, 200, complete_response.content)
        self.assertEqual(expire_response.status_code, 200, expire_response.content)
        tg_user.refresh_from_db()
        ledgers = list(BalanceLedger.objects.filter(related_id=recharge.id).order_by('id'))
        self.assertEqual(tg_user.balance, 0)
        self.assertEqual(len(ledgers), 2)
        self.assertEqual(ledgers[0].direction, BalanceLedger.DIRECTION_IN)
        self.assertEqual(ledgers[1].direction, BalanceLedger.DIRECTION_OUT)

    def test_cloud_order_password_plaintext_requires_superuser(self):
        tg_user = TelegramUser.objects.create(
            tg_user_id=990103,
            username='password_user',
        )
        plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Micro Password',
            price='19.00',
            currency='USDT',
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='DASH-PWD-1',
            user=tg_user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            login_user='root',
            login_password='RootPass123456',
        )

        staff_payload = self._login(self.staff).get(
            f'/api/admin/cloud-orders/{order.id}/',
        ).json()['data']
        super_payload = self._login(self.superuser).get(
            f'/api/admin/cloud-orders/{order.id}/',
        ).json()['data']

        self.assertEqual(staff_payload['login_password'], '')
        self.assertEqual(staff_payload['login_password_preview'], 'Root***3456')
        self.assertEqual(super_payload['login_password'], 'RootPass123456')

    def test_cloud_order_manual_ip_recycle_time_is_not_overwritten(self):
        tg_user = TelegramUser.objects.create(
            tg_user_id=990108,
            username='manual_lifecycle_user',
        )
        plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Manual Lifecycle',
            price='19.00',
            currency='USDT',
            is_active=True,
        )
        service_expires_at = timezone.now() + timezone.timedelta(days=10)
        manual_recycle_at = timezone.now() + timezone.timedelta(days=60)
        order = CloudServerOrder.objects.create(
            order_no='DASH-LIFE-1',
            user=tg_user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            service_expires_at=service_expires_at,
        )
        client = self._login(self.superuser)

        response = self._post_json(
            client,
            f'/api/admin/cloud-orders/{order.id}/',
            {'ip_recycle_at': manual_recycle_at.isoformat()},
        )

        self.assertEqual(response.status_code, 200, response.content)
        order.refresh_from_db()
        self.assertEqual(order.ip_recycle_at, manual_recycle_at)

    def test_notice_switch_history_delete_and_manual_text_are_persisted(self):
        tg_user = TelegramUser.objects.create(
            tg_user_id=990104,
            username='notice_user',
        )
        plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Notice Plan',
            price='19.00',
            currency='USDT',
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='DASH-NOTICE-1',
            user=tg_user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
            public_ip='203.0.113.10',
        )
        client = self._login(self.superuser)

        text_response = self._post_json(
            client,
            '/api/admin/tasks/notices/text/',
            {
                'event': 'renew_notice',
                'notice_text': '自定义提醒 {order_no}',
                'order_ids': [order.id],
                'user_id': tg_user.id,
            },
        )
        plan_response = client.get('/api/admin/tasks/notices/')

        self.assertEqual(text_response.status_code, 200, text_response.content)
        future_items = plan_response.json()['data']['future_plan_items']
        self.assertEqual(future_items[0]['notice_text_preview'], '自定义提醒 DASH-NOTICE-1')

        switch_response = self._post_json(
            client,
            '/api/admin/tasks/notices/switches/',
            {'switches': [{'key': 'renew_notice', 'enabled': False}]},
        )
        disabled_plan = client.get('/api/admin/tasks/notices/').json()['data']

        self.assertEqual(switch_response.status_code, 200, switch_response.content)
        self.assertEqual(SiteConfig.get('cloud_notice_renew_enabled'), '0')
        self.assertEqual(disabled_plan['due_count'], 0)

        order.renew_notice_sent_at = timezone.now()
        order.save(update_fields=['renew_notice_sent_at', 'updated_at'])
        delete_response = self._post_json(
            client,
            f'/api/admin/tasks/notices/history/renew_notice-{order.id}/delete/',
            {},
        )

        self.assertEqual(delete_response.status_code, 200, delete_response.content)
        order.refresh_from_db()
        self.assertIsNone(order.renew_notice_sent_at)

    def test_auto_renew_task_lists_and_executes_due_orders(self):
        tg_user = TelegramUser.objects.create(
            tg_user_id=990105,
            username='auto_renew_user',
            balance='50.00',
        )
        plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Auto Renew Plan',
            price='10.00',
            currency='USDT',
            is_active=True,
        )
        expires_at = timezone.now() + timezone.timedelta(hours=12)
        order = CloudServerOrder.objects.create(
            order_no='DASH-AUTO-1',
            user=tg_user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='10.00',
            pay_amount='10.00',
            status='completed',
            lifecycle_days=31,
            service_expires_at=expires_at,
            public_ip='203.0.113.20',
            auto_renew_enabled=True,
        )
        client = self._login(self.superuser)

        detail = client.get('/api/admin/tasks/auto-renew/').json()['data']
        run_response = self._post_json(
            client,
            f'/api/admin/tasks/auto-renew/orders/{order.id}/run/',
            {},
        )

        self.assertEqual(detail['due_count'], 1)
        self.assertEqual(run_response.status_code, 200, run_response.content)
        self.assertEqual(run_response.json()['data']['success_count'], 1)
        tg_user.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(tg_user.balance, Decimal('40.00'))
        self.assertGreater(order.service_expires_at, expires_at)
        self.assertTrue(BalanceLedger.objects.filter(
            related_id=order.id,
            description__icontains='自动续费',
        ).exists())

    def test_auto_renew_tick_respects_notice_switch(self):
        tg_user = TelegramUser.objects.create(
            tg_user_id=990106,
            username='auto_renew_notice_user',
            balance='50.00',
        )
        plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Auto Renew Notice Plan',
            price='10.00',
            currency='USDT',
            is_active=True,
        )
        expires_at = timezone.now() + timezone.timedelta(hours=12)
        order = CloudServerOrder.objects.create(
            order_no='DASH-AUTO-NOTICE-1',
            user=tg_user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='10.00',
            pay_amount='10.00',
            status='completed',
            lifecycle_days=31,
            service_expires_at=expires_at,
            public_ip='203.0.113.21',
            auto_renew_enabled=True,
        )
        SiteConfig.set('cloud_notice_auto_renew_enabled', '0')
        notifications = []

        async def fake_notify(user_id, text, reply_markup=None):
            notifications.append((user_id, text, reply_markup))

        async_to_sync(auto_renew_tick)(notify=fake_notify)

        order.refresh_from_db()
        tg_user.refresh_from_db()
        self.assertEqual(notifications, [])
        self.assertEqual(tg_user.balance, Decimal('40.00'))
        self.assertGreater(order.service_expires_at, expires_at)

    def test_auto_renew_ignores_pending_address_renewal(self):
        tg_user = TelegramUser.objects.create(
            tg_user_id=990109,
            username='auto_renew_pending_user',
            balance='50.00',
        )
        plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Auto Renew Pending Plan',
            price='10.00',
            currency='USDT',
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='DASH-AUTO-PENDING-1',
            user=tg_user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='10.00',
            pay_amount='10.123',
            pay_method='address',
            status='renew_pending',
            lifecycle_days=31,
            service_expires_at=timezone.now() + timezone.timedelta(hours=12),
            expired_at=timezone.now() + timezone.timedelta(minutes=30),
            public_ip='203.0.113.22',
            auto_renew_enabled=True,
        )
        client = self._login(self.superuser)

        detail = client.get('/api/admin/tasks/auto-renew/').json()['data']
        run_response = self._post_json(client, '/api/admin/tasks/auto-renew/run/', {})

        self.assertEqual(detail['due_count'], 0)
        self.assertEqual(run_response.status_code, 200, run_response.content)
        self.assertEqual(run_response.json()['data']['success_count'], 0)
        tg_user.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(tg_user.balance, Decimal('50.00'))
        self.assertEqual(order.status, 'renew_pending')

    def test_cloud_orders_list_only_marks_live_ip_orders_renewable(self):
        tg_user = TelegramUser.objects.create(
            tg_user_id=990110,
            username='renewable_list_user',
        )
        plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Renewable List Plan',
            price='10.00',
            currency='USDT',
            is_active=True,
        )
        active_order = CloudServerOrder.objects.create(
            order_no='DASH-RENEWABLE-ACTIVE',
            user=tg_user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='10.00',
            pay_amount='10.00',
            status='completed',
            public_ip='203.0.113.24',
            service_expires_at=timezone.now() + timezone.timedelta(days=10),
        )
        deleted_order = CloudServerOrder.objects.create(
            order_no='DASH-RENEWABLE-DELETED',
            user=tg_user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='10.00',
            pay_amount='10.00',
            status='deleted',
            previous_public_ip='203.0.113.25',
            service_expires_at=timezone.now() + timezone.timedelta(days=10),
        )
        renewal_pending_order = CloudServerOrder.objects.create(
            order_no='DASH-RENEWABLE-PENDING',
            user=tg_user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='10.00',
            pay_amount='10.123',
            pay_method='address',
            status='renew_pending',
            public_ip='203.0.113.26',
            service_expires_at=timezone.now() + timezone.timedelta(days=10),
        )
        client = self._login(self.staff)

        payload = client.get('/api/admin/cloud-orders/').json()['data']
        by_id = {item['id']: item for item in payload}

        self.assertTrue(by_id[active_order.id]['can_renew'])
        self.assertFalse(by_id[deleted_order.id]['can_renew'])
        self.assertFalse(by_id[renewal_pending_order.id]['can_renew'])

    def test_provisioning_claim_prevents_duplicate_cloud_create(self):
        tg_user = TelegramUser.objects.create(
            tg_user_id=990107,
            username='provision_claim_user',
        )
        plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Provision Claim Plan',
            price='10.00',
            currency='USDT',
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='DASH-PROVISION-CLAIM-1',
            user=tg_user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='10.00',
            pay_amount='10.00',
            status='paid',
        )

        claimed, first_reason = async_to_sync(_claim_order_for_provisioning)(order.id)
        duplicate, duplicate_reason = async_to_sync(_claim_order_for_provisioning)(order.id)

        self.assertIsNone(first_reason)
        self.assertEqual(claimed.status, 'provisioning')
        self.assertEqual(duplicate.id, order.id)
        self.assertIn('已在创建中', duplicate_reason)
