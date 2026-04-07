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
    path('stores/whatsapp/', views.whatsapp_list, name='whatsapp_list'),
    path('stores/<int:pk>/whatsapp-analyze/', views.analyze_whatsapp_ajax, name='analyze_whatsapp_ajax'),




    # ── Configurazione messaggi ──────────────────────────────────────────────
    path('settings/messages/', views.message_templates, name='message_templates'),
    path('settings/messages/new/', views.message_template_create, name='message_template_create'),
    path('settings/messages/<int:pk>/edit/', views.message_template_edit, name='message_template_edit'),
    path('settings/messages/<int:pk>/delete/', views.message_template_delete, name='message_template_delete'),
    path('settings/messages/<int:pk>/set-default/', views.message_template_set_default, name='message_template_set_default'),
    path('export/urls/',  views.export_stores_urls, name='export_urls'),
    path('export/csv/',   views.export_stores_csv,  name='export_csv'),
    #meta
    path('meta-ads/', views.meta_ads_search, name='meta_ads_search'),
    # ── Meta Ads Keyword Lists ───────────────────────────────────────────────────
path('meta-ads/keyword-lists/',                views.meta_ads_keyword_lists,        name='meta_ads_keyword_lists'),
path('meta-ads/keyword-lists/create/',         views.meta_ads_keyword_list_create,  name='meta_ads_keyword_list_create'),
path('meta-ads/keyword-lists/<int:pk>/update/', views.meta_ads_keyword_list_update, name='meta_ads_keyword_list_update'),
path('meta-ads/keyword-lists/<int:pk>/delete/', views.meta_ads_keyword_list_delete, name='meta_ads_keyword_list_delete'),

]
