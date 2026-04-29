from django.conf import settings
from django.shortcuts import redirect


def index(request):
    return redirect(settings.ADMIN_FRONTEND_URL)
