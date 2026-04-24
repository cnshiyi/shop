from django.core.management.base import BaseCommand

from bot.models import TelegramUser


class Command(BaseCommand):
    help = '标准化 users.username 字段中的多用户名格式'

    def handle(self, *args, **options):
        count = 0
        for user in TelegramUser.objects.all():
            serialized = TelegramUser.serialize_usernames(user.username)
            if user.username != serialized:
                user.username = serialized
                user.save(update_fields=['username', 'updated_at'])
                count += 1
        self.stdout.write(self.style.SUCCESS(f'标准化完成: {count} 个用户'))
