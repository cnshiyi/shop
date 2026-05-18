from django.urls import include, path
from core.views import index

urlpatterns = [
    path('api/', include(('dashboard_api.public_urls', 'dashboard_api_public'), namespace='dashboard_api_public')),
    path('api/admin/', include(('dashboard_api.urls', 'dashboard_api_admin'), namespace='dashboard_api_admin')),
    path('', index, name='index'),
]
