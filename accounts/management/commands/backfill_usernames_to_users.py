from django.core.management.base import BaseCommand

from accounts.models import TelegramUser


class Command(BaseCommand):
    help = '将 TelegramUsername 中的历史用户名回填到 users.username 字段'

    def handle(self, *args, **options):
        count = 0
        for user in TelegramUser.objects.prefetch_related('telegramusernames').all():
            names = []
            if user.username:
                for raw in str(user.username).replace('，', ',').replace(' / ', ',').replace('/', ',').split(','):
                    value = raw.strip().lstrip('@')
                    if value and value not in names:
                        names.append(value)
            for item in user.telegramusernames.all().order_by('-is_primary', 'username'):
                if item.username and item.username not in names:
                    names.append(item.username)
            serialized = ','.join(names)
            if user.username != serialized:
                user.username = serialized
                user.save(update_fields=['username', 'updated_at'])
                count += 1
        self.stdout.write(self.style.SUCCESS(f'回填完成: {count} 个用户'))
