from django.urls import path
from trading import views

app_name = 'trading'

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('api/status/', views.api_status_view, name='api_status'),
    path('api/trigger-cycle/', views.trigger_cycle_view, name='trigger_cycle'),
]
