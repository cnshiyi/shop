import asyncio
import inspect
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
from django.urls import Resolver404, resolve
from django.utils import timezone

from bot.api import DASHBOARD_SESSION_IDLE_SECONDS, _active_proxy_counts_by_user, _authenticate_dashboard_request, admin_users_list, archive_telegram_chat, auth_totp_start, create_admin_user, create_cloud_account, create_product, delete_cloud_account, me, send_daily_expiry_summary_test_notification, send_telegram_chat_message, site_config_groups, telegram_login_start, update_cloud_account, update_site_config, users_list, verify_cloud_account
from bot.handlers import _asset_reinstall_confirm_keyboard, _asset_reinstall_submitted_keyboard, _asset_renewal_plan_keyboard, _buy_cloud_server_with_balance_and_notify, _cloud_renewal_postcheck_and_notify, _cloud_renewal_result_keyboard, _cloud_server_created_text, _fetch_tron_address_summary, _hydrate_order_proxy_links, _install_notice_copy_wrapper, _pay_cloud_server_order_with_balance_and_notify, _proxy_links_text, _reinstall_confirm_keyboard, _reinstall_submitted_keyboard, _requires_recovery_provision, _retained_ip_renewal_plan_keyboard, _save_asset_main_proxy_link, _save_user_main_proxy_link, _trongrid_get_with_key_fallback, _trongrid_post_with_key_fallback, _validate_reinstall_proxy_link, register_handlers
from bot.keyboards import _compact_back_button_callback, append_back_callback, balance_details_list, cloud_asset_detail_callback, cloud_auto_renew_callback, cloud_detail_callback, cloud_previous_detail_callback, compact_callback_path, cloud_ip_query_result, cloud_order_list, cloud_order_readonly_detail, cloud_server_change_ip_region_menu, cloud_server_detail, cloud_server_list, cloud_server_renew_payment
from bot.models import TelegramChatArchive, TelegramChatMessage, TelegramGroupFilter, TelegramLoginAccount, TelegramUser
from bot.services import record_telegram_message, telegram_group_delivery_flags
from bot.states import CustomServerStates
from bot.telegram_listener import _build_bark_request, _build_push_payload, _is_bot_sender, _is_self_sender, _sync_account_profile
from cloud import services as cloud_services
from cloud.asset_expiry import order_asset_expiry
from cloud.models import CloudAsset, CloudServerOrder, CloudServerPlan
from cloud.services import prepare_cloud_server_order_instances, update_cloud_item_expiry_for_admin
from core.models import CloudAccountConfig, SiteConfig
from core.texts import BOT_TEXTS
from orders.ledger import record_balance_ledger
from orders.models import Product, Recharge
from orders.services import list_balance_details, list_cloud_orders


