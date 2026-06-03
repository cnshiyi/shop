from django.urls import include, path
from bot import api as bot_api
from core.views import index

urlpatterns = [
    path('api/csrf/', bot_api.csrf, name='api-csrf'),
    path('api/auth/', include(('shop.auth_urls', 'auth_api'), namespace='auth_api')),
    path('api/admin/', include(('shop.admin_urls', 'admin_api'), namespace='admin_api')),
    path('', index, name='index'),
]
