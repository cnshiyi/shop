from django.urls import path

from . import views

app_name = 'dashboard_api_public'

urlpatterns = [
    path('csrf/', views.csrf, name='csrf'),
    path('auth/login', views.auth_login, name='auth-login'),
    path('auth/logout', views.auth_logout, name='auth-logout'),
    path('auth/refresh', views.auth_refresh, name='auth-refresh'),
    path('auth/codes', views.auth_codes, name='auth-codes'),
]
