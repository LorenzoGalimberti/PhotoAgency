from django.urls import path
from . import views

app_name = 'analyzer'

urlpatterns = [
    path('analyze/<int:pk>/',        views.analyze_store,    name='analyze_store'),
    path('analyze-all/',             views.analyze_all,      name='analyze_all'),
    path('report/<int:pk>/',         views.analysis_report,  name='analysis_report'),
    path('job/<str:job_id>/',        views.job_status,       name='job_status'),
    path('job/<str:job_id>/api/',    views.job_status_api,   name='job_status_api'),
]