"""bot 域模型。"""

from django.db import models

from core.crypto import decrypt_text, encrypt_text


class TelegramLoginAccount(models.Model):
    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    label = models.CharField('账号备注', max_length=191, db_comment='账号备注')
    phone = models.CharField('手机号', max_length=64, blank=True, null=True, db_comment='手机号')
    tg_user_id = models.BigIntegerField('Telegram 用户ID', blank=True, null=True, db_index=True, db_comment='Telegram 用户ID')
    username = models.CharField('用户名', max_length=191, blank=True, null=True, db_comment='用户名')
    status = models.CharField('状态', max_length=32, default='pending', db_index=True, db_comment='状态')
    phone_code_hash = models.CharField('验证码哈希', max_length=191, blank=True, null=True, db_comment='加密存储的Telegram验证码哈希')
    session_string = models.TextField('Telegram会话', blank=True, null=True, db_comment='加密存储的Telegram会话字符串')
    notify_enabled = models.BooleanField('允许通知', default=True, db_index=True, db_comment='允许通知')
    listener_push_enabled = models.BooleanField('个人号监听推送', default=True, db_index=True, db_comment='个人号监听推送')
    note = models.TextField('备注', blank=True, null=True, db_comment='备注')
    last_synced_at = models.DateTimeField('最近同步时间', blank=True, null=True, db_comment='最近同步时间')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'bot_telegram_login_account'
        db_table_comment = 'Telegram 登录账号和会话配置表'
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
    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    tg_user_id = models.BigIntegerField('Telegram 用户ID', unique=True, db_index=True, db_comment='Telegram 用户唯一数字ID')
    username = models.TextField('用户名集合', blank=True, null=True, db_comment='用户名集合')
    first_name = models.CharField('昵称', max_length=191, blank=True, null=True, db_comment='昵称')
    balance = models.DecimalField('USDT余额', max_digits=18, decimal_places=6, default=0, db_comment='用户USDT可用余额')
    balance_trx = models.DecimalField('TRX余额', max_digits=18, decimal_places=6, default=0, db_comment='用户TRX可用余额')
    cloud_discount_rate = models.DecimalField('云服务器专属折扣', max_digits=5, decimal_places=2, default=100, help_text='百分比，100 表示无折扣，90 表示 9 折', db_comment='云服务器折扣百分比，100表示无折扣')
    cloud_reminder_muted_until = models.DateTimeField('云服务器提醒静默到', blank=True, null=True, db_comment='云服务器提醒静默到')
    admin_forward_muted_until = models.DateTimeField('管理员转发静默到', blank=True, null=True, db_index=True, db_comment='管理员转发静默到')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'bot_user'
        db_table_comment = 'Telegram 用户账户和余额表'
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
        raw = (
            str(value)
            .replace('，', ',')
            .replace('｜', ',')
            .replace('|', ',')
            .replace(' / ', ',')
            .replace('/', ',')
        )
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

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    user = models.ForeignKey(TelegramUser, verbose_name='Telegram用户', on_delete=models.SET_NULL, blank=True, null=True, related_name='operation_logs', db_comment='Telegram用户')
    tg_user_id = models.BigIntegerField('Telegram用户ID', db_index=True, db_comment='Telegram用户ID')
    chat_id = models.BigIntegerField('会话ID', blank=True, null=True, db_index=True, db_comment='会话ID')
    message_id = models.BigIntegerField('消息ID', blank=True, null=True, db_comment='消息ID')
    action_type = models.CharField('操作类型', max_length=32, choices=ACTION_CHOICES, db_index=True, db_comment='操作类型')
    action_label = models.CharField('操作说明', max_length=191, blank=True, null=True, db_comment='操作说明')
    payload = models.TextField('操作内容', blank=True, null=True, db_comment='操作内容')
    username_snapshot = models.CharField('用户名快照', max_length=191, blank=True, null=True, db_index=True, db_comment='用户名快照')
    first_name_snapshot = models.CharField('昵称快照', max_length=191, blank=True, null=True, db_comment='昵称快照')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True, db_comment='创建时间')

    class Meta:
        db_table = 'bot_operation_log'
        db_table_comment = 'Telegram 机器人用户操作日志表'
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
    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    chat_id = models.BigIntegerField('会话ID', unique=True, db_index=True, db_comment='会话ID')
    title = models.CharField('会话标题', max_length=191, blank=True, null=True, db_comment='会话标题')
    note = models.TextField('备注', blank=True, null=True, db_comment='备注')
    created_at = models.DateTimeField('归档时间', auto_now_add=True, db_comment='归档时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'bot_telegram_chat_archive'
        db_table_comment = 'Telegram 会话归档表'
        verbose_name = 'Telegram归档会话'
        verbose_name_plural = 'Telegram归档会话'
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return self.title or str(self.chat_id)


class TelegramGroupFilter(models.Model):
    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    chat_id = models.BigIntegerField('群组会话ID', unique=True, db_index=True, db_comment='群组会话ID')
    title = models.CharField('群组名称', max_length=191, blank=True, null=True, db_comment='群组名称')
    username = models.CharField('群组用户名', max_length=191, blank=True, null=True, db_index=True, db_comment='群组用户名')
    enabled = models.BooleanField('允许转发', default=False, db_index=True, db_comment='允许转发')
    push_enabled = models.BooleanField('允许推送', default=False, db_index=True, db_comment='允许推送')
    collapsed = models.BooleanField('折叠', default=False, db_index=True, db_comment='折叠')
    archived = models.BooleanField('归档', default=False, db_index=True, db_comment='归档')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'bot_telegram_group_filter'
        db_table_comment = 'Telegram 群组转发和推送过滤表'
        verbose_name = 'Telegram群组过滤'
        verbose_name_plural = 'Telegram群组过滤'
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return self.title or (self.username and f'@{self.username}') or str(self.chat_id)


class AdminReplyLink(models.Model):
    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    admin_chat_id = models.BigIntegerField('管理员会话ID', db_index=True, db_comment='管理员会话ID')
    admin_message_id = models.BigIntegerField('管理员消息ID', db_index=True, db_comment='管理员消息ID')
    user = models.ForeignKey(TelegramUser, verbose_name='Telegram用户', on_delete=models.CASCADE, related_name='admin_reply_links', db_comment='Telegram用户')
    user_chat_id = models.BigIntegerField('用户会话ID', db_index=True, db_comment='用户会话ID')
    user_message_id = models.BigIntegerField('用户消息ID', blank=True, null=True, db_comment='用户消息ID')
    source_content_type = models.CharField('原消息类型', max_length=32, default='text', db_comment='原消息类型')
    is_active = models.BooleanField('启用', default=True, db_index=True, db_comment='启用')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True, db_comment='创建时间')

    class Meta:
        db_table = 'bot_admin_reply_link'
        db_table_comment = '管理员消息与用户会话回复映射表'
        verbose_name = '管理员回复通道'
        verbose_name_plural = '管理员回复通道'
        unique_together = [('admin_chat_id', 'admin_message_id')]
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['admin_chat_id', 'admin_message_id'], name='idx_admin_reply_msg'),
            models.Index(fields=['user_chat_id', '-created_at'], name='idx_admin_reply_user_time'),
        ]

    def __str__(self):
        return f'{self.admin_chat_id}:{self.admin_message_id} -> {self.user_chat_id}'


