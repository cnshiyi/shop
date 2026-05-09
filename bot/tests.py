from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sessions.models import Session
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from bot.api import DASHBOARD_SESSION_IDLE_SECONDS, _authenticate_dashboard_request, test_daily_expiry_summary_notification
from bot.handlers import _retained_ip_renewal_plan_keyboard, _validate_reinstall_proxy_link
from bot.telegram_listener import _build_bark_request, _build_push_payload, _is_self_sender
from core.texts import BOT_TEXTS


class DashboardSessionExpiryTestCase(TestCase):
    def test_authenticated_dashboard_request_refreshes_one_hour_idle_expiry(self):
        user = get_user_model().objects.create_user(username='dashboard_staff', password='pass', is_staff=True)
        request = RequestFactory().get('/api/auth/codes')
        SessionMiddleware(lambda req: None).process_request(request)
        request.session['_auth_user_id'] = str(user.pk)
        request.session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
        request.session['_auth_user_hash'] = user.get_session_auth_hash()
        request.session.set_expiry(60)
        request.session.save()
        request.user = AnonymousUser()
        request.META['HTTP_AUTHORIZATION'] = f'Bearer session-{request.session.session_key}'

        authenticated = _authenticate_dashboard_request(request)

        self.assertEqual(authenticated, user)
        refreshed = Session.objects.get(session_key=request.session.session_key)
        remaining_seconds = (refreshed.expire_date - timezone.now()).total_seconds()
        self.assertGreater(remaining_seconds, DASHBOARD_SESSION_IDLE_SECONDS - 30)
        self.assertLessEqual(remaining_seconds, DASHBOARD_SESSION_IDLE_SECONDS + 30)


class DashboardNotificationTestCase(TestCase):
    def test_daily_expiry_summary_test_endpoint_forces_send(self):
        staff = get_user_model().objects.create_user(username='daily_expiry_staff', password='pass', is_staff=True)
        request = RequestFactory().post('/api/admin/settings/site-configs/daily-expiry-summary/test/')
        request.user = staff
        bot = MagicMock()
        bot.session.close = AsyncMock()

        with patch('bot.api.get_runtime_config', return_value='123:test-token'):
            with patch('aiogram.Bot', return_value=bot):
                with patch('cloud.lifecycle.daily_expiry_summary_tick', new_callable=AsyncMock) as tick:
                    tick.return_value = {'sent': 1, 'today': 2, 'expired': 3}
                    response = test_daily_expiry_summary_notification(request)

        self.assertEqual(response.status_code, 200)
        tick.assert_awaited_once()
        self.assertTrue(tick.await_args.kwargs.get('force'))
        self.assertFalse(tick.await_args.kwargs.get('sync_cloud'))
        bot.session.close.assert_awaited_once()


class TelegramListenerPushTestCase(SimpleTestCase):
    def test_build_push_payload_for_private_message(self):
        payload = _build_push_payload(
            is_outgoing=False,
            is_private_chat=True,
            sender_name='Alice',
            chat_title='Alice',
            text='hello',
            content_type='text',
            private_enabled=True,
        )
        self.assertEqual(payload, ('📨 私聊消息', '收到一条新的私聊消息'))

    def test_build_push_payload_for_group_push_enabled(self):
        payload = _build_push_payload(
            is_outgoing=False,
            is_private_chat=False,
            sender_name='Bob',
            chat_title='Push Group',
            text='hello group',
            content_type='text',
            private_enabled=True,
            group_push_enabled=True,
        )
        self.assertEqual(payload, ('📢 群/频道消息', '收到一条新的群组或频道消息'))

    def test_build_push_payload_skips_group_without_push_switch(self):
        payload = _build_push_payload(
            is_outgoing=False,
            is_private_chat=False,
            sender_name='Bob',
            chat_title='Other Group',
            text='hello',
            content_type='text',
            private_enabled=True,
        )
        self.assertIsNone(payload)

    def test_is_self_sender_matches_login_account_id(self):
        self.assertTrue(_is_self_sender(SimpleNamespace(id='12345'), 12345))
        self.assertFalse(_is_self_sender(SimpleNamespace(id='12345'), 67890))
        self.assertFalse(_is_self_sender(SimpleNamespace(id='abc'), 12345))

    def test_build_bark_request_defaults_to_foldable_notification(self):
        url, params = _build_bark_request(
            'https://api.day.app/key/重要警告',
            title='📨 私聊消息',
            body='收到一条新的私聊消息',
            config={},
        )

        self.assertEqual(url, 'https://api.day.app/key/重要警告')
        self.assertEqual(params['title'], '📨 私聊消息')
        self.assertEqual(params['body'], '收到一条新的私聊消息')
        self.assertEqual(params['level'], 'active')
        self.assertEqual(params['volume'], '5')
        self.assertEqual(params['sound'], 'paymentsuccess')
        self.assertEqual(params['group'], 'telegram-listener')

    def test_build_bark_request_keeps_existing_url_parameters(self):
        url, params = _build_bark_request(
            'https://api.day.app/key/重要警告?level=timeSensitive&volume=3&sound=alarm',
            title='📨 私聊消息',
            body='收到一条新的私聊消息',
            config={},
        )

        self.assertEqual(url, 'https://api.day.app/key/重要警告?level=timeSensitive&volume=3&sound=alarm')
        self.assertEqual(params['level'], 'timeSensitive')
        self.assertEqual(params['volume'], '3')
        self.assertEqual(params['sound'], 'alarm')
        self.assertEqual(params['group'], 'telegram-listener')

    def test_build_bark_request_keeps_existing_group_parameter(self):
        url, params = _build_bark_request(
            'https://api.day.app/key/重要警告?group=shop-alerts',
            title='📨 私聊消息',
            body='收到一条新的私聊消息',
            config={},
        )

        self.assertEqual(url, 'https://api.day.app/key/重要警告?group=shop-alerts')
        self.assertEqual(params['group'], 'shop-alerts')

    def test_build_bark_request_adds_ciphertext_and_iv_when_encrypted(self):
        url, params = _build_bark_request(
            'https://api.day.app/key/重要警告?level=critical&volume=5&sound=paymentsuccess',
            title='📨 私聊消息',
            body='收到一条新的私聊消息',
            config={
                'encryption_key': '12345678901234567890123456789012',
                'encryption_iv': '1234567890123456',
                'encryption_algorithm': 'AES256',
                'encryption_mode': 'CBC',
                'encryption_padding': 'pkcs7',
            },
        )

        self.assertEqual(url, 'https://api.day.app/key')
        self.assertEqual(params['iv'], '1234567890123456')
        self.assertIn('ciphertext', params)
        self.assertNotIn('title', params)
        self.assertNotIn('body', params)

    def test_build_bark_request_accepts_hex_encoded_key(self):
        url, params = _build_bark_request(
            'https://api.day.app/key/重要警告?level=critical&volume=5&sound=paymentsuccess',
            title='重要警告',
            body='Bark 加密测试',
            config={
                'encryption_key': '00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff',
                'encryption_iv': '1234567890123456',
            },
        )

        self.assertEqual(url, 'https://api.day.app/key')
        self.assertEqual(params['iv'], '1234567890123456')
        self.assertIn('ciphertext', params)

    def test_build_bark_request_infers_aes128_from_key_length(self):
        url, params = _build_bark_request(
            'https://api.day.app/key/重要警告',
            title='重要警告',
            body='Bark 加密测试',
            config={
                'encryption_key': 'A7mK9qX2vR4pL8zN',
                'encryption_iv': 'Q8nT4xLp2Vb7Ks1M',
            },
        )

        self.assertEqual(url, 'https://api.day.app/key')
        self.assertEqual(params['iv'], 'Q8nT4xLp2Vb7Ks1M')
        self.assertIn('ciphertext', params)


