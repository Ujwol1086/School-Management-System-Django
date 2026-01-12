from django.urls import path
from . import views

urlpatterns = [
    path('', views.custom_login, name='login'),
    path('login/', views.custom_login, name='login'),
    path('login/<str:role>/', views.custom_login, name='login_role'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('register/', views.register, name='register'),
    
    # Dashboards
    path('teacher-dashboard/', views.teacher_dashboard, name='teacher_dashboard'),
    path('student-dashboard/', views.student_dashboard, name='student_dashboard'),
    
    # Attendance
    path('attendance/', views.mark_attendance, name='mark_attendance'),
    path('attendance/bulk/', views.bulk_attendance, name='bulk_attendance'),
    path('attendance/reports/', views.attendance_reports, name='attendance_reports'),
    path('attendance/per-student/', views.attendance_per_student, name='attendance_per_student'),
    path('attendance/per-student/<int:student_id>/', views.attendance_per_student, name='attendance_per_student_detail'),
    path('attendance/per-course/', views.attendance_per_course, name='attendance_per_course'),
    path('attendance/per-course/<int:course_id>/', views.attendance_per_course, name='attendance_per_course_detail'),
    path('attendance/export/', views.export_attendance, name='export_attendance'),
    
    # Audit Logs
    path('audit-logs/', views.audit_logs, name='audit_logs'),
    
    # Grades
    path('grades/', views.my_grades, name='my_grades'),
    path('grades/manage/', views.manage_grades, name='manage_grades'),
    path('grades/add/', views.add_grade, name='add_grade'),
    
    # Notifications
    path('notifications/', views.notifications, name='notifications'),
    
    # Calendar/Events
    path('calendar/', views.calendar, name='calendar'),
    path('calendar/add-event/', views.add_event, name='add_event'),
    
    # Admin Management
    path('admin/courses/', views.manage_courses, name='manage_courses'),
    path('admin/courses/add/', views.add_course, name='add_course'),
    path('admin/courses/<int:course_id>/edit/', views.edit_course, name='edit_course'),
    path('admin/courses/<int:course_id>/delete/', views.delete_course, name='delete_course'),
    
    path('admin/teachers/', views.manage_teachers, name='manage_teachers'),
    path('admin/teachers/add/', views.add_teacher, name='add_teacher'),
    path('admin/teachers/<int:teacher_id>/edit/', views.edit_teacher, name='edit_teacher'),
    path('admin/teachers/<int:teacher_id>/delete/', views.delete_teacher, name='delete_teacher'),
    
    path('admin/students/', views.manage_students, name='manage_students'),
    path('admin/students/add/', views.add_student, name='add_student'),
    path('admin/students/<int:student_id>/edit/', views.edit_student, name='edit_student'),
    path('admin/students/<int:student_id>/delete/', views.delete_student, name='delete_student'),
]
