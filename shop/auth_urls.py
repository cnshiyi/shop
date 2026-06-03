from django.urls import path

from bot import api as bot_api

app_name = 'auth_api'

urlpatterns = [
    path('login', bot_api.auth_login, name='login'),
    path('logout', bot_api.auth_logout, name='logout'),
    path('refresh', bot_api.auth_refresh, name='refresh'),
    path('codes', bot_api.auth_codes, name='codes'),
]