class ApiPrefixContractTestCase(SimpleTestCase):
    def test_only_auth_and_admin_api_prefixes_are_routed(self):
        expected_routes = {
            '/api/csrf/': 'api-csrf',
            '/api/auth/login': 'auth_api:login',
            '/api/auth/refresh': 'auth_api:refresh',
            '/api/admin/user/info': 'admin_api:user-info',
            '/api/admin/dashboard/overview/': 'admin_api:overview',
            '/api/admin/cloud-assets/sync-jobs/metrics/': 'admin_api:cloud-assets-sync-jobs-metrics',
        }

        for path, view_name in expected_routes.items():
            self.assertEqual(resolve(path).view_name, view_name)

        removed_routes = [
            '/api/dashboard/overview/',
            '/api/dashboard/dashboard/overview/',
            '/api/dashboard/auth/login',
            '/api/admin/auth/login',
            '/api/admin/task-list/',
            '/api/admin/plan-settings/',
            '/api/users/',
        ]
        for path in removed_routes:
            with self.assertRaises(Resolver404):
                resolve(path)


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

    def test_bearer_dashboard_request_does_not_create_cookie_session(self):
        from django.contrib.sessions.backends.db import SessionStore

        user = get_user_model().objects.create_user(username='dashboard_bearer_staff', password='pass', is_staff=True)
        bearer_session = SessionStore()
        bearer_session['_auth_user_id'] = str(user.pk)
        bearer_session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
        bearer_session['_auth_user_hash'] = user.get_session_auth_hash()
        bearer_session.set_expiry(60)
        bearer_session.save()
        request = RequestFactory().get('/api/admin/cloud-assets/')
        SessionMiddleware(lambda req: None).process_request(request)
        request.user = AnonymousUser()
        request.META['HTTP_AUTHORIZATION'] = f'Bearer session-{bearer_session.session_key}'

        authenticated = _authenticate_dashboard_request(request)

        self.assertEqual(authenticated, user)
        self.assertIsNone(request.session.session_key)
        self.assertFalse(request.session.modified)
        refreshed = Session.objects.get(session_key=bearer_session.session_key)
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
        request = self._authorized_get('/api/admin/dashboard/me/', user)

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
    def test_group_push_switch_defaults_off_and_can_be_enabled(self):
        flags = async_to_sync(telegram_group_delivery_flags)(
            chat_id=-10070003,
            title='Default Silent Group',
            username='default_silent_group',
        )

        self.assertEqual(flags, {'enabled': False, 'push_enabled': False})
        group = TelegramGroupFilter.objects.get(chat_id=-10070003)
        self.assertFalse(group.enabled)
        self.assertFalse(group.push_enabled)

        group.push_enabled = True
        group.save(update_fields=['push_enabled', 'updated_at'])

        flags = async_to_sync(telegram_group_delivery_flags)(
            chat_id=-10070003,
            title='Default Silent Group',
            username='default_silent_group',
        )
        self.assertEqual(flags, {'enabled': False, 'push_enabled': True})

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
    def _attach_bearer_session(self, request, user):
        SessionMiddleware(lambda req: None).process_request(request)
        request.session['_auth_user_id'] = str(user.pk)
        request.session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
        request.session['_auth_user_hash'] = user.get_session_auth_hash()
        request.session.save()
        request.user = AnonymousUser()
        request.META['HTTP_AUTHORIZATION'] = f'Bearer session-{request.session.session_key}'
        return request

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
        self._attach_bearer_session(create_request, staff)
        self.assertEqual(create_cloud_account(create_request).status_code, 403)
        self.assertFalse(CloudAccountConfig.objects.filter(name='blocked-create').exists())

        update_request = RequestFactory().post(
            f'/api/admin/settings/cloud-accounts/{account.id}/',
            data=json.dumps({'name': 'blocked-update'}),
            content_type='application/json',
        )
        self._attach_bearer_session(update_request, staff)
        self.assertEqual(update_cloud_account(update_request, account.id).status_code, 403)
        account.refresh_from_db()
        self.assertEqual(account.name, 'aws-staff-forbidden')

        verify_request = RequestFactory().post(f'/api/admin/settings/cloud-accounts/{account.id}/verify/')
        self._attach_bearer_session(verify_request, staff)
        self.assertEqual(verify_cloud_account(verify_request, account.id).status_code, 403)

        delete_request = RequestFactory().post(f'/api/admin/settings/cloud-accounts/{account.id}/delete/')
        self._attach_bearer_session(delete_request, staff)
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
        self._attach_bearer_session(create_request, root)
        with patch('bot.api_cloud_accounts._fetch_cloud_account_external_account_id', return_value='123456789012'):
            create_response = create_cloud_account(create_request)
        self.assertEqual(create_response.status_code, 200)
        account_id = json.loads(create_response.content.decode('utf-8'))['data']['id']

        update_request = RequestFactory().post(
            f'/api/admin/settings/cloud-accounts/{account_id}/',
            data=json.dumps({'name': 'aws-root-updated', 'is_active': False}),
            content_type='application/json',
        )
        self._attach_bearer_session(update_request, root)
        self.assertEqual(update_cloud_account(update_request, account_id).status_code, 200)
        account = CloudAccountConfig.objects.get(id=account_id)
        self.assertEqual(account.name, 'aws-root-updated')
        self.assertFalse(account.is_active)

        delete_request = RequestFactory().post(f'/api/admin/settings/cloud-accounts/{account_id}/delete/')
        self._attach_bearer_session(delete_request, root)
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
        self._attach_bearer_session(create_request, root)
        with patch('bot.api_cloud_accounts._fetch_cloud_account_external_account_id', return_value=existing.external_account_id):
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
        self._attach_bearer_session(update_request, root)
        with patch('bot.api_cloud_accounts._fetch_cloud_account_external_account_id', return_value=existing.external_account_id):
            update_response = update_cloud_account(update_request, other.id)
        update_payload = json.loads(update_response.content.decode('utf-8'))

        self.assertEqual(update_response.status_code, 400)
        self.assertIn('云厂商账号ID已存在', update_payload['message'])
        other.refresh_from_db()
        self.assertFalse(other.external_account_id)

    def test_cloud_account_access_key_must_be_unique_per_provider_without_external_account_id(self):
        root = get_user_model().objects.create_user(username='cloud_account_ak_unique_root', password='pass', is_staff=True, is_superuser=True)
        existing = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-ak-existing',
            access_key='same-access-key',
            secret_key='aws-sk-existing',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        other = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-ak-other',
            access_key='other-access-key',
            secret_key='aws-sk-other',
            region_hint='ap-southeast-1',
            is_active=True,
        )

        create_request = RequestFactory().post(
            '/api/admin/settings/cloud-accounts/create/',
            data=json.dumps({
                'provider': CloudAccountConfig.PROVIDER_AWS,
                'name': 'aws-ak-duplicate',
                'access_key': existing.access_key_plain,
                'secret_key': 'aws-sk-new',
            }),
            content_type='application/json',
        )
        self._attach_bearer_session(create_request, root)
        with patch('bot.api_cloud_accounts._fetch_cloud_account_external_account_id', return_value='999999999999'):
            create_response = create_cloud_account(create_request)
        create_payload = json.loads(create_response.content.decode('utf-8'))

        self.assertEqual(create_response.status_code, 400)
        self.assertIn('云账号 Access Key 已存在', create_payload['message'])
        self.assertFalse(CloudAccountConfig.objects.filter(name='aws-ak-duplicate').exists())

        update_request = RequestFactory().post(
            f'/api/admin/settings/cloud-accounts/{other.id}/',
            data=json.dumps({'access_key': existing.access_key_plain}),
            content_type='application/json',
        )
        self._attach_bearer_session(update_request, root)
        with patch('bot.api_cloud_accounts._fetch_cloud_account_external_account_id', return_value='888888888888'):
            update_response = update_cloud_account(update_request, other.id)
        update_payload = json.loads(update_response.content.decode('utf-8'))

        self.assertEqual(update_response.status_code, 400)
        self.assertIn('云账号 Access Key 已存在', update_payload['message'])
        other.refresh_from_db()
        self.assertEqual(other.access_key_plain, 'other-access-key')

    def test_cloud_account_save_fetches_account_id_and_blocks_duplicate_different_keys(self):
        root = get_user_model().objects.create_user(username='cloud_account_identity_unique_root', password='pass', is_staff=True, is_superuser=True)
        existing = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-identity-existing',
            external_account_id='123456789012',
            access_key='aws-ak-existing',
            secret_key='aws-sk-existing',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        other = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-identity-other',
            external_account_id='222222222222',
            access_key='aws-ak-other',
            secret_key='aws-sk-other',
            region_hint='ap-southeast-1',
            is_active=True,
        )

        create_request = RequestFactory().post(
            '/api/admin/settings/cloud-accounts/create/',
            data=json.dumps({
                'provider': CloudAccountConfig.PROVIDER_AWS,
                'name': 'aws-identity-duplicate',
                'access_key': 'different-access-key',
                'secret_key': 'different-secret-key',
            }),
            content_type='application/json',
        )
        self._attach_bearer_session(create_request, root)
        with patch('bot.api_cloud_accounts._fetch_cloud_account_external_account_id', return_value=existing.external_account_id):
            create_response = create_cloud_account(create_request)
        create_payload = json.loads(create_response.content.decode('utf-8'))

        self.assertEqual(create_response.status_code, 400)
        self.assertIn('云厂商账号ID已存在', create_payload['message'])
        self.assertFalse(CloudAccountConfig.objects.filter(name='aws-identity-duplicate').exists())

        update_request = RequestFactory().post(
            f'/api/admin/settings/cloud-accounts/{other.id}/',
            data=json.dumps({
                'access_key': 'another-different-access-key',
                'secret_key': 'another-different-secret-key',
            }),
            content_type='application/json',
        )
        self._attach_bearer_session(update_request, root)
        with patch('bot.api_cloud_accounts._fetch_cloud_account_external_account_id', return_value=existing.external_account_id):
            update_response = update_cloud_account(update_request, other.id)
        update_payload = json.loads(update_response.content.decode('utf-8'))

        self.assertEqual(update_response.status_code, 400)
        self.assertIn('云厂商账号ID已存在', update_payload['message'])
        other.refresh_from_db()
        self.assertEqual(other.external_account_id, '222222222222')
        self.assertEqual(other.access_key_plain, 'aws-ak-other')

    def test_cloud_account_update_allows_rotated_key_for_same_external_account_id(self):
        root = get_user_model().objects.create_user(username='cloud_account_rotate_root', password='pass', is_staff=True, is_superuser=True)
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-rotated-key',
            external_account_id='123456789012',
            access_key='expired-access-key',
            secret_key='expired-secret-key',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        update_request = RequestFactory().post(
            f'/api/admin/settings/cloud-accounts/{account.id}/',
            data=json.dumps({
                'access_key': 'new-access-key',
                'secret_key': 'new-secret-key',
            }),
            content_type='application/json',
        )
        self._attach_bearer_session(update_request, root)

        with patch('bot.api_cloud_accounts._fetch_cloud_account_external_account_id', return_value=account.external_account_id):
            update_response = update_cloud_account(update_request, account.id)

        self.assertEqual(update_response.status_code, 200)
        account.refresh_from_db()
        self.assertEqual(account.external_account_id, '123456789012')
        self.assertEqual(account.access_key_plain, 'new-access-key')
        self.assertEqual(account.secret_key_plain, 'new-secret-key')

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
        self._attach_bearer_session(request, staff)

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
        self._attach_bearer_session(request, staff)

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
        self._attach_bearer_session(request, staff)

        with patch.dict(sys.modules, {'alibabacloud_swas_open20200601': fake_aliyun_module}), \
            patch('cloud.aliyun_simple._build_client', return_value=FakeClient()) as build_client, \
            patch('bot.api_cloud_accounts._fetch_aliyun_account_id', return_value='aliyun-owner-1'):
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

    def test_build_push_payload_skips_bot_sender(self):
        payload = _build_push_payload(
            is_outgoing=False,
            is_private_chat=True,
            sender_name='Notify Bot',
            chat_title='Notify Bot',
            text='bot notice',
            content_type='text',
            private_enabled=True,
            sender_is_bot=True,
        )
        self.assertIsNone(payload)

    def test_is_self_sender_matches_login_account_id(self):
        self.assertTrue(_is_self_sender(SimpleNamespace(id='12345'), 12345))
        self.assertFalse(_is_self_sender(SimpleNamespace(id='12345'), 67890))
        self.assertFalse(_is_self_sender(SimpleNamespace(id='abc'), 12345))

    def test_is_bot_sender_uses_telegram_user_bot_flag(self):
        self.assertTrue(_is_bot_sender(SimpleNamespace(id=12345, bot=True)))
        self.assertFalse(_is_bot_sender(SimpleNamespace(id=12345, bot=False)))
        self.assertFalse(_is_bot_sender(None))

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

            async def fake_copy(_bot, chat_id, text, parse_mode=None, **_kwargs):
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
    def test_cloud_detail_callbacks_keep_nested_back_path(self):
        back_callback = 'profile:orders:cloud:filter:paid:page:2'

        self.assertEqual(
            cloud_detail_callback(88, back_callback),
            'cloud:detail:88:poc:paid:2',
        )
        self.assertEqual(
            cloud_asset_detail_callback(99, back_callback),
            'cad:99:poc:paid:2',
        )
        self.assertEqual(
            cloud_previous_detail_callback(88, 'cloud:ad:asset:99:cloud:list:page:3'),
            'cad:99:clp:3',
        )
        self.assertEqual(
            cloud_previous_detail_callback(88, back_callback),
            'cloud:detail:88:poc:paid:2',
        )
        self.assertEqual(compact_callback_path(back_callback), 'poc:paid:2')
        self.assertEqual(compact_callback_path('cloud:list:page:12345'), 'clp:12345')

    def test_cloud_server_detail_actions_keep_back_path(self):
        markup = cloud_server_detail(
            88,
            can_renew=True,
            can_change_ip=True,
            can_reinit=True,
            back_callback='profile:orders:cloud:filter:paid:page:2',
            can_upgrade=True,
        )
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

        self.assertIn('cloud:renew:88:poc:paid:2', callbacks)
        self.assertIn('cloud:ip:88:poc:paid:2', callbacks)
        self.assertIn('cloud:reinit:88:poc:paid:2', callbacks)
        self.assertIn('cloud:upgrade:88:poc:paid:2', callbacks)
        self.assertIn('poc:paid:2', callbacks)
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks if item))

    def test_cloud_server_detail_actions_from_long_asset_detail_stay_under_callback_limit(self):
        back_callback = 'cloud:ad:asset:9999999:cloud:list:page:12345'
        markup = cloud_server_detail(
            9999999,
            can_renew=True,
            can_change_ip=True,
            can_reinit=True,
            back_callback=back_callback,
            can_upgrade=True,
        )
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

        self.assertIn('cloud:renew:9999999:cad:9999999:clp:12345', callbacks)
        self.assertIn('cloud:ip:9999999:cad:9999999:clp:12345', callbacks)
        self.assertIn('cloud:reinit:9999999:cad:9999999:clp:12345', callbacks)
        self.assertIn('cloud:upgrade:9999999:cad:9999999:clp:12345', callbacks)
        self.assertIn('cad:9999999:clp:12345', callbacks)
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks if item))

    def test_cloud_server_detail_back_button_from_extreme_nested_detail_stays_under_limit(self):
        item_id = 999999999999999999
        back_callback = f'cloud:detail:{item_id}:profile:orders:cloud:filter:provisioning:page:{item_id}'

        markup = cloud_server_detail(
            item_id,
            can_renew=True,
            can_change_ip=True,
            can_reinit=True,
            back_callback=back_callback,
            can_upgrade=True,
        )
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]

        self.assertIn(f'poc:provisioning:{item_id}', callbacks)
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks))

    def test_cloud_server_detail_back_button_falls_back_to_cloud_list_when_source_is_too_long(self):
        back_callback = 'x' * 100

        markup = cloud_server_detail(
            88,
            can_renew=False,
            can_change_ip=False,
            can_reinit=False,
            back_callback=back_callback,
        )
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]

        self.assertIn('cloud:list', callbacks)
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks))

    def test_detail_back_buttons_fall_back_when_source_is_too_long(self):
        source = inspect.getsource(register_handlers)
        asset_detail_source = source.split('async def cb_cloud_asset_detail', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:assetinit:'))", 1)[0]
        admin_expiry_source = source.split('async def msg_cloud_admin_expiry_time', 1)[1].split("@dp.callback_query(F.data.startswith('balance:detail:'))", 1)[0]

        self.assertIn("_compact_back_button_callback(':'.join(back_parts))", asset_detail_source)
        self.assertIn("_compact_back_button_callback(data.get('admin_expiry_back') or 'cloud:querymenu')", admin_expiry_source)

        readonly_markup = cloud_order_readonly_detail(88, 'x' * 100)
        readonly_callbacks = [button.callback_data for row in readonly_markup.inline_keyboard for button in row if button.callback_data]

        self.assertIn('cloud:list', readonly_callbacks)
        self.assertEqual(_compact_back_button_callback('x' * 100), 'cloud:list')
        self.assertTrue(all(len(item.encode()) <= 64 for item in readonly_callbacks))

    def test_asset_detail_direct_action_buttons_compact_back_callback(self):
        source = inspect.getsource(register_handlers)
        asset_detail_source = source.split('async def cb_cloud_asset_detail', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:assetinit:'))", 1)[0]

        self.assertIn("append_back_callback(f'cloud:assetinit:{item_id}', back_callback)", asset_detail_source)
        self.assertIn("append_back_callback(f'exp:a:{item_id}', back_callback)", asset_detail_source)
        reinstall_callback = f'cloud:assetinit:9999999:{compact_callback_path("cloud:ad:asset:9999999:cloud:list:page:12345")}'
        admin_expiry_callback = f'exp:a:9999999:{compact_callback_path("cloud:ad:asset:9999999:cloud:list:page:12345")}'
        self.assertLessEqual(len(reinstall_callback.encode()), 64)
        self.assertLessEqual(len(admin_expiry_callback.encode()), 64)

    def test_reinstall_cancel_buttons_keep_back_path(self):
        order_markup = _reinstall_confirm_keyboard(88, 'token', 'cloud:list:page:3')
        resume_markup = _reinstall_confirm_keyboard(88, 'token', 'cloud:list:page:3', resume_init=True)
        asset_markup = _asset_reinstall_confirm_keyboard(99, 'token', 'cloud:querymenu')

        self.assertEqual(order_markup.inline_keyboard[0][0].text, '确认重建迁移')
        self.assertEqual(resume_markup.inline_keyboard[0][0].text, '确认继续初始化')
        self.assertEqual(asset_markup.inline_keyboard[0][0].text, '确认重建迁移')
        self.assertEqual(order_markup.inline_keyboard[1][0].callback_data, 'cloud:detail:88:clp:3')
        self.assertEqual(asset_markup.inline_keyboard[1][0].callback_data, 'cad:99:cloud:querymenu')

    def test_reinstall_submitted_buttons_keep_back_path(self):
        order_markup = _reinstall_submitted_keyboard(88, 'cloud:querymenu')
        asset_markup = _asset_reinstall_submitted_keyboard(99, 'cloud:list:page:3')
        nested_asset_markup = _asset_reinstall_submitted_keyboard(
            999999999999999999,
            'cloud:ad:asset:999999999999999999:cloud:list:page:999999999999999999',
        )
        callbacks = [
            order_markup.inline_keyboard[0][0].callback_data,
            asset_markup.inline_keyboard[0][0].callback_data,
            nested_asset_markup.inline_keyboard[0][0].callback_data,
        ]

        self.assertEqual(callbacks[0], 'cloud:detail:88:cloud:querymenu')
        self.assertEqual(callbacks[1], 'cad:99:clp:3')
        self.assertEqual(callbacks[2], 'cad:999999999999999999:clp:999999999999999999')
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks))

    def test_reinstall_confirm_handlers_reuse_saved_back_path_after_submit(self):
        source = inspect.getsource(register_handlers)
        asset_confirm_source = source.split('async def cb_cloud_asset_reinit_confirm', 1)[1].split("@dp.callback_query(F.data.startswith('d:'))", 1)[0]
        order_confirm_source = source.split('async def cb_cloud_reinit_confirm', 1)[1].split("@dp.callback_query(F.data.startswith('exp:'))", 1)[0]

        self.assertIn("back_callback = data.get('reinstall_back')", asset_confirm_source)
        self.assertIn('_asset_reinstall_submitted_keyboard(asset_id, back_callback)', asset_confirm_source)
        self.assertIn('reason=not_rebuild_order', asset_confirm_source)
        self.assertIn('retry_only=False', asset_confirm_source)
        self.assertNotIn('继续初始化当前服务器', asset_confirm_source)
        self.assertIn("back_callback = data.get('reinstall_back')", order_confirm_source)
        self.assertIn('_reinstall_submitted_keyboard(order.id, back_callback)', order_confirm_source)
        self.assertIn("action_text = '重建迁移' if is_rebuild else '继续初始化'", order_confirm_source)
        self.assertNotIn("else '重新安装'", order_confirm_source)

    def test_asset_renewal_plan_keyboard_keeps_back_path(self):
        plans = [SimpleNamespace(id=1)]

        markup = _asset_renewal_plan_keyboard(99, plans, 'cloud:querymenu')

        self.assertEqual(markup.inline_keyboard[0][0].callback_data, 'arp:99:1:cloud:querymenu')
        self.assertEqual(markup.inline_keyboard[-1][0].callback_data, 'cad:99:cloud:querymenu')

    def test_retained_ip_renewal_plan_keyboard_keeps_back_path(self):
        plans = [SimpleNamespace(id=7)]

        markup = _retained_ip_renewal_plan_keyboard(88, plans, 'cloud:querymenu')

        self.assertEqual(markup.inline_keyboard[0][0].callback_data, 'rnp:88:7:cloud:querymenu')
        self.assertEqual(markup.inline_keyboard[-1][0].callback_data, 'cloud:detail:88:cloud:querymenu')

    def test_second_level_cloud_actions_with_large_ids_stay_under_callback_limit(self):
        back_callback = compact_callback_path('cloud:ad:asset:1234567890:profile:orders:cloud:filter:renew:page:1234567890')
        plans = [SimpleNamespace(id=1234567890)]
        callbacks = [
            _asset_renewal_plan_keyboard(1234567890, plans, back_callback).inline_keyboard[0][0].callback_data,
            _retained_ip_renewal_plan_keyboard(1234567890, plans, back_callback).inline_keyboard[0][0].callback_data,
            append_back_callback('upp:1234567890:1234567890', back_callback),
            append_back_callback('exp:a:1234567890', back_callback),
        ]

        self.assertEqual(callbacks[0], 'arp:1234567890:1234567890:cad:1234567890:poc:renew:1234567890')
        self.assertEqual(callbacks[1], 'rnp:1234567890:1234567890:cad:1234567890:poc:renew:1234567890')
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks if item))

    def test_extreme_nested_cloud_callbacks_stay_under_telegram_limit(self):
        item_id = 999999999999999999
        back_callback = f'cloud:ad:asset:{item_id}:cloud:list:page:{item_id}'
        regions = [
            ('ap-southeast-1', '新加坡'),
            ('ap-northeast-1', '日本'),
            ('us-east-1', '美国'),
            ('eu-west-2', '英国'),
            ('ap-south-1', '印度'),
            ('ca-central-1', '加拿大'),
        ]
        plans = [SimpleNamespace(id=item_id)]
        markups = [
            cloud_server_detail(item_id, True, True, True, back_callback, True),
            cloud_server_renew_payment(item_id, Decimal('12.3'), Decimal('45.6'), back_callback=back_callback),
            cloud_server_change_ip_region_menu(item_id, regions, back_callback=back_callback),
            _asset_renewal_plan_keyboard(item_id, plans, back_callback),
            _retained_ip_renewal_plan_keyboard(item_id, plans, back_callback),
        ]
        callbacks = [button.callback_data for markup in markups for row in markup.inline_keyboard for button in row if button.callback_data]

        self.assertIn(f'r:{item_id}:a:{item_id}:{item_id}', callbacks)
        self.assertIn(f'ir:{item_id}:as1:a:{item_id}', callbacks)
        self.assertIn(f'arp:{item_id}:{item_id}:a:{item_id}', callbacks)
        self.assertEqual(compact_callback_path(f'a:{item_id}:{item_id}'), f'cad:{item_id}:clp:{item_id}')
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks))

    def test_cloud_renew_payment_keyboard_keeps_back_path(self):
        markup = cloud_server_renew_payment(88, Decimal('12.3'), Decimal('45.6'), back_callback='cloud:querymenu')
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

        self.assertIn('cloud:rp:88:USDT:cloud:querymenu', callbacks)
        self.assertIn('cloud:rp:88:TRX:cloud:querymenu', callbacks)
        self.assertIn('cloud:detail:88:cloud:querymenu', callbacks)
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks if item))

    def test_cloud_renew_payment_from_asset_detail_returns_to_asset_detail(self):
        asset_detail_back = 'cloud:ad:asset:99:cloud:list:page:3'
        markup = cloud_server_renew_payment(88, Decimal('12.3'), Decimal('45.6'), back_callback=asset_detail_back)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        compact_asset_detail_back = 'cad:99:clp:3'

        self.assertIn(f'cloud:rp:88:USDT:{compact_asset_detail_back}', callbacks)
        self.assertIn(compact_asset_detail_back, callbacks)
        self.assertNotIn(f'cloud:detail:88:{compact_asset_detail_back}', callbacks)
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks if item))

    def test_cloud_renew_payment_from_long_asset_detail_stays_under_callback_limit(self):
        asset_detail_back = 'cloud:ad:asset:9999999:cloud:list:page:12345'
        markup = cloud_server_renew_payment(9999999, Decimal('12.3'), Decimal('45.6'), back_callback=asset_detail_back)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

        self.assertIn('cloud:rp:9999999:USDT:cad:9999999:clp:12345', callbacks)
        self.assertIn('cloud:rp:9999999:TRX:cad:9999999:clp:12345', callbacks)
        self.assertIn('cad:9999999:clp:12345', callbacks)
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks if item))

    def test_cloud_renewal_result_branches_keep_back_path(self):
        item_id = 999999999999999999
        back_callback = f'cloud:ad:asset:{item_id}:cloud:list:page:{item_id}'
        markup = _cloud_renewal_result_keyboard(item_id, back_callback)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]
        source = inspect.getsource(register_handlers)
        wallet_source = source.split('async def cb_cloud_renew_wallet', 1)[1].split("@dp.callback_query(F.data.startswith('p:'))", 1)[0]
        renew_pay_source = source.split('async def cb_cloud_renew_pay', 1)[1].split("@dp.callback_query(F.data.startswith('i:'))", 1)[0]

        self.assertEqual(callbacks, [f'cad:{item_id}:clp:{item_id}'])
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks))
        self.assertGreaterEqual(wallet_source.count('reply_markup=_cloud_renewal_result_keyboard'), 4)
        self.assertGreaterEqual(renew_pay_source.count('reply_markup=_cloud_renewal_result_keyboard'), 4)

    def test_wallet_balance_purchase_auto_submits_default_port(self):
        order = SimpleNamespace(
            id=88,
            order_no='SRV-BALANCE-443',
            region_name='新加坡',
            plan_name='nano',
            quantity=1,
            pay_amount=Decimal('19.00'),
            currency='USDT',
            mtproxy_port=443,
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        scheduled = []

        def capture_task(coro):
            scheduled.append(coro.cr_code.co_name)
            coro.close()
            return object()

        with patch('bot.handlers.buy_cloud_server_with_balance', new=AsyncMock(return_value=(order, None))) as buy_mock, \
                patch('bot.handlers.prepare_cloud_server_order_instances', new=AsyncMock(return_value=[order])) as prepare_mock, \
                patch('bot.handlers._send_admin_user_action_notice', new=AsyncMock()), \
                patch('bot.handlers.main_menu', return_value=None), \
                patch('bot.handlers.asyncio.create_task', side_effect=capture_task):
            async_to_sync(_buy_cloud_server_with_balance_and_notify)(bot, 12345, 7, 55, 1, 'USDT')

        buy_mock.assert_awaited_once_with(7, 55, 'USDT', 1)
        prepare_mock.assert_awaited_once_with(88, 7, 443)
        bot.send_message.assert_awaited_once()
        text = bot.send_message.await_args.kwargs['text']
        self.assertIn('端口: 443', text)
        self.assertIn('创建任务已提交', text)
        self.assertEqual(scheduled, ['_provision_cloud_server_and_notify'])

    def test_cloud_background_tasks_keep_high_concurrency_isolated(self):
        class FakeBot:
            def __init__(self):
                self.messages = []

            async def send_message(self, **kwargs):
                self.messages.append(kwargs)
                await asyncio.sleep(0)
                return SimpleNamespace(message_id=len(self.messages))

        def order_view(order_id, order_no, *, quantity=1):
            return SimpleNamespace(
                id=order_id,
                order_no=order_no,
                region_name='新加坡',
                plan_name='nano',
                quantity=quantity,
                pay_amount=Decimal('19.00'),
                currency='USDT',
                mtproxy_port=443,
                public_ip=f'10.0.0.{order_id % 255}',
                previous_public_ip='',
                status='completed',
            )

        async def fake_buy(user_id, plan_id, currency, quantity):
            await asyncio.sleep(0.02 if user_id == 7 else 0)
            return order_view(801, 'BOT-CONCURRENT-BUY', quantity=quantity), None

        async def fake_pay(order_id, user_id, currency):
            await asyncio.sleep(0.01)
            return order_view(802, 'BOT-CONCURRENT-PAY'), None

        async def fake_prepare(order_id, user_id, port):
            await asyncio.sleep(0)
            if order_id == 801:
                return [
                    SimpleNamespace(id=811, mtproxy_port=443),
                    SimpleNamespace(id=812, mtproxy_port=443),
                ]
            return [SimpleNamespace(id=821, mtproxy_port=443)]

        async def fake_postcheck(order_id):
            await asyncio.sleep(0.015)
            return order_view(order_id, 'BOT-CONCURRENT-RENEW'), None

        scheduled = []

        def capture_task(coro):
            frame_locals = dict(getattr(coro, 'cr_frame', None).f_locals)
            scheduled.append((
                coro.cr_code.co_name,
                frame_locals.get('chat_id'),
                frame_locals.get('order_id'),
                frame_locals.get('port'),
            ))
            coro.close()
            return object()

        async def run_case():
            bot = FakeBot()
            with patch('bot.handlers.buy_cloud_server_with_balance', new=AsyncMock(side_effect=fake_buy)), \
                    patch('bot.handlers.pay_cloud_server_order_with_balance', new=AsyncMock(side_effect=fake_pay)), \
                    patch('bot.handlers.prepare_cloud_server_order_instances', new=AsyncMock(side_effect=fake_prepare)) as prepare_mock, \
                    patch('bot.handlers.run_cloud_server_renewal_postcheck', new=AsyncMock(side_effect=fake_postcheck)), \
                    patch('bot.handlers.is_cloud_asset_renewal_order', return_value=False), \
                    patch('bot.handlers._requires_recovery_provision', return_value=False), \
                    patch('bot.handlers._cloud_order_plan_text', return_value='套餐: nano\n'), \
                    patch('bot.handlers._send_admin_user_action_notice', new=AsyncMock()), \
                    patch('bot.handlers.asyncio.create_task', side_effect=capture_task):
                await asyncio.gather(
                    _buy_cloud_server_with_balance_and_notify(bot, 1001, 7, 55, 2, 'USDT'),
                    _pay_cloud_server_order_with_balance_and_notify(bot, 1002, 8, 88, 'USDT'),
                    _cloud_renewal_postcheck_and_notify(bot, 1003, 99, {'currency': 'USDT', 'amount': Decimal('1.00'), 'before': Decimal('5.00'), 'after': Decimal('4.00')}),
                )
            return bot.messages, prepare_mock.await_args_list

        messages, prepare_calls = async_to_sync(run_case)()

        self.assertEqual(
            sorted((name, chat_id, order_id, port) for name, chat_id, order_id, port in scheduled),
            [
                ('_provision_cloud_server_and_notify', 1001, 811, 443),
                ('_provision_cloud_server_and_notify', 1001, 812, 443),
                ('_provision_cloud_server_and_notify', 1002, 821, 443),
            ],
        )
        self.assertEqual(
            {(call.args[0], call.args[1], call.args[2]) for call in prepare_calls},
            {(801, 7, 443), (802, 8, 443)},
        )
        message_by_chat = {}
        for item in messages:
            message_by_chat.setdefault(item['chat_id'], []).append(item['text'])
        self.assertTrue(any('2 台服务器创建任务已提交' in text for text in message_by_chat[1001]))
        self.assertTrue(any('1 台服务器创建任务已提交' in text for text in message_by_chat[1002]))
        self.assertTrue(any('续费已完成' in text for text in message_by_chat[1003]))

    def test_cloud_background_tasks_keep_bulk_concurrency_isolated(self):
        class FakeBot:
            def __init__(self):
                self.messages = []

            async def send_message(self, **kwargs):
                self.messages.append(kwargs)
                await asyncio.sleep(0)
                return SimpleNamespace(message_id=len(self.messages))

        parent_quantities = {}

        def order_view(order_id, order_no, *, quantity=1):
            return SimpleNamespace(
                id=order_id,
                order_no=order_no,
                region_name='新加坡',
                plan_name='nano',
                quantity=quantity,
                pay_amount=Decimal('19.00'),
                currency='USDT',
                mtproxy_port=443,
                public_ip=f'10.0.{order_id % 255}.{order_id % 251}',
                previous_public_ip='',
                status='completed',
            )

        async def fake_buy(user_id, plan_id, currency, quantity):
            await asyncio.sleep((plan_id % 5) / 1000)
            order_id = 10000 + plan_id
            parent_quantities[order_id] = quantity
            return order_view(order_id, f'BOT-BULK-BUY-{plan_id}', quantity=quantity), None

        async def fake_pay(order_id, user_id, currency):
            await asyncio.sleep((order_id % 7) / 1000)
            parent_order_id = 20000 + order_id
            parent_quantities[parent_order_id] = 1
            return order_view(parent_order_id, f'BOT-BULK-PAY-{order_id}', quantity=1), None

        async def fake_prepare(order_id, user_id, port):
            await asyncio.sleep((order_id % 3) / 1000)
            quantity = parent_quantities.get(order_id, 1)
            return [
                SimpleNamespace(id=order_id * 10 + index, mtproxy_port=port)
                for index in range(quantity)
            ]

        async def fake_postcheck(order_id):
            await asyncio.sleep((order_id % 4) / 1000)
            return order_view(order_id, f'BOT-BULK-RENEW-{order_id}'), None

        scheduled = []

        def capture_task(coro):
            frame = getattr(coro, 'cr_frame', None)
            frame_locals = dict(frame.f_locals) if frame is not None else {}
            scheduled.append((
                coro.cr_code.co_name,
                frame_locals.get('chat_id'),
                frame_locals.get('order_id'),
                frame_locals.get('port'),
            ))
            coro.close()
            return object()

        async def run_case():
            bot = FakeBot()
            with patch('bot.handlers.buy_cloud_server_with_balance', new=AsyncMock(side_effect=fake_buy)), \
                    patch('bot.handlers.pay_cloud_server_order_with_balance', new=AsyncMock(side_effect=fake_pay)), \
                    patch('bot.handlers.prepare_cloud_server_order_instances', new=AsyncMock(side_effect=fake_prepare)) as prepare_mock, \
                    patch('bot.handlers.run_cloud_server_renewal_postcheck', new=AsyncMock(side_effect=fake_postcheck)), \
                    patch('bot.handlers.is_cloud_asset_renewal_order', return_value=False), \
                    patch('bot.handlers._requires_recovery_provision', return_value=False), \
                    patch('bot.handlers._cloud_order_plan_text', return_value='套餐: nano\n'), \
                    patch('bot.handlers._send_admin_user_action_notice', new=AsyncMock()), \
                    patch('bot.handlers.asyncio.create_task', side_effect=capture_task):
                tasks = []
                for index in range(20):
                    quantity = 1 + (index % 3)
                    tasks.append(_buy_cloud_server_with_balance_and_notify(bot, 30000 + index, 700 + index, 900 + index, quantity, 'USDT'))
                    tasks.append(_pay_cloud_server_order_with_balance_and_notify(bot, 40000 + index, 800 + index, 1200 + index, 'USDT'))
                    tasks.append(_cloud_renewal_postcheck_and_notify(bot, 50000 + index, 1500 + index, {'currency': 'USDT', 'amount': Decimal('1.00'), 'before': Decimal('5.00'), 'after': Decimal('4.00')}))
                await asyncio.gather(*tasks)
            return bot.messages, prepare_mock.await_args_list

        messages, prepare_calls = async_to_sync(run_case)()

        self.assertGreaterEqual(len(messages), 60)
        self.assertEqual(len({item['chat_id'] for item in messages}), 60)
        self.assertEqual(len(prepare_calls), 40)
        expected_scheduled = sum(1 + (index % 3) for index in range(20)) + 20
        self.assertEqual(len(scheduled), expected_scheduled)
        self.assertEqual({item[0] for item in scheduled}, {'_provision_cloud_server_and_notify'})
        self.assertTrue(all(port == 443 for _name, _chat_id, _order_id, port in scheduled))
        self.assertEqual(
            {chat_id for _name, chat_id, _order_id, _port in scheduled},
            {*(30000 + index for index in range(20)), *(40000 + index for index in range(20))},
        )
        for index in range(20):
            buy_texts = [item['text'] for item in messages if item['chat_id'] == 30000 + index]
            pay_texts = [item['text'] for item in messages if item['chat_id'] == 40000 + index]
            renew_texts = [item['text'] for item in messages if item['chat_id'] == 50000 + index]
            self.assertEqual(len(buy_texts), 1)
            self.assertEqual(len(pay_texts), 1)
            self.assertGreaterEqual(len(renew_texts), 1)
            self.assertIn(f'{1 + (index % 3)} 台服务器创建任务已提交', buy_texts[0])
            self.assertIn('1 台服务器创建任务已提交', pay_texts[0])
            self.assertTrue(any('续费已完成' in text for text in renew_texts))

    def test_removed_custom_port_flow_stays_removed(self):
        source = inspect.getsource(register_handlers)
        all_bot_texts = '\n'.join(value for value, _ in BOT_TEXTS.values())

        self.assertNotIn('waiting_port', {state.state.split(':')[-1] for state in CustomServerStates.__all_states__})
        self.assertNotIn('custom:port:', source)
        self.assertNotIn('cloud:ipport:', source)
        self.assertNotIn('bot_custom_port_invalid', BOT_TEXTS)
        self.assertNotIn('bot_set_port_failed', BOT_TEXTS)
        self.assertNotIn('bot_custom_port_hint', BOT_TEXTS)
        self.assertNotIn('bot_custom_port_success', BOT_TEXTS)
        self.assertNotIn('以你发送的主链接端口为准', all_bot_texts)
        self.assertNotIn('系统记录的主端口不对', all_bot_texts)
        self.assertNotIn('确认重新安装', all_bot_texts)
        self.assertNotIn('重新安装大约', all_bot_texts)
        self.assertNotIn('期间代理可能会断连', all_bot_texts)
        self.assertIn('未记录时使用默认端口 443', BOT_TEXTS['bot_reinstall_need_main_link'][0])
        self.assertIn('确认重建迁移', BOT_TEXTS['bot_reinstall_confirm'][0])
        self.assertIn('未记录时使用默认端口 443', BOT_TEXTS['bot_retained_ip_renewal_link_prompt'][0])
        self.assertFalse(hasattr(cloud_services, 'set_cloud_server_port'))
        self.assertNotIn('set_cloud_server_port', getattr(cloud_services, '__all__', []))

    def test_cloud_change_ip_keyboards_keep_back_path(self):
        regions = [
            ('us-east-1', '美国'),
            ('ap-northeast-1', '日本'),
            ('eu-west-2', '英国'),
            ('ap-south-1', '印度'),
            ('ap-southeast-1', '新加坡'),
            ('ca-central-1', '加拿大'),
        ]

        region_markup = cloud_server_change_ip_region_menu(88, regions, back_callback='cloud:querymenu')
        callbacks = [button.callback_data for row in region_markup.inline_keyboard for button in row]

        self.assertIn('cloud:ipregion:88:us-east-1:cloud:querymenu', callbacks)
        self.assertIn('cloud:ipregions:more:88:cloud:querymenu', callbacks)
        self.assertIn('cloud:detail:88:cloud:querymenu', callbacks)

    def test_cloud_change_ip_from_asset_detail_returns_to_asset_detail(self):
        regions = [('us-east-1', '美国')]
        asset_detail_back = 'cloud:ad:asset:99:cloud:list:page:3'
        markup = cloud_server_change_ip_region_menu(88, regions, back_callback=asset_detail_back)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        compact_asset_detail_back = 'cad:99:clp:3'

        self.assertIn(f'cloud:ipregion:88:us-east-1:{compact_asset_detail_back}', callbacks)
        self.assertIn(compact_asset_detail_back, callbacks)
        self.assertNotIn(f'cloud:detail:88:{compact_asset_detail_back}', callbacks)

    def test_asset_change_ip_action_keeps_back_path_when_rendering_regions(self):
        source = inspect.getsource(register_handlers)
        asset_action_source = source.split('async def cb_cloud_asset_action', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:assetinit:'))", 1)[0]

        self.assertIn("@dp.callback_query(F.data.startswith('cloud:aa:'))", source)
        self.assertIn(
            'cloud_server_change_ip_region_menu(order.id, regions, expanded=False, back_callback=asset_detail_back)',
            asset_action_source,
        )

    def test_cloud_change_ip_region_submission_keeps_back_path(self):
        source = inspect.getsource(register_handlers)
        region_source = source.split('async def cb_cloud_change_ip_region', 1)[1].split("@dp.callback_query(F.data.startswith('u:'))", 1)[0]

        self.assertIn("parts = callback.data.split(':', 3)", region_source)
        self.assertIn("back_callback = compact_callback_path(parts[3]) if len(parts) > 3 else ''", region_source)
        self.assertIn("parts = callback.data.split(':', 4)", region_source)
        self.assertIn("back_callback = compact_callback_path(parts[4]) if len(parts) > 4 else ''", region_source)
        self.assertIn('cloud_previous_detail_callback(order_id, back_callback)', region_source)

    def test_asset_detail_handler_keeps_current_callback_parsing(self):
        source = inspect.getsource(register_handlers)
        asset_detail_source = source.split('async def cb_cloud_asset_detail', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:assetaction:'))", 1)[0]

        self.assertNotIn("@dp.callback_query(F.data.startswith('cloud:assetdetail:'))", source)
        self.assertIn("@dp.callback_query(F.data.startswith('cloud:ad:'))", source)
        self.assertIn("@dp.callback_query(F.data.startswith('cad:'))", source)
        self.assertIn("@dp.callback_query(F.data.startswith('csd:'))", source)
        self.assertIn("parts[:2] == ['cloud', 'ad']", asset_detail_source)
        self.assertNotIn("parts[:2] == ['cloud', 'assetdetail']", asset_detail_source)
        self.assertIn("item_id = int(raw_item_id)", asset_detail_source)

    def test_compact_profile_cloud_order_callback_is_registered(self):
        source = inspect.getsource(register_handlers)
        self.assertIn("@dp.callback_query(F.data.startswith('poc:'))", source)
        self.assertIn("@dp.callback_query(F.data.startswith('clp:'))", source)
        self.assertIn("@dp.callback_query(F.data.startswith('cloud:rp:'))", source)
        self.assertNotIn("@dp.callback_query(F.data.startswith('cloud:renewpay:'))", source)
        self.assertIn("await _render_profile_cloud_orders(callback, page=page, order_filter=order_filter)", source)

    def test_cloud_order_list_uses_short_back_callback(self):
        order = SimpleNamespace(
            id=9999999,
            status='paid',
            public_ip='1.2.3.4',
            previous_public_ip='',
            pay_amount=Decimal('12.3'),
            total_amount=Decimal('12.3'),
            currency='USDT',
        )

        markup = cloud_order_list(
            [order],
            page=12345,
            total_pages=12345,
            prefix='profile:orders:cloud:filter:paid:page',
            order_filter='paid',
        )
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

        self.assertIn('cloud:orderdetail:9999999:poc:paid:12345', callbacks)
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks if item))

    def test_cloud_server_list_order_detail_uses_short_back_callback(self):
        item_id = 999999999999999999
        order = SimpleNamespace(
            id=item_id,
            status='completed',
            public_ip='1.2.3.4',
            previous_public_ip='',
            get_status_display=lambda: '已完成',
        )

        markup = cloud_server_list(
            [order],
            page=item_id,
            total_pages=item_id,
            prefix='profile:orders:cloud:filter:provisioning:page',
        )
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]

        self.assertEqual(
            cloud_detail_callback(item_id, f'profile:orders:cloud:filter:provisioning:page:{item_id}'),
            f'd:{item_id}:o:provisioning:{item_id}',
        )
        self.assertIn(f'd:{item_id}:o:provisioning:{item_id}', callbacks)
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks))

    def test_asset_detail_callback_from_extreme_order_detail_stays_under_limit(self):
        item_id = 999999999999999999
        back_callback = f'cloud:detail:{item_id}:profile:orders:cloud:filter:provisioning:page:{item_id}'

        callback_data = cloud_asset_detail_callback(item_id, back_callback)

        self.assertEqual(callback_data, f'cad:{item_id}:d:{item_id}')
        self.assertLessEqual(len(callback_data.encode()), 64)

    def test_asset_detail_callback_recompacts_nested_asset_detail_back_path(self):
        item_id = 999999999999999999
        back_callback = f'cad:{item_id}:d:{item_id}:o:provisioning:{item_id}'

        callback_data = cloud_asset_detail_callback(item_id, back_callback)

        self.assertEqual(callback_data, f'cad:{item_id}:d:{item_id}')
        self.assertLessEqual(len(callback_data.encode()), 64)

    def test_cloud_detail_handler_accepts_short_detail_callback(self):
        source = inspect.getsource(register_handlers)
        detail_source = source.split('async def cb_cloud_detail', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:mute:'))", 1)[0]

        self.assertIn("@dp.callback_query(F.data.startswith('d:'))", source)
        self.assertIn("callback.data.startswith('d:')", detail_source)
        self.assertIn('back_callback = compact_callback_path(parts[2])', detail_source)

    def test_cloud_order_detail_handler_accepts_short_back_callback(self):
        source = inspect.getsource(register_handlers)
        order_detail_source = source.split('async def cb_cloud_order_detail', 1)[1].split("@dp.callback_query(F.data.startswith('adminreply:hint:'))", 1)[0]

        self.assertIn("compact_callback_path(':'.join(parts[3:]))", order_detail_source)

    def test_cloud_ip_query_actions_return_to_query_menu(self):
        markup = cloud_ip_query_result(
            [],
            [
                {
                    'ip': '1.2.3.4',
                    'order_id': 88,
                    'asset_id': 0,
                    'can_change_ip': True,
                    'can_reinit': True,
                    'can_config': True,
                    'can_auto_renew': True,
                    'auto_renew_enabled': False,
                },
                {
                    'ip': '5.6.7.8',
                    'order_id': 0,
                    'asset_id': 99,
                    'can_change_ip': True,
                    'can_config': True,
                },
            ],
            include_reinit=True,
        )
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

        self.assertIn('cloud:renew:88:cloud:querymenu', callbacks)
        self.assertIn('cloud:ip:88:cloud:querymenu', callbacks)
        self.assertIn('cloud:reinit:88:cloud:querymenu', callbacks)
        self.assertIn('cloud:upgrade:88:cloud:querymenu', callbacks)
        self.assertIn('cloud:autorenew:on:88:cloud:querymenu', callbacks)
        self.assertIn('cloud:aa:changeip:99:cloud:querymenu', callbacks)
        self.assertIn('cloud:aa:upgrade:99:cloud:querymenu', callbacks)
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks if item))

    def test_cloud_auto_renew_callbacks_keep_nested_back_under_limit(self):
        item_id = 999999999999999999
        back_callback = f'cloud:ad:asset:{item_id}:cloud:list:page:{item_id}'
        callbacks = [
            cloud_auto_renew_callback('on', item_id, back_callback),
            cloud_auto_renew_callback('off', item_id, back_callback),
        ]
        source = inspect.getsource(register_handlers)
        asset_detail_source = source.split('async def cb_cloud_asset_detail', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:assetaction:'))", 1)[0]
        auto_renew_source = source.split('async def cb_cloud_auto_renew_toggle', 1)[1].split('def _retained_recovery_missing_payment_text', 1)[0]

        self.assertEqual(callbacks[0], f'ao:{item_id}:a:{item_id}:{item_id}')
        self.assertEqual(callbacks[1], f'af:{item_id}:a:{item_id}:{item_id}')
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks))
        self.assertIn('cloud_auto_renew_callback(', asset_detail_source)
        self.assertIn("@dp.callback_query(F.data.startswith('ao:'))", source)
        self.assertIn("_parse_cloud_auto_renew_callback_data(callback.data)", auto_renew_source)

    def test_cloud_upgrade_payment_keeps_back_path(self):
        source = inspect.getsource(register_handlers)
        asset_action_source = source.split('async def cb_cloud_asset_action', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:assetinit:'))", 1)[0]
        order_upgrade_source = source.split('async def cb_cloud_upgrade', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:upgradepay:'))", 1)[0]
        upgrade_pay_source = source.split('async def cb_cloud_upgrade_pay', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:reinit:'))", 1)[0]

        self.assertIn(
            "append_back_callback(f\"upp:{order.id}:{plan['id']}\", asset_detail_back)",
            asset_action_source,
        )
        self.assertIn(
            "append_back_callback(f\"upp:{order_id}:{plan['id']}\", back_callback)",
            order_upgrade_source,
        )
        self.assertIn("callback.data.split(':', 4)", upgrade_pay_source)
        self.assertIn('cloud_previous_detail_callback(int(raw_order_id), back_callback)', upgrade_pay_source)

    def test_cloud_action_handlers_compact_nested_back_callback_before_reuse(self):
        source = inspect.getsource(register_handlers)
        sections = [
            source.split('async def cb_cloud_renew', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:assetrenewplan:'))", 1)[0],
            source.split('async def cb_cloud_renew_wallet', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:rp:'))", 1)[0],
            source.split('async def cb_cloud_change_ip', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:ipregions:more:'))", 1)[0],
            source.split('async def cb_cloud_change_ip_regions_more', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:ipregion:'))", 1)[0],
            source.split('async def cb_cloud_change_ip_region', 1)[1].split("@dp.callback_query(F.data.startswith('u:'))", 1)[0],
            source.split('async def cb_cloud_upgrade', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:upgradepay:'))", 1)[0],
            source.split('async def cb_cloud_upgrade_pay', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:reinit:'))", 1)[0],
            source.split('async def cb_cloud_reinit', 1)[1].split("@dp.callback_query(F.data.startswith('cloud:reinitconfirm:'))", 1)[0],
        ]

        for section in sections:
            self.assertIn('back_callback = compact_callback_path(', section)

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

    def test_validate_reinstall_proxy_link_rejects_client_port_override_for_reinstall(self):
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
        self.assertIn('链接端口不匹配', reason)
        self.assertIn('当前主代理端口是 9528', reason)

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

    def test_query_link_save_rejects_asset_port_override(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='query-link-asset-port-override',
            public_ip='31.31.32.10',
            status=CloudAsset.STATUS_RUNNING,
        )
        link_data = {
            'url': 'tg://proxy?server=31.31.32.10&port=9528&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.32.10',
            'port': '9528',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        with self.assertRaisesMessage(ValueError, '当前主代理端口是 443'):
            async_to_sync(_save_asset_main_proxy_link)(asset.id, self.user.id, link_data)

        asset.refresh_from_db()
        self.assertIsNone(asset.mtproxy_link)
        self.assertIsNone(asset.mtproxy_port)

    def test_query_link_save_allows_asset_recorded_custom_port(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='query-link-asset-custom-port',
            public_ip='31.31.32.11',
            mtproxy_port=9528,
            status=CloudAsset.STATUS_RUNNING,
        )
        link_data = {
            'url': 'tg://proxy?server=31.31.32.11&port=9528&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.32.11',
            'port': '9528',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        saved = async_to_sync(_save_asset_main_proxy_link)(asset.id, self.user.id, link_data)

        self.assertEqual(saved.mtproxy_port, 9528)
        self.assertEqual(saved.mtproxy_link, link_data['url'])

    def test_query_link_save_rejects_order_port_override(self):
        order = self._cloud_order('ORDER-QUERY-LINK-PORT-OVERRIDE', status='completed', public_ip='31.31.32.12', paid=True)
        link_data = {
            'url': 'tg://proxy?server=31.31.32.12&port=9528&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.32.12',
            'port': '9528',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        with self.assertRaisesMessage(ValueError, '当前主代理端口是 443'):
            async_to_sync(_save_user_main_proxy_link)(order.id, link_data)

        order.refresh_from_db()
        self.assertFalse(order.mtproxy_link)
        self.assertEqual(order.mtproxy_port, 443)

    def test_query_link_save_allows_order_recorded_custom_port(self):
        order = self._cloud_order('ORDER-QUERY-LINK-CUSTOM-PORT', status='completed', public_ip='31.31.32.13', paid=True)
        order.mtproxy_port = 9528
        order.save(update_fields=['mtproxy_port', 'updated_at'])
        link_data = {
            'url': 'tg://proxy?server=31.31.32.13&port=9528&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.32.13',
            'port': '9528',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        saved = async_to_sync(_save_user_main_proxy_link)(order.id, link_data)

        self.assertEqual(saved.mtproxy_port, 9528)
        self.assertEqual(saved.mtproxy_link, link_data['url'])

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

    def test_paid_cloud_order_prepare_submits_default_port_directly(self):
        order = self._cloud_order('ORDER-DEFAULT-PORT-PREPARE', status='paid', paid=True)

        orders = async_to_sync(prepare_cloud_server_order_instances)(order.id, self.user.id, 443)

        self.assertEqual([item.id for item in orders], [order.id])
        order.refresh_from_db()
        self.assertEqual(order.mtproxy_port, 443)
        self.assertIn('使用默认端口 443，开始创建服务器。', order.provision_note)
        self.assertNotIn('用户已确认端口', order.provision_note)

    def test_balance_pay_existing_cloud_order_auto_submits_default_port(self):
        order = self._cloud_order('ORDER-BALANCE-PAY-443', status='pending', paid=False)
        bot = SimpleNamespace(send_message=AsyncMock())
        scheduled = []

        def capture_task(coro):
            scheduled.append(coro.cr_code.co_name)
            coro.close()
            return object()

        with patch('bot.handlers.pay_cloud_server_order_with_balance', new=AsyncMock(return_value=(order, None))) as pay_mock, \
                patch('bot.handlers.is_cloud_asset_renewal_order', return_value=False), \
                patch('bot.handlers.prepare_cloud_server_order_instances', new=AsyncMock(return_value=[order])) as prepare_mock, \
                patch('bot.handlers._send_admin_user_action_notice', new=AsyncMock()), \
                patch('bot.handlers.main_menu', return_value=None), \
                patch('bot.handlers.asyncio.create_task', side_effect=capture_task):
            async_to_sync(_pay_cloud_server_order_with_balance_and_notify)(bot, self.user.tg_user_id, self.user.id, order.id, 'USDT')

        pay_mock.assert_awaited_once_with(order.id, self.user.id, 'USDT')
        prepare_mock.assert_awaited_once_with(order.id, self.user.id, 443)
        bot.send_message.assert_awaited_once()
        text = bot.send_message.await_args.kwargs['text']
        self.assertIn('端口: 443', text)
        self.assertIn('创建任务已提交', text)
        self.assertEqual(scheduled, ['_provision_cloud_server_and_notify'])

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
        self.assertIn('cloud:start:123:cloud:querymenu', callbacks)
        self.assertIn('exp:o:123:cloud:querymenu', callbacks)
        self.assertTrue(all(len(item.encode()) <= 64 for item in callbacks if item))

    def test_admin_start_handler_keeps_query_menu_back_path(self):
        source = inspect.getsource(register_handlers)
        start_source = source.split('async def cb_cloud_start', 1)[1].split("@dp.callback_query(F.data.startswith('ao:'))", 1)[0]

        self.assertIn("parts = callback.data.split(':', 3)", start_source)
        self.assertIn("_compact_back_button_callback(parts[3] if len(parts) > 3 else 'cloud:querymenu')", start_source)
        self.assertIn("callback_data=back_callback", start_source)


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
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='expiry-server',
            public_ip='8.8.8.8',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=old_expiry,
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
        self.assertEqual(server.actual_expires_at, new_expiry)
        self.assertIsNone(order.renew_notice_sent_at)
