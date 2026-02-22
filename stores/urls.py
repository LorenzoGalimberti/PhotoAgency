from django.urls import path
from . import views

app_name = 'stores'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('stores/', views.store_list, name='store_list'),
    path('stores/<int:pk>/', views.store_detail, name='store_detail'),
    path('stores/<int:pk>/change-status/', views.change_status, name='change_status'),
    path('import/', views.import_stores, name='import_stores'),
    path('selenium/', views.run_selenium, name='run_selenium'),
]