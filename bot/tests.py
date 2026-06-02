import asyncio
import json
import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sessions.models import Session
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from bot.api import DASHBOARD_SESSION_IDLE_SECONDS, _active_proxy_counts_by_user, _authenticate_dashboard_request, admin_users_list, archive_telegram_chat, auth_totp_start, create_admin_user, create_cloud_account, create_product, delete_cloud_account, me, send_daily_expiry_summary_test_notification, send_telegram_chat_message, site_config_groups, telegram_login_start, update_cloud_account, update_site_config, users_list, verify_cloud_account
from bot.handlers import _cloud_renewal_postcheck_and_notify, _cloud_server_created_text, _fetch_tron_address_summary, _hydrate_order_proxy_links, _install_notice_copy_wrapper, _proxy_links_text, _requires_recovery_provision, _retained_ip_renewal_plan_keyboard, _trongrid_get_with_key_fallback, _trongrid_post_with_key_fallback, _validate_reinstall_proxy_link
from bot.keyboards import balance_details_list, cloud_ip_query_result, cloud_order_list
from bot.models import TelegramChatArchive, TelegramChatMessage, TelegramLoginAccount, TelegramUser
from bot.services import record_telegram_message
from bot.telegram_listener import _build_bark_request, _build_push_payload, _is_self_sender, _sync_account_profile
from cloud.asset_expiry import order_asset_expiry
from cloud.models import CloudAsset, CloudServerOrder, CloudServerPlan
from cloud.server_records import Server
from cloud.services import update_cloud_item_expiry_for_admin
from core.models import CloudAccountConfig, SiteConfig
from core.texts import BOT_TEXTS
from orders.ledger import record_balance_ledger
from orders.models import Product, Recharge
from orders.services import list_balance_details, list_cloud_orders


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


class DashboardAuthSurfaceTestCase(TestCase):
    def _attach_bearer_session(self, request, user):
        SessionMiddleware(lambda req: None).process_request(request)
        request.session['_auth_user_id'] = str(user.pk)
        request.session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
        request.session['_auth_user_hash'] = user.get_session_auth_hash()
        request.session.save()
        request.user = AnonymousUser()
        request.META['HTTP_AUTHORIZATION'] = f'Bearer session-{request.session.session_key}'
        return request

    def _authorized_get(self, path, user):
        return self._attach_bearer_session(RequestFactory().get(path), user)

    def test_site_config_groups_requires_dashboard_auth(self):
        SiteConfig.set('bot_token', '123456789:test-token', sensitive=True)
        request = RequestFactory().get('/api/admin/settings/site-configs/groups/', {'group': 'bot'})
        request.user = AnonymousUser()

        response = site_config_groups(request)

        self.assertEqual(response.status_code, 401)
        self.assertNotIn(b'123456789:test-token', response.content)

    def test_sensitive_site_config_blank_value_preserves_existing_secret(self):
        root = get_user_model().objects.create_user(username='root_preserve_site_secret', password='pass', is_staff=True, is_superuser=True)
        config = SiteConfig.set('bot_token', '123456789:existing-token', sensitive=True)
        request = RequestFactory().post(
            f'/api/admin/settings/site-configs/{config.id}/',
            data=json.dumps({'value': ''}),
            content_type='application/json',
        )
        self._attach_bearer_session(request, root)

        response = update_site_config(request, config.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SiteConfig.get('bot_token'), '123456789:existing-token')
        self.assertNotIn(b'123456789:existing-token', response.content)

    # 功能：验证相关业务场景和回归行为；当前函数属于 Telegram Bot 和后台用户能力。
    def test_trongrid_api_key_blank_value_preserves_and_masks_existing_secret(self):
        root = get_user_model().objects.create_user(username='root_preserve_trongrid_key', password='pass', is_staff=True, is_superuser=True)
        config = SiteConfig.set('trongrid_api_key', 'tg-test-key-one\ntg-test-key-two', sensitive=True)
        request = RequestFactory().post(
            f'/api/admin/settings/site-configs/{config.id}/',
            data=json.dumps({'value': ''}),
            content_type='application/json',
        )
        self._attach_bearer_session(request, root)

        response = update_site_config(request, config.id)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SiteConfig.get('trongrid_api_key'), 'tg-test-key-one\ntg-test-key-two')
        self.assertEqual(payload['value'], '')
        self.assertNotEqual(payload['value_preview'], 'tg-test-key-one\ntg-test-key-two')
        self.assertNotIn(b'tg-test-key-one', response.content)
        self.assertNotIn(b'tg-test-key-two', response.content)

    def test_dashboard_me_accepts_bearer_session(self):
        user = get_user_model().objects.create_user(username='dashboard_me_staff', password='pass', is_staff=True)
        request = self._authorized_get('/api/dashboard/me/', user)

        response = me(request)
        payload = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['data']['id'], user.id)
        self.assertTrue(payload['data']['is_staff'])

    def test_admin_user_management_requires_superuser(self):
        staff = get_user_model().objects.create_user(username='staff_no_admin_user_manage', password='pass', is_staff=True)
        request = RequestFactory().post(
            '/api/admin/admin-users/',
            data=json.dumps({'username': 'new_root', 'password': 'StrongPass123!', 'is_superuser': True}),
            content_type='application/json',
        )
        self._attach_bearer_session(request, staff)

        response = create_admin_user(request)
        payload = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.filter(username='new_root').exists())
        self.assertEqual(payload['message'], '需要超级管理员权限')

    def test_sensitive_dashboard_write_actions_require_superuser(self):
        staff = get_user_model().objects.create_user(username='staff_no_sensitive_write', password='pass', is_staff=True)
        config = SiteConfig.objects.create(key='cloud_delete_time', value='15:00', is_sensitive=False)
        product_payload = {'name': 'blocked-product', 'price': '1.00', 'stock': 1}

        cases = [
            (auth_totp_start, RequestFactory().post('/api/admin/auth/totp/start', data=json.dumps({}), content_type='application/json'), ()),
            (update_site_config, RequestFactory().post(f'/api/admin/settings/site-configs/{config.id}/', data=json.dumps({'value': '16:00'}), content_type='application/json'), (config.id,)),
            (telegram_login_start, RequestFactory().post('/api/admin/telegram/login/start/', data=json.dumps({'phone': '+10000000000'}), content_type='application/json'), ()),
            (send_telegram_chat_message, RequestFactory().post('/api/admin/telegram/messages/send/', data=json.dumps({'chat_id': 1, 'text': 'blocked'}), content_type='application/json'), ()),
            (create_product, RequestFactory().post('/api/admin/products/create/', data=json.dumps(product_payload), content_type='application/json'), ()),
        ]

        for view_func, request, args in cases:
            self._attach_bearer_session(request, staff)
            response = view_func(request, *args)
            payload = json.loads(response.content.decode('utf-8'))
            self.assertEqual(response.status_code, 403)
            self.assertEqual(payload['message'], '需要超级管理员权限')

        config.refresh_from_db()
        self.assertEqual(config.value, '15:00')
        self.assertFalse(Product.objects.filter(name='blocked-product').exists())

    def test_superuser_can_list_admin_users(self):
        root = get_user_model().objects.create_user(username='root_admin_user_manage', password='pass', is_staff=True, is_superuser=True)
        request = RequestFactory().get('/api/admin/admin-users/')
        request.user = root

        response = admin_users_list(request)
        payload = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(item['username'] == root.username for item in payload['data']))

    def test_dashboard_write_rejects_cookie_only_session(self):
        root = get_user_model().objects.create_user(username='root_cookie_only_write', password='pass', is_staff=True, is_superuser=True)
        request = RequestFactory().post(
            '/api/admin/admin-users/',
            data=json.dumps({'username': 'blocked_cookie_only', 'password': 'StrongPass123!', 'is_superuser': True}),
            content_type='application/json',
        )
        SessionMiddleware(lambda req: None).process_request(request)
        request.session['_auth_user_id'] = str(root.pk)
        request.session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
        request.session['_auth_user_hash'] = root.get_session_auth_hash()
        request.session.save()
        request.user = root

        response = create_admin_user(request)

        self.assertEqual(response.status_code, 401)
        self.assertFalse(get_user_model().objects.filter(username='blocked_cookie_only').exists())

    def test_archive_telegram_chat_parses_string_false_as_unarchive(self):
        user = get_user_model().objects.create_user(username='dashboard_archive_staff', password='pass', is_staff=True, is_superuser=True)
        TelegramChatArchive.objects.create(chat_id=-10012345, title='Archived Group')
        TelegramChatMessage.objects.create(
            tg_user_id=12345,
            chat_id=-10012345,
            message_id=1,
            direction=TelegramChatMessage.DIRECTION_IN,
            content_type='text',
            text='hello',
            chat_title='Archived Group',
        )
        request = RequestFactory().post(
            '/api/admin/telegram/chats/archive/',
            data=json.dumps({'chat_id': -10012345, 'archived': 'false'}),
            content_type='application/json',
        )
        self._attach_bearer_session(request, user)

        response = archive_telegram_chat(request)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TelegramChatArchive.objects.filter(chat_id=-10012345).exists())


class BotRunnerWarmCacheTestCase(SimpleTestCase):
    def test_warm_trx_rate_cache_swallows_rate_errors(self):
        from bot.runner import warm_trx_rate_cache

        with patch('bot.runner.get_trx_price', new=AsyncMock(side_effect=RuntimeError('rate failed'))):
            async_to_sync(warm_trx_rate_cache)()


class TelegramMessageRecordingTestCase(TestCase):
    def test_recording_same_tg_user_refreshes_name_and_prioritizes_new_username(self):
        async_to_sync(record_telegram_message)(
            tg_user_id=70000,
            chat_id=70000,
            message_id=1,
            direction=TelegramChatMessage.DIRECTION_IN,
            content_type='text',
            text='old profile',
            username='old_user',
            first_name='旧昵称',
            source='bot',
        )

        with self.assertLogs('bot.services', level='INFO') as log:
            async_to_sync(record_telegram_message)(
                tg_user_id=70000,
                chat_id=70000,
                message_id=2,
                direction=TelegramChatMessage.DIRECTION_IN,
                content_type='text',
                text='new profile',
                username='new_user',
                first_name='新昵称',
                source='bot',
                active_usernames=['new_user', 'new_alias'],
            )

        user = TelegramUser.objects.get(tg_user_id=70000)
        self.assertEqual(user.first_name, '新昵称')
        self.assertEqual(user.usernames[:3], ['new_user', 'new_alias', 'old_user'])
        log_text = '\n'.join(log.output)
        self.assertIn('用户资料同步完成', log_text)
        self.assertIn('tg_user_id=70000', log_text)
        self.assertIn('previous_username=old_user', log_text)
        self.assertIn('current_username=new_user,new_alias,old_user', log_text)
        self.assertIn('previous_first_name=旧昵称', log_text)
        self.assertIn('current_first_name=新昵称', log_text)

    def test_login_account_profile_refreshes_shared_user_by_tg_id(self):
        account = TelegramLoginAccount.objects.create(
            label='旧登录账号',
            tg_user_id=70002,
            username='old_login',
            status='logged_in',
        )
        TelegramUser.objects.create(tg_user_id=70002, username='old_login', first_name='旧昵称')
        entity = SimpleNamespace(id=70002, username='new_login', first_name='新昵称', last_name='', usernames=[])

        with self.assertLogs('bot.telegram_listener', level='INFO') as log:
            async_to_sync(_sync_account_profile)(account.id, entity, note='监听中')

        account.refresh_from_db()
        user = TelegramUser.objects.get(tg_user_id=70002)
        self.assertEqual(account.tg_user_id, 70002)
        self.assertEqual(account.username, 'new_login')
        self.assertEqual(account.label, '新昵称')
        self.assertEqual(user.first_name, '新昵称')
        self.assertEqual(user.usernames[:2], ['new_login', 'old_login'])
        log_text = '\n'.join(log.output)
        self.assertIn('Telegram登录账号用户资料同步完成', log_text)
        self.assertIn(f'account_id={account.id}', log_text)
        self.assertIn('tg_user_id=70002', log_text)
        self.assertIn('previous_username=old_login', log_text)
        self.assertIn('current_username=new_login,old_login', log_text)
        self.assertIn('previous_first_name=旧昵称', log_text)
        self.assertIn('current_first_name=新昵称', log_text)

    def test_personal_account_messages_are_deduped_per_login_account(self):
        first_account = TelegramLoginAccount.objects.create(label='listener-a', status='logged_in')
        second_account = TelegramLoginAccount.objects.create(label='listener-b', status='logged_in')

        async_to_sync(record_telegram_message)(
            tg_user_id=70001,
            chat_id=70001,
            message_id=10,
            direction=TelegramChatMessage.DIRECTION_IN,
            content_type='text',
            text='first account',
            username='first_user',
            first_name='First',
            login_account_id=first_account.id,
            source='account',
        )
        async_to_sync(record_telegram_message)(
            tg_user_id=70001,
            chat_id=70001,
            message_id=10,
            direction=TelegramChatMessage.DIRECTION_IN,
            content_type='text',
            text='second account',
            username='first_user',
            first_name='First',
            login_account_id=second_account.id,
            source='account',
        )

        self.assertEqual(
            TelegramChatMessage.objects.filter(chat_id=70001, message_id=10, direction=TelegramChatMessage.DIRECTION_IN).count(),
            2,
        )


