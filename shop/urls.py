from django.urls import include, path
from core.views import index

urlpatterns = [
    path('api/admin/', include(('shop.dashboard_urls', 'dashboard_api'), namespace='dashboard_api_admin')),
    path('api/dashboard/', include(('shop.dashboard_urls', 'dashboard_api'), namespace='dashboard_api_dashboard')),
    path('api/', include(('shop.dashboard_urls', 'dashboard_api'), namespace='dashboard_api_root')),
    path('', index, name='index'),
]