class RetainedIpRenewalUiTestCase(SimpleTestCase):
    def test_validate_reinstall_proxy_link_keeps_strict_port_check_by_default(self):
        order = SimpleNamespace(
            id=1,
            public_ip='1.2.3.4',
            previous_public_ip='1.2.3.4',
            mtproxy_port=9528,
            mtproxy_secret='abcdef1234567890',
            login_password='',
            login_user='root',
        )
        link_data = {
            'server': '1.2.3.4',
            'port': '443',
            'secret': 'abcdef1234567890',
            'url': 'tg://proxy?server=1.2.3.4&port=443&secret=abcdef1234567890',
        }

        ok, reason = async_to_sync(_validate_reinstall_proxy_link)(
            order,
            link_data,
            probe_when_possible=False,
        )

        self.assertFalse(ok)
        self.assertIn('当前主代理端口是 9528', reason)

    def test_validate_reinstall_proxy_link_rejects_target_ip_mismatch_before_probe(self):
        order = SimpleNamespace(
            id=1,
            public_ip='13.228.232.184',
            previous_public_ip='13.228.232.184',
            mtproxy_port=443,
            mtproxy_secret='abcdef1234567890',
            login_password='would-not-probe',
            login_user='root',
        )
        link_data = {
            'server': '54.151.227.23',
            'port': '443',
            'secret': 'abcdef1234567890',
            'url': 'tg://proxy?server=54.151.227.23&port=443&secret=***',
        }

        ok, reason = async_to_sync(_validate_reinstall_proxy_link)(
            order,
            link_data,
            probe_when_possible=True,
        )

        self.assertFalse(ok)
        self.assertIn('链接 IP 不匹配', reason)
        self.assertIn('13.228.232.184', reason)

    def test_validate_reinstall_proxy_link_allows_client_port_override_for_reinstall(self):
        order = SimpleNamespace(
            id=1,
            public_ip='1.2.3.4',
            previous_public_ip='1.2.3.4',
            mtproxy_port=9528,
            mtproxy_secret='abcdef1234567890',
            login_password='',
            login_user='root',
        )
        link_data = {
            'server': '1.2.3.4',
            'port': '443',
            'secret': 'abcdef1234567890',
            'url': 'tg://proxy?server=1.2.3.4&port=443&secret=abcdef1234567890',
        }

        ok, reason = async_to_sync(_validate_reinstall_proxy_link)(
            order,
            link_data,
            probe_when_possible=False,
            allow_client_port=True,
        )

        self.assertTrue(ok)
        self.assertEqual(reason, '主链接格式和 IP 校验通过')

    def test_retained_ip_renewal_plan_keyboard_uses_three_columns(self):
        plans = [SimpleNamespace(id=index) for index in range(1, 8)]

        markup = _retained_ip_renewal_plan_keyboard(123, plans)

        self.assertEqual([len(row) for row in markup.inline_keyboard[:-1]], [3, 3, 1])
        self.assertEqual(markup.inline_keyboard[-1][0].text, '🔙 返回详情')

    def test_retained_ip_renewal_texts_are_configurable(self):
        self.assertIn('bot_retained_ip_renewal_plan_intro', BOT_TEXTS)
        self.assertIn('bot_retained_ip_renewal_plan_footer', BOT_TEXTS)
        self.assertIn('bot_retained_ip_renewal_link_prompt', BOT_TEXTS)
