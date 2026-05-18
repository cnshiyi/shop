from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.conf import settings
import os
import secrets


class Command(BaseCommand):
    help = 'Ensure a default dashboard admin exists'

    def handle(self, *args, **options):
        User = get_user_model()
        username = (os.getenv('DASHBOARD_ADMIN_USERNAME') or 'admin').strip() or 'admin'
        password = (os.getenv('DASHBOARD_ADMIN_PASSWORD') or '').strip()
        email = (os.getenv('DASHBOARD_ADMIN_EMAIL') or '').strip()

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
            elif not password:
                self.stdout.write(
                    self.style.WARNING(
                        'DASHBOARD_ADMIN_PASSWORD is not set; existing admin password was not changed.',
                    ),
                )
            if email and user.email != email:
                user.email = email
                changed = True
            if changed:
                user.save()
                self.stdout.write(self.style.SUCCESS(f'Updated dashboard admin: {username}'))
            else:
                self.stdout.write(self.style.SUCCESS(f'Dashboard admin already ready: {username}'))
            return

        generated_password = False
        if not password:
            if not settings.DEBUG:
                raise CommandError(
                    'DASHBOARD_ADMIN_PASSWORD is required when creating a dashboard admin with DEBUG=0.',
                )
            password = secrets.token_urlsafe(24)
            generated_password = True

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f'Created dashboard admin: {username}'))
        if generated_password:
            self.stdout.write(
                self.style.WARNING(
                    f'Generated development password for {username}: {password}',
                ),
            )
            self.stdout.write(
                self.style.WARNING(
                    'Set DASHBOARD_ADMIN_PASSWORD to use a stable local password.',
                ),
            )