class DashboardCloudAccountVerifyTestCase(TestCase):
    def test_cloud_account_write_actions_require_superuser(self):
        staff = get_user_model().objects.create_user(username='cloud_account_write_staff', password='pass', is_staff=True)
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-staff-forbidden',
            access_key='aws-ak',
            secret_key='aws-sk',
            region_hint='ap-southeast-1',
            is_active=True,
        )

        create_request = RequestFactory().post(
            '/api/admin/settings/cloud-accounts/create/',
            data=json.dumps({
                'provider': CloudAccountConfig.PROVIDER_AWS,
                'name': 'blocked-create',
                'access_key': 'new-ak',
                'secret_key': 'new-sk',
            }),
            content_type='application/json',
        )
        create_request.user = staff
        self.assertEqual(create_cloud_account(create_request).status_code, 403)
        self.assertFalse(CloudAccountConfig.objects.filter(name='blocked-create').exists())

        update_request = RequestFactory().post(
            f'/api/admin/settings/cloud-accounts/{account.id}/',
            data=json.dumps({'name': 'blocked-update'}),
            content_type='application/json',
        )
        update_request.user = staff
        self.assertEqual(update_cloud_account(update_request, account.id).status_code, 403)
        account.refresh_from_db()
        self.assertEqual(account.name, 'aws-staff-forbidden')

        verify_request = RequestFactory().post(f'/api/admin/settings/cloud-accounts/{account.id}/verify/')
        verify_request.user = staff
        self.assertEqual(verify_cloud_account(verify_request, account.id).status_code, 403)

        delete_request = RequestFactory().post(f'/api/admin/settings/cloud-accounts/{account.id}/delete/')
        delete_request.user = staff
        self.assertEqual(delete_cloud_account(delete_request, account.id).status_code, 403)
        self.assertTrue(CloudAccountConfig.objects.filter(id=account.id).exists())

    def test_superuser_can_create_update_and_delete_unlinked_cloud_account(self):
        root = get_user_model().objects.create_user(username='cloud_account_write_root', password='pass', is_staff=True, is_superuser=True)

        create_request = RequestFactory().post(
            '/api/admin/settings/cloud-accounts/create/',
            data=json.dumps({
                'provider': CloudAccountConfig.PROVIDER_AWS,
                'name': 'aws-root-created',
                'access_key': 'aws-ak',
                'secret_key': 'aws-sk',
                'region_hint': 'ap-southeast-1',
            }),
            content_type='application/json',
        )
        create_request.user = root
        create_response = create_cloud_account(create_request)
        self.assertEqual(create_response.status_code, 200)
        account_id = json.loads(create_response.content.decode('utf-8'))['data']['id']

        update_request = RequestFactory().post(
            f'/api/admin/settings/cloud-accounts/{account_id}/',
            data=json.dumps({'name': 'aws-root-updated', 'is_active': False}),
            content_type='application/json',
        )
        update_request.user = root
        self.assertEqual(update_cloud_account(update_request, account_id).status_code, 200)
        account = CloudAccountConfig.objects.get(id=account_id)
        self.assertEqual(account.name, 'aws-root-updated')
        self.assertFalse(account.is_active)

        delete_request = RequestFactory().post(f'/api/admin/settings/cloud-accounts/{account_id}/delete/')
        delete_request.user = root
        self.assertEqual(delete_cloud_account(delete_request, account_id).status_code, 200)
        self.assertFalse(CloudAccountConfig.objects.filter(id=account_id).exists())

    def test_cloud_account_external_account_id_must_be_unique_per_provider(self):
        root = get_user_model().objects.create_user(username='cloud_account_unique_root', password='pass', is_staff=True, is_superuser=True)
        existing = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-existing',
            external_account_id='123456789012',
            access_key='aws-ak-existing',
            secret_key='aws-sk-existing',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        other = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-other',
            access_key='aws-ak-other',
            secret_key='aws-sk-other',
            region_hint='ap-southeast-1',
            is_active=True,
        )

        create_request = RequestFactory().post(
            '/api/admin/settings/cloud-accounts/create/',
            data=json.dumps({
                'provider': CloudAccountConfig.PROVIDER_AWS,
                'name': 'aws-duplicate',
                'external_account_id': existing.external_account_id,
                'access_key': 'aws-ak-new',
                'secret_key': 'aws-sk-new',
            }),
            content_type='application/json',
        )
        create_request.user = root
        create_response = create_cloud_account(create_request)
        create_payload = json.loads(create_response.content.decode('utf-8'))

        self.assertEqual(create_response.status_code, 400)
        self.assertIn('云厂商账号ID已存在', create_payload['message'])
        self.assertFalse(CloudAccountConfig.objects.filter(name='aws-duplicate').exists())

        update_request = RequestFactory().post(
            f'/api/admin/settings/cloud-accounts/{other.id}/',
            data=json.dumps({'external_account_id': existing.external_account_id}),
            content_type='application/json',
        )
        update_request.user = root
        update_response = update_cloud_account(update_request, other.id)
        update_payload = json.loads(update_response.content.decode('utf-8'))

        self.assertEqual(update_response.status_code, 400)
        self.assertIn('云厂商账号ID已存在', update_payload['message'])
        other.refresh_from_db()
        self.assertFalse(other.external_account_id)

    def test_cloud_account_verify_blocks_duplicate_external_account_id(self):
        staff = get_user_model().objects.create_user(username='aws_verify_unique_staff', password='pass', is_staff=True, is_superuser=True)
        CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-owner',
            external_account_id='123456789012',
            access_key='aws-ak-owner',
            secret_key='aws-sk-owner',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-candidate',
            access_key='aws-ak-candidate',
            secret_key='aws-sk-candidate',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        request = RequestFactory().post(
            f'/api/admin/settings/cloud-accounts/{account.id}/verify/',
            data=json.dumps({'region': 'ap-southeast-1'}),
            content_type='application/json',
        )
        request.user = staff

        fake_lightsail = SimpleNamespace(get_instances=lambda: {'instances': []})
        fake_sts = SimpleNamespace(get_caller_identity=lambda: {'Account': '123456789012'})

        with patch('boto3.client', side_effect=[fake_lightsail, fake_sts]):
            response = verify_cloud_account(request, account.id)
        payload = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response.status_code, 400)
        self.assertIn('云厂商账号ID已存在', payload['message'])
        account.refresh_from_db()
        self.assertFalse(account.external_account_id)

    def test_user_proxy_count_follows_cloud_account_active_state(self):
        root = get_user_model().objects.create_user(username='root_proxy_count', password='pass', is_staff=True, is_superuser=True)
        user = TelegramUser.objects.create(tg_user_id=900001, username='alpha / beta', first_name='Tester')
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='count-account',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account,
            account_label='aws+count-account',
            region_code='ap-southeast-1',
            asset_name='count-server',
            instance_id='count-instance',
            public_ip='203.0.113.10',
            user=user,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        self.assertEqual(_active_proxy_counts_by_user([user.id]).get(user.id), 1)
        request = RequestFactory().get('/api/admin/users/', {'keyword': str(user.tg_user_id)})
        request.user = root
        payload = json.loads(users_list(request).content.decode('utf-8'))
        self.assertEqual(payload['data'][0]['proxy_count'], 1)
        self.assertEqual(payload['data'][0]['username_label'], '@alpha｜@beta')

        account.is_active = False
        account.save(update_fields=['is_active'])

        self.assertEqual(_active_proxy_counts_by_user([user.id]).get(user.id, 0), 0)
        payload = json.loads(users_list(request).content.decode('utf-8'))
        self.assertEqual(payload['data'][0]['proxy_count'], 0)

    def test_delete_cloud_account_blocks_linked_business_data(self):
        staff = get_user_model().objects.create_user(username='cloud_account_delete_staff', password='pass', is_staff=True, is_superuser=True)
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-linked-account',
            access_key='aws-ak',
            secret_key='aws-sk',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            provider='aws_lightsail',
            cloud_account=account,
            region_code='ap-southeast-1',
            asset_name='linked-asset',
            public_ip='203.0.113.10',
            status=CloudAsset.STATUS_RUNNING,
        )
        request = RequestFactory().post(f'/api/admin/settings/cloud-accounts/{account.id}/delete/')
        request.user = staff

        response = delete_cloud_account(request, account.id)
        payload = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response.status_code, 400)
        self.assertIn('不能物理删除', payload['message'])
        self.assertTrue(CloudAccountConfig.objects.filter(id=account.id).exists())

    def test_aliyun_verify_passes_account_without_global_env_mutation(self):
        staff = get_user_model().objects.create_user(username='aliyun_verify_staff', password='pass', is_staff=True, is_superuser=True)
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-verify-account',
            external_account_id='',
            access_key='aliyun-ak',
            secret_key='aliyun-sk',
            region_hint='cn-hongkong',
            is_active=True,
        )

        class FakeClient:
            def list_instances_with_options(self, request, runtime_options):
                return SimpleNamespace(body=SimpleNamespace(to_map=lambda: {'Instances': [{'InstanceId': 'i-1'}]}))

        fake_aliyun_module = SimpleNamespace(models=SimpleNamespace(ListInstancesRequest=lambda **kwargs: kwargs))
        request = RequestFactory().post(
            f'/api/admin/settings/cloud-accounts/{account.id}/verify/',
            data=json.dumps({'region': 'cn-hongkong'}),
            content_type='application/json',
        )
        request.user = staff

        with patch.dict(sys.modules, {'alibabacloud_swas_open20200601': fake_aliyun_module}), \
            patch('cloud.aliyun_simple._build_client', return_value=FakeClient()) as build_client, \
            patch('bot.api._fetch_aliyun_account_id', return_value='aliyun-owner-1'):
            response = verify_cloud_account(request, account.id)

        self.assertEqual(response.status_code, 200)
        build_client.assert_called_once()
        self.assertEqual(build_client.call_args.kwargs['account'].id, account.id)
        account.refresh_from_db()
        self.assertEqual(account.external_account_id, 'aliyun-owner-1')


