from django.urls import path
from trading import views

app_name = 'trading'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.dashboard_view, name='dashboard'),
    path('api/status/', views.api_status_view, name='api_status'),
    path('api/trigger-cycle/', views.trigger_cycle_view, name='trigger_cycle'),
    path('api/accounts/create/', views.create_account_view, name='create_account'),
    path('api/accounts/<int:account_id>/update/', views.update_account_view, name='update_account'),
    path('api/accounts/<int:account_id>/test/', views.test_account_view, name='test_account'),
    path('api/accounts/<int:account_id>/toggle/', views.toggle_account_view, name='toggle_account'),
    path('api/accounts/<int:account_id>/seed-symbols/', views.seed_account_symbols_view, name='seed_account_symbols'),
    path('api/accounts/<int:account_id>/toggle-bots/', views.toggle_account_bots_view, name='toggle_account_bots'),
    path('api/bot/create/', views.create_bot_view, name='create_bot'),
    path('api/bot/<int:bot_id>/update/', views.update_bot_view, name='update_bot'),
    path('api/bot/<int:bot_id>/toggle/', views.toggle_bot_view, name='toggle_bot'),
]
