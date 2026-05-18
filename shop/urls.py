from django.urls import include, path
from core.views import index

urlpatterns = [
    # Keep /api/admin/ for the current frontend while avoiding duplicate
    # namespace registrations from the previous triple include setup.
    path('api/admin/', include(('dashboard_api.urls', 'dashboard_api_admin'), namespace='dashboard_api_admin')),
    path('api/', include(('dashboard_api.urls', 'dashboard_api'), namespace='dashboard_api')),
    path('', index, name='index'),
]