class BotOrderProxyLinkHydrationTestCase(TestCase):
    def test_hydrate_order_proxy_links_does_not_copy_cross_account_asset_links(self):
        user = TelegramUser.objects.create(tg_user_id=90001, username='owner')
        other_user = TelegramUser.objects.create(tg_user_id=90002, username='other')
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='owner-account',
            access_key='ak-owner',
            secret_key='sk-owner',
            region_hint='ap-southeast-1',
        )
        other_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='other-account',
            access_key='ak-other',
            secret_key='sk-other',
            region_hint='ap-southeast-1',
        )
        plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='small',
            price='10',
        )
        order = CloudServerOrder.objects.create(
            order_no='TEST-HYDRATE-001',
            user=user,
            plan=plan,
            provider='aws_lightsail',
            cloud_account=account,
            account_label='aws+owner-account',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='small',
            quantity=1,
            total_amount='10',
            status='completed',
            public_ip='203.0.113.30',
            mtproxy_link='',
            proxy_links=[],
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            provider='aws_lightsail',
            cloud_account=other_account,
            account_label='aws+other-account',
            user=other_user,
            region_code='ap-southeast-1',
            asset_name='foreign-asset',
            public_ip='203.0.113.30',
            status=CloudAsset.STATUS_RUNNING,
            mtproxy_link='tg://proxy?server=203.0.113.30&port=9528&secret=foreign',
            proxy_links=[{'name': 'foreign', 'url': 'tg://proxy?server=203.0.113.30&port=9528&secret=foreign', 'port': 9528}],
        )

        hydrated = async_to_sync(_hydrate_order_proxy_links)(order)

        self.assertEqual(hydrated.proxy_links, [])