class TelegramChatMessage(models.Model):
    DIRECTION_IN = 'in'
    DIRECTION_OUT = 'out'
    DIRECTION_CHOICES = [
        (DIRECTION_IN, '收到'),
        (DIRECTION_OUT, '发出'),
    ]

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    user = models.ForeignKey(TelegramUser, verbose_name='Telegram用户', on_delete=models.SET_NULL, blank=True, null=True, related_name='chat_messages', db_comment='Telegram用户')
    login_account = models.ForeignKey(TelegramLoginAccount, verbose_name='登录账号', on_delete=models.SET_NULL, blank=True, null=True, related_name='chat_messages', db_comment='登录账号')
    tg_user_id = models.BigIntegerField('Telegram用户ID', db_index=True, db_comment='Telegram用户ID')
    chat_id = models.BigIntegerField('会话ID', db_index=True, db_comment='会话ID')
    message_id = models.BigIntegerField('消息ID', blank=True, null=True, db_comment='消息ID')
    direction = models.CharField('方向', max_length=8, choices=DIRECTION_CHOICES, default=DIRECTION_IN, db_index=True, db_comment='方向')
    content_type = models.CharField('消息类型', max_length=32, default='text', db_comment='消息类型')
    text = models.TextField('消息内容', blank=True, null=True, db_comment='消息内容')
    username_snapshot = models.CharField('用户名快照', max_length=191, blank=True, null=True, db_index=True, db_comment='用户名快照')
    first_name_snapshot = models.CharField('昵称快照', max_length=191, blank=True, null=True, db_comment='昵称快照')
    chat_title = models.CharField('会话标题', max_length=191, blank=True, null=True, db_comment='会话标题')
    source = models.CharField('来源', max_length=32, default='bot', db_index=True, db_comment='来源')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True, db_comment='创建时间')

    class Meta:
        db_table = 'bot_telegram_chat_message'
        db_table_comment = 'Telegram 聊天消息归档表'
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
    'AdminReplyLink',
    'BotOperationLog',
    'BotUser',
    'TelegramChatArchive',
    'TelegramChatMessage',
    'TelegramGroupFilter',
    'TelegramLoginAccount',
    'TelegramUser',
]
