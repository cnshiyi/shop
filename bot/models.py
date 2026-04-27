"""bot 域模型。"""

from django.db import models

from core.crypto import decrypt_text, encrypt_text


class TelegramLoginAccount(models.Model):
    label = models.CharField('账号备注', max_length=191)
    phone = models.CharField('手机号', max_length=64, blank=True, null=True)
    username = models.CharField('用户名', max_length=191, blank=True, null=True)
    status = models.CharField('状态', max_length=32, default='pending', db_index=True)
    phone_code_hash = models.CharField('验证码哈希', max_length=191, blank=True, null=True)
    session_string = models.TextField('Telegram会话', blank=True, null=True)
    notify_enabled = models.BooleanField('允许通知', default=True, db_index=True)
    note = models.TextField('备注', blank=True, null=True)
    last_synced_at = models.DateTimeField('最近同步时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'bot_telegram_login_account'
        verbose_name = 'Telegram登录账号'
        verbose_name_plural = 'Telegram登录账号'
        ordering = ['-updated_at', '-id']

    @property
    def phone_code_hash_plain(self) -> str:
        return decrypt_text(self.phone_code_hash or '')

    @property
    def session_string_plain(self) -> str:
        return decrypt_text(self.session_string or '')

    def save(self, *args, **kwargs):
        if self.phone_code_hash and not str(self.phone_code_hash).startswith('gAAAA'):
            self.phone_code_hash = encrypt_text(self.phone_code_hash)
        if self.session_string and not str(self.session_string).startswith('gAAAA'):
            self.session_string = encrypt_text(self.session_string)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.label


class TelegramUser(models.Model):
    tg_user_id = models.BigIntegerField('Telegram 用户ID', unique=True, db_index=True)
    username = models.TextField('用户名集合', blank=True, null=True)
    first_name = models.CharField('昵称', max_length=191, blank=True, null=True)
    balance = models.DecimalField('USDT余额', max_digits=18, decimal_places=6, default=0)
    balance_trx = models.DecimalField('TRX余额', max_digits=18, decimal_places=6, default=0)
    cloud_discount_rate = models.DecimalField('云服务器专属折扣', max_digits=5, decimal_places=2, default=100, help_text='百分比，100 表示无折扣，90 表示 9 折')
    cloud_reminder_muted_until = models.DateTimeField('云服务器提醒静默到', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'bot_user'
        verbose_name = 'Telegram用户'
        verbose_name_plural = 'Telegram用户'

    def __str__(self):
        return f'{self.tg_user_id} {self.primary_username or ""}'

    @staticmethod
    def normalize_usernames(value):
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            merged = []
            for item in value:
                merged.extend(TelegramUser.normalize_usernames(item))
            value = ','.join(merged)
        raw = str(value).replace('，', ',').replace(' / ', ',').replace('/', ',')
        result = []
        seen = set()
        for item in raw.split(','):
            username = item.strip().lstrip('@')
            key = username.lower()
            if username and key not in seen:
                result.append(username)
                seen.add(key)
        return result

    @staticmethod
    def serialize_usernames(usernames):
        return ','.join(TelegramUser.normalize_usernames(usernames))

    def set_usernames(self, usernames):
        self.username = self.serialize_usernames(usernames)

    @property
    def usernames(self):
        return self.normalize_usernames(self.username)

    @property
    def primary_username(self):
        names = self.usernames
        return names[0] if names else ''


class BotOperationLog(models.Model):
    ACTION_MESSAGE = 'message'
    ACTION_CALLBACK = 'callback'
    ACTION_CHOICES = [
        (ACTION_MESSAGE, '发送消息'),
        (ACTION_CALLBACK, '点击按钮'),
    ]

    user = models.ForeignKey(TelegramUser, verbose_name='Telegram用户', on_delete=models.SET_NULL, blank=True, null=True, related_name='operation_logs')
    tg_user_id = models.BigIntegerField('Telegram用户ID', db_index=True)
    chat_id = models.BigIntegerField('会话ID', blank=True, null=True, db_index=True)
    message_id = models.BigIntegerField('消息ID', blank=True, null=True)
    action_type = models.CharField('操作类型', max_length=32, choices=ACTION_CHOICES, db_index=True)
    action_label = models.CharField('操作说明', max_length=191, blank=True, null=True)
    payload = models.TextField('操作内容', blank=True, null=True)
    username_snapshot = models.CharField('用户名快照', max_length=191, blank=True, null=True, db_index=True)
    first_name_snapshot = models.CharField('昵称快照', max_length=191, blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'bot_operation_log'
        verbose_name = '机器人操作日志'
        verbose_name_plural = '机器人操作日志'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['tg_user_id', '-created_at'], name='idx_bot_op_user_time'),
            models.Index(fields=['action_type', '-created_at'], name='idx_bot_op_action_time'),
        ]

    def __str__(self):
        return f'{self.tg_user_id} {self.action_type}'


class TelegramChatArchive(models.Model):
    chat_id = models.BigIntegerField('会话ID', unique=True, db_index=True)
    title = models.CharField('会话标题', max_length=191, blank=True, null=True)
    note = models.TextField('备注', blank=True, null=True)
    created_at = models.DateTimeField('归档时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'bot_telegram_chat_archive'
        verbose_name = 'Telegram归档会话'
        verbose_name_plural = 'Telegram归档会话'
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return self.title or str(self.chat_id)


class TelegramChatMessage(models.Model):
    DIRECTION_IN = 'in'
    DIRECTION_OUT = 'out'
    DIRECTION_CHOICES = [
        (DIRECTION_IN, '收到'),
        (DIRECTION_OUT, '发出'),
    ]

    user = models.ForeignKey(TelegramUser, verbose_name='Telegram用户', on_delete=models.SET_NULL, blank=True, null=True, related_name='chat_messages')
    login_account = models.ForeignKey(TelegramLoginAccount, verbose_name='登录账号', on_delete=models.SET_NULL, blank=True, null=True, related_name='chat_messages')
    tg_user_id = models.BigIntegerField('Telegram用户ID', db_index=True)
    chat_id = models.BigIntegerField('会话ID', db_index=True)
    message_id = models.BigIntegerField('消息ID', blank=True, null=True)
    direction = models.CharField('方向', max_length=8, choices=DIRECTION_CHOICES, default=DIRECTION_IN, db_index=True)
    content_type = models.CharField('消息类型', max_length=32, default='text')
    text = models.TextField('消息内容', blank=True, null=True)
    username_snapshot = models.CharField('用户名快照', max_length=191, blank=True, null=True, db_index=True)
    first_name_snapshot = models.CharField('昵称快照', max_length=191, blank=True, null=True)
    chat_title = models.CharField('会话标题', max_length=191, blank=True, null=True)
    source = models.CharField('来源', max_length=32, default='bot', db_index=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'bot_telegram_chat_message'
        verbose_name = 'Telegram聊天记录'
        verbose_name_plural = 'Telegram聊天记录'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['tg_user_id', '-created_at'], name='idx_tg_msg_user_time'),
            models.Index(fields=['username_snapshot', '-created_at'], name='idx_tg_msg_username_time'),
        ]

    def __str__(self):
        return f'{self.tg_user_id} {self.direction} {self.content_type}'


BotUser = TelegramUser

__all__ = [
    'BotOperationLog',
    'BotUser',
    'TelegramChatArchive',
    'TelegramChatMessage',
    'TelegramLoginAccount',
    'TelegramUser',
]
