from django.urls import include, path
from core.views import index

urlpatterns = [
    path('api/admin/', include(('dashboard_api.urls', 'dashboard_api'), namespace='dashboard_api_admin')),
    path('api/dashboard/', include(('dashboard_api.urls', 'dashboard_api'), namespace='dashboard_api_dashboard')),
    path('api/', include(('dashboard_api.urls', 'dashboard_api'), namespace='dashboard_api_root')),
    path('', index, name='index'),
]
