from django.test import SimpleTestCase

from bot.telegram_listener import _build_push_payload


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
