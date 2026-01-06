from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('teacher-dashboard/', views.teacher_dashboard, name='teacher_dashboard'),
    path('attendance/', views.mark_attendance, name='mark_attendance'),
    path('attendance/bulk/', views.bulk_attendance, name='bulk_attendance'),
]
