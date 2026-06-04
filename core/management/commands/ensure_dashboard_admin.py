from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.conf import settings
import os


class Command(BaseCommand):
    help = '确保默认后台管理员存在'

    def handle(self, *args, **options):
        User = get_user_model()
        username = os.getenv('DASHBOARD_ADMIN_USERNAME', 'admin')
        password = os.getenv('DASHBOARD_ADMIN_PASSWORD')
        email = os.getenv('DASHBOARD_ADMIN_EMAIL', '')

        user = User.objects.filter(username=username).first()
        if user:
            changed = False
            if not user.is_active:
                user.is_active = True
                changed = True
            if not user.is_staff:
                user.is_staff = True
                changed = True
            if not user.is_superuser:
                user.is_superuser = True
                changed = True
            if password and not user.check_password(password):
                user.set_password(password)
                changed = True
            if email and user.email != email:
                user.email = email
                changed = True
            if changed:
                user.save()
                self.stdout.write(self.style.SUCCESS(f'后台管理员已更新：{username}'))
            else:
                self.stdout.write(self.style.SUCCESS(f'后台管理员已就绪：{username}'))
            return

        if not password and not settings.DEBUG:
            raise CommandError('生产环境创建后台管理员必须设置 DASHBOARD_ADMIN_PASSWORD。')
        User.objects.create_superuser(username=username, email=email, password=password or 'Admin@123456')
        self.stdout.write(self.style.SUCCESS(f'后台管理员已创建：{username}'))
