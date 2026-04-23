from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.conf import settings
import os


class Command(BaseCommand):
    help = 'Ensure a default dashboard admin exists'

    def handle(self, *args, **options):
        User = get_user_model()
        username = os.getenv('DASHBOARD_ADMIN_USERNAME', 'admin')
        password = os.getenv('DASHBOARD_ADMIN_PASSWORD', 'Admin@123456')
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
                self.stdout.write(self.style.SUCCESS(f'Updated dashboard admin: {username}'))
            else:
                self.stdout.write(self.style.SUCCESS(f'Dashboard admin already ready: {username}'))
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f'Created dashboard admin: {username}'))