class DashboardNotificationTestCase(TestCase):
    def test_daily_expiry_summary_test_endpoint_forces_send(self):
        staff = get_user_model().objects.create_user(username='daily_expiry_staff', password='pass', is_staff=True, is_superuser=True)
        request = RequestFactory().post('/api/admin/settings/site-configs/daily-expiry-summary/test/')
        SessionMiddleware(lambda req: None).process_request(request)
        request.session['_auth_user_id'] = str(staff.pk)
        request.session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
        request.session['_auth_user_hash'] = staff.get_session_auth_hash()
        request.session.save()
        request.user = staff
        request.META['HTTP_AUTHORIZATION'] = f'Bearer session-{request.session.session_key}'
        bot = MagicMock()
        bot.session.close = AsyncMock()

        with patch('bot.api_site_configs.get_runtime_config', return_value='123:test-token'):
            with patch('aiogram.Bot', return_value=bot):
                with patch('cloud.lifecycle.daily_expiry_summary_tick', new_callable=AsyncMock) as tick:
                    tick.return_value = {'sent': 1, 'today': 2, 'expired': 3}
                    response = send_daily_expiry_summary_test_notification(request)

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

    def test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated(self):
        class FakeBot:
            def __init__(self):
                self.sent = []

            async def send_message(self, *args, **kwargs):
                self.sent.append((args, kwargs))
                return SimpleNamespace(message_id=len(self.sent))

        async def run_case():
            bot = FakeBot()
            copies = []

            async def fake_copy(_bot, chat_id, text, parse_mode=None):
                copies.append((chat_id, text, parse_mode))
                if len(copies) == 1:
                    await asyncio.sleep(0.02)

            _install_notice_copy_wrapper(bot)
            with (
                patch('bot.handlers._notice_copy_recipient_ids', new=AsyncMock(return_value={'999'})),
                patch('bot.handlers._copy_user_notice_to_admins', new=fake_copy),
            ):
                await asyncio.gather(
                    bot.send_message(chat_id=101, text='first'),
                    bot.send_message(chat_id=102, text='second'),
                )
            return copies

        copies = async_to_sync(run_case)()

        self.assertEqual({item[0] for item in copies}, {101, 102})
        self.assertEqual(len(copies), 2)


class TronGridFallbackTestCase(SimpleTestCase):
    def test_bot_tron_address_summary_uses_runtime_base_url(self):
        captured_urls = []

        class FakeResponse:
            status_code = 200

            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, json=None, headers=None):
                captured_urls.append(url)
                return FakeResponse({'balance': 1000000, 'active_permission': []})

            async def get(self, url, headers=None):
                captured_urls.append(url)
                if '/v1/accounts/' in url and '/transactions' not in url:
                    return FakeResponse({'data': [{'trc20': []}]})
                return FakeResponse({'data': []})

        with patch('bot.handlers.get_config', return_value='https://tron.internal.example'), \
            patch('bot.handlers.build_trongrid_headers', new=AsyncMock(return_value={'accept': 'application/json'})), \
            patch('bot.handlers.httpx.AsyncClient', FakeClient):
            summary = async_to_sync(_fetch_tron_address_summary)('TD7cnQFUwDxPMSxruGELK6hs8YQm83Avco')

        self.assertEqual(summary['trx_balance'], 1)
        self.assertTrue(captured_urls)
        self.assertTrue(all(url.startswith('https://tron.internal.example/') for url in captured_urls))

    def test_bot_trongrid_get_retries_without_invalid_api_key(self):
        class FakeResponse:
            def __init__(self, status_code):
                self.status_code = status_code

        class FakeClient:
            def __init__(self):
                self.headers = []

            async def get(self, url, headers=None):
                self.headers.append(headers)
                return FakeResponse(401 if len(self.headers) == 1 else 200)

        client = FakeClient()

        response = async_to_sync(_trongrid_get_with_key_fallback)(client, 'https://api.trongrid.io/test', {'TRON-PRO-API-KEY': 'bad', 'accept': 'application/json'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.headers[0]['TRON-PRO-API-KEY'], 'bad')
        self.assertNotIn('TRON-PRO-API-KEY', client.headers[1])

    def test_bot_trongrid_post_retries_without_invalid_api_key(self):
        class FakeResponse:
            def __init__(self, status_code):
                self.status_code = status_code

        class FakeClient:
            def __init__(self):
                self.headers = []

            async def post(self, url, json=None, headers=None):
                self.headers.append(headers)
                return FakeResponse(401 if len(self.headers) == 1 else 200)

        client = FakeClient()

        response = async_to_sync(_trongrid_post_with_key_fallback)(client, 'https://api.trongrid.io/test', {'x': 1}, {'TRON-PRO-API-KEY': 'bad'})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('TRON-PRO-API-KEY', client.headers[1])


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

    def test_cloud_server_created_text_includes_socks5_proxy_link(self):
        order = SimpleNamespace(
            public_ip='1.2.3.4',
            mtproxy_port=443,
            mtproxy_secret='eeabcdefabcdefabcdefabcdefabcdefab617a7572652e6d6963726f736f66742e636f6d',
            mtproxy_link='tg://proxy?server=1.2.3.4&port=443&secret=eeabcdefabcdefabcdefabcdefabcdefab617a7572652e6d6963726f736f66742e636f6d',
            proxy_links=[
                {'name': '主代理 mtg', 'url': 'tg://proxy?server=1.2.3.4&port=443&secret=eeabcdefabcdefabcdefabcdefabcdefab617a7572652e6d6963726f736f66742e636f6d', 'port': '443'},
                {'name': 'SOCKS5', 'url': 'socks5://abcdefabcdefabcdefabcdefabcdefab:abcdefabcdefabcdefabcdefabcdefab@1.2.3.4:9534', 'port': '9534'},
            ],
            provision_note='',
            auto_renew_enabled=False,
            status='completed',
        )

        with patch('bot.handlers._bot_text', side_effect=lambda _key, default: default), patch('bot.handlers.get_config', side_effect=lambda _key, default=None: default):
            text = _cloud_server_created_text(order, 443)

        self.assertIn('SOCKS5:', text)
        self.assertIn('tg://socks?server=1.2.3.4&amp;port=9534&amp;user=abcdefabcdefabcdefabcdefabcdefab&amp;pass=abcdefabcdefabcdefabcdefabcdefab', text)
        self.assertNotIn('socks5://abcdefabcdefabcdefabcdefabcdefab', text)

    def test_cloud_server_created_text_recovers_socks5_from_install_note(self):
        order = SimpleNamespace(
            public_ip='1.2.3.4',
            mtproxy_port=443,
            mtproxy_secret='eeabcdefabcdefabcdefabcdefabcdefab617a7572652e6d6963726f736f66742e636f6d',
            mtproxy_link='tg://proxy?server=1.2.3.4&port=443&secret=eeabcdefabcdefabcdefabcdefabcdefab617a7572652e6d6963726f736f66742e636f6d',
            proxy_links=[],
            provision_note='MTProxy 安装完成\nSOCKS5: OK 端口 9534',
            auto_renew_enabled=False,
            status='completed',
        )

        with patch('bot.handlers._bot_text', side_effect=lambda _key, default: default), patch('bot.handlers.get_config', side_effect=lambda _key, default=None: default):
            text = _cloud_server_created_text(order, 443)

        self.assertIn('SOCKS5:', text)
        self.assertIn('tg://socks?server=1.2.3.4&amp;port=9534&amp;user=abcdefabcdefabcdefabcdefabcdefab&amp;pass=abcdefabcdefabcdefabcdefabcdefab', text)
        self.assertNotIn('socks5://abcdefabcdefabcdefabcdefabcdefab', text)

    def test_cloud_server_created_text_prefers_main_proxy_link_for_one_click(self):
        order = SimpleNamespace(
            public_ip='1.2.3.4',
            mtproxy_port=9528,
            mtproxy_secret='eeabcdefabcdefabcdefabcdefabcdefab617a7572652e6d6963726f736f66742e636f6d',
            mtproxy_link='tg://proxy?server=1.2.3.4&port=9528&secret=main',
            proxy_links=[
                {'name': '主代理 mtg', 'url': 'tg://proxy?server=1.2.3.4&port=9528&secret=main', 'port': '9528'},
                {'name': 'SOCKS5', 'url': 'socks5://abcdefabcdefabcdefabcdefabcdefab:abcdefabcdefabcdefabcdefabcdefab@1.2.3.4:9534', 'port': '9534'},
            ],
            provision_note='分享链接: https://t.me/proxy?server=1.2.3.4&port=9534&secret=wrong',
            auto_renew_enabled=False,
            status='completed',
        )

        with patch('bot.handlers._bot_text', side_effect=lambda _key, default: default), patch('bot.handlers.get_config', side_effect=lambda _key, default=None: default):
            text = _cloud_server_created_text(order, 9528)

        self.assertIn('一键链接: tg://proxy?server=1.2.3.4&amp;port=9528&amp;secret=main', text)
        self.assertNotIn('一键链接: https://t.me/proxy?server=1.2.3.4&amp;port=9534&amp;secret=wrong', text)
        self.assertNotIn('port=9534&amp;secret=wrong', text)

    def test_cloud_server_created_text_does_not_use_socks5_as_one_click(self):
        order = SimpleNamespace(
            public_ip='1.2.3.4',
            mtproxy_port=443,
            mtproxy_secret='eeabcdefabcdefabcdefabcdefabcdefab617a7572652e6d6963726f736f66742e636f6d',
            mtproxy_link='',
            proxy_links=[
                {'name': 'SOCKS5', 'url': 'socks5://abcdefabcdefabcdefabcdefabcdefab:abcdefabcdefabcdefabcdefabcdefab@1.2.3.4:9534', 'port': '9534'},
            ],
            provision_note='',
            auto_renew_enabled=False,
            status='completed',
        )

        with patch('bot.handlers._bot_text', side_effect=lambda _key, default: default), patch('bot.handlers.get_config', side_effect=lambda _key, default=None: default):
            text = _cloud_server_created_text(order, 443)

        self.assertIn('一键链接: -', text)
        self.assertIn('SOCKS5: tg://socks?server=1.2.3.4&amp;port=9534&amp;user=abcdefabcdefabcdefabcdefabcdefab&amp;pass=abcdefabcdefabcdefabcdefabcdefab', text)

    def test_proxy_links_text_converts_socks5_to_telegram_link(self):
        order = SimpleNamespace(
            mtproxy_port=443,
            mtproxy_link='tg://proxy?server=1.2.3.4&port=443&secret=main',
            proxy_links=[
                {'name': 'SOCKS5', 'url': 'socks5://abcdefabcdefabcdefabcdefabcdefab:abcdefabcdefabcdefabcdefabcdefab@1.2.3.4:9534', 'port': '9534'},
            ],
        )

        text = _proxy_links_text(order)

        self.assertIn('SOCKS5: tg://socks?server=1.2.3.4&amp;port=9534&amp;user=abcdefabcdefabcdefabcdefabcdefab&amp;pass=abcdefabcdefabcdefabcdefabcdefab', text)
        self.assertNotIn('socks5://abcdefabcdefabcdefabcdefabcdefab', text)

    def test_retained_ip_renewal_plan_keyboard_uses_three_columns(self):
        plans = [SimpleNamespace(id=index) for index in range(1, 8)]

        markup = _retained_ip_renewal_plan_keyboard(123, plans)

        self.assertEqual([len(row) for row in markup.inline_keyboard[:-1]], [3, 3, 1])
        self.assertEqual(markup.inline_keyboard[-1][0].text, '🔙 返回详情')

    def test_retained_ip_renewal_texts_are_configurable(self):
        self.assertIn('bot_retained_ip_renewal_plan_intro', BOT_TEXTS)
        self.assertIn('bot_retained_ip_renewal_plan_footer', BOT_TEXTS)
        self.assertIn('bot_retained_ip_renewal_link_prompt', BOT_TEXTS)

    def test_recovery_provision_required_includes_unbound_asset_renewal(self):
        order = SimpleNamespace(
            status='paid',
            replacement_for_id=None,
            provision_note='未绑定代理资产续费：来源资产 #123；支付完成后自动创建服务器。',
            instance_id='',
            service_started_at=None,
        )

        self.assertTrue(_requires_recovery_provision(order))

    def test_recovery_provision_required_ignores_completed_asset_renewal(self):
        order = SimpleNamespace(
            status='completed',
            replacement_for_id=None,
            provision_note='未绑定代理资产续费：来源资产 #123；恢复完成。',
            instance_id='i-abc',
            service_started_at=timezone.now(),
        )

        self.assertFalse(_requires_recovery_provision(order))

    def test_renewal_postcheck_task_reports_failure_without_raising(self):
        class FakeBot:
            def __init__(self):
                self.messages = []

            async def send_message(self, **kwargs):
                self.messages.append(kwargs)
                return SimpleNamespace(message_id=len(self.messages))

        async def run_case():
            bot = FakeBot()
            with patch('bot.handlers.run_cloud_server_renewal_postcheck', new=AsyncMock(side_effect=RuntimeError('postcheck failed'))):
                await _cloud_renewal_postcheck_and_notify(bot, 12345, 99)
            return bot.messages

        messages = async_to_sync(run_case)()

        self.assertEqual(len(messages), 2)
        self.assertIn('正在检查服务器运行状态', messages[0]['text'])
        self.assertIn('续费已完成，但续费后巡检通知失败', messages[1]['text'])


class BotOrderAndBalanceFilterTestCase(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(tg_user_id=9911001, username='filter_user')
        self.plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Micro',
            price='19.00',
            currency='USDT',
            is_active=True,
        )

    def _cloud_order(self, order_no, status='pending', public_ip='', paid=False, note=''):
        expires_at = timezone.now() + timezone.timedelta(days=30)
        order = CloudServerOrder.objects.create(
            order_no=order_no,
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
            pay_method='balance' if paid else 'address',
            status=status,
            public_ip=public_ip,
            paid_at=timezone.now() if paid else None,
            provision_note=note,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=f'{order_no}-asset',
            public_ip=public_ip,
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=expires_at,
        )
        return order

    def test_cloud_order_filters_and_button_label_prefer_ip(self):
        paid_order = self._cloud_order('ORDER-PAID-1', status='completed', public_ip='1.2.3.4', paid=True)
        unpaid_order = self._cloud_order('ORDER-PENDING-1', status='pending')
        renew_order = self._cloud_order('ORDER-RENEW-1', status='renew_pending', public_ip='2.2.2.2', note='用户发起续费')

        paid_items, paid_total = async_to_sync(list_cloud_orders)(self.user.id, order_filter='paid')
        unpaid_items, unpaid_total = async_to_sync(list_cloud_orders)(self.user.id, order_filter='unpaid')
        renew_items, renew_total = async_to_sync(list_cloud_orders)(self.user.id, order_filter='renew')

        self.assertEqual(paid_total, 1)
        self.assertEqual(paid_items[0].id, paid_order.id)
        self.assertEqual(unpaid_total, 2)
        self.assertIn(unpaid_order.id, {item.id for item in unpaid_items})
        self.assertIn(renew_order.id, {item.id for item in renew_items})
        self.assertEqual(renew_total, 1)

        markup = cloud_order_list([paid_order], 1, 1, order_filter='paid')
        button_texts = [button.text for row in markup.inline_keyboard for button in row]
        self.assertTrue(any(text.startswith('1.2.3.4 | 已完成') for text in button_texts))
        self.assertTrue(any(text == '• 已支付' for text in button_texts))

    def test_balance_detail_filters_and_pagination_callbacks_keep_filter(self):
        old_balance = self.user.balance
        self.user.balance = Decimal('50.00')
        self.user.save(update_fields=['balance', 'updated_at'])
        record_balance_ledger(
            self.user,
            ledger_type='cloud_order_balance_pay',
            currency='USDT',
            old_balance=Decimal('50.00'),
            new_balance=Decimal('31.00'),
            related_type='cloud_order',
            related_id=1,
            description='云服务器订单余额支付',
        )
        Recharge.objects.create(
            user=self.user,
            currency='USDT',
            amount='20.00',
            pay_amount='20.00',
            status='completed',
            receive_address='TTestAddress',
            completed_at=timezone.now(),
        )
        self.user.balance = old_balance
        self.user.save(update_fields=['balance', 'updated_at'])

        pay_items, pay_total = async_to_sync(list_balance_details)(self.user.id, detail_filter='pay')
        recharge_items, recharge_total = async_to_sync(list_balance_details)(self.user.id, detail_filter='recharge')

        self.assertEqual(pay_total, 1)
        self.assertEqual(pay_items[0]['direction'], 'out')
        self.assertGreaterEqual(recharge_total, 1)

        markup = balance_details_list(pay_items, page=2, total_pages=3, detail_filter='pay')
        callback_data = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn('bdpage:pay:1', callback_data)
        self.assertIn('bdpage:pay:3', callback_data)

    def test_admin_query_keyboard_includes_reinstall_and_expiry_actions(self):
        markup = cloud_ip_query_result(
            [],
            [{
                'ip': '1.2.3.4',
                'order_id': 123,
                'asset_id': 0,
                'can_reinit': True,
                'can_config': True,
                'can_auto_renew': True,
                'auto_renew_enabled': False,
            }],
            include_start=True,
            include_reinit=True,
        )
        texts = [button.text for row in markup.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

        self.assertIn('🛠 重新安装', texts)
        self.assertIn('🕒 修改时间', texts)
        self.assertIn('cloud:adminexp:order:123:cloud:querymenu', callbacks)


class BotAdminExpiryUpdateTestCase(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(tg_user_id=9912001, username='expiry_user')
        self.plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Micro',
            price='19.00',
            currency='USDT',
            is_active=True,
        )

    def test_admin_expiry_update_syncs_order_asset_and_server(self):
        old_expiry = timezone.now() + timezone.timedelta(days=5)
        new_expiry = timezone.now() + timezone.timedelta(days=40)
        order = CloudServerOrder.objects.create(
            order_no='ADMIN-EXPIRY-ORDER-1',
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
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='expiry-asset',
            public_ip='8.8.8.8',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=old_expiry,
        )
        server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='expiry-server',
            public_ip='8.8.8.8',
            status=Server.STATUS_RUNNING,
            expires_at=old_expiry,
        )

        with patch('cloud.services._refresh_dashboard_plan_snapshots_after_service_change'):
            updated, err = async_to_sync(update_cloud_item_expiry_for_admin)(order.id, 'order', new_expiry)

        self.assertIsNone(err)
        self.assertEqual(updated.id, order.id)
        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(order_asset_expiry(order), new_expiry)
        self.assertEqual(asset.actual_expires_at, new_expiry)
        self.assertEqual(server.expires_at, new_expiry)
        self.assertIsNone(order.renew_notice_sent_at)
