from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, authenticate
from django.http import Http404, HttpResponse, JsonResponse, HttpResponseForbidden
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Avg, Sum
from django.contrib import messages
from django.utils import timezone
from django.core.paginator import Paginator
from django.db import transaction
from django.views.decorators.http import require_http_methods
import csv
import json
from datetime import date, datetime, timedelta

from .forms import (
    AttendanceForm, UserRegistrationForm, AttendanceFilterForm,
    BulkAttendanceForm, GradeForm, AssignmentForm, EventForm,
    CourseForm, TeacherForm, StudentForm
)
from .models import (
    Course, Attendance, Student, Teacher, AuditLog,
    Grade, Notification, Assignment, Event
)
from .decorators import teacher_required, student_required, admin_or_teacher_required, admin_required
from .utils import (
    log_attendance_change, get_client_ip, calculate_attendance_percentage,
    get_course_attendance_stats, filter_attendance
)

# Create your views here.

def custom_login(request, role=None):
    """
    Custom login view with role-based sections
    """
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        
        if username and password:
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                
                # Redirect based on role
                try:
                    Teacher.objects.get(user=user)
                    return redirect('teacher_dashboard')
                except Teacher.DoesNotExist:
                    pass
                
                try:
                    Student.objects.get(user=user)
                    return redirect('student_dashboard')
                except Student.DoesNotExist:
                    pass
                
                # Admin or regular user
                return redirect('dashboard')
            else:
                messages.error(request, 'Invalid username or password.')
        else:
            messages.error(request, 'Please provide both username and password.')
    
    context = {
        'selected_role': role,
    }
    return render(request, 'core/login.html', context)

def register(request):
    """
    User registration view.
    Creates a new user account and automatically logs them in.
    """
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Automatically log in the user after registration
            login(request, user)
            messages.success(request, f'Welcome, {user.username}! Your account has been created successfully.')
            return redirect('dashboard')
    else:
        form = UserRegistrationForm()
    
    return render(request, 'core/register.html', {'form': form})

@login_required
def dashboard(request):
    """
    Main dashboard - redirects teachers to teacher_dashboard, students to student_dashboard, others see general dashboard.
    """
    # Check if user is a teacher and redirect to teacher dashboard
    try:
        Teacher.objects.get(user=request.user)
        return redirect('teacher_dashboard')
    except Teacher.DoesNotExist:
        pass  # Not a teacher, check if student
    
    # Check if user is a student and redirect to student dashboard
    try:
        Student.objects.get(user=request.user)
        return redirect('student_dashboard')
    except Student.DoesNotExist:
        pass  # Not a student, show general dashboard
    
    # Get statistics for the general dashboard (Admin view)
    total_students = Student.objects.count()
    total_teachers = Teacher.objects.count()
    total_courses = Course.objects.count()
    today_attendance = Attendance.objects.filter(date=date.today()).count()
    recent_attendance = Attendance.objects.select_related('student', 'course').order_by('-created_at')[:10]
    
    # Get unread notifications
    unread_notifications = Notification.objects.filter(user=request.user, is_read=False).count()
    
    context = {
        'total_students': total_students,
        'total_teachers': total_teachers,
        'total_courses': total_courses,
        'today_attendance': today_attendance,
        'recent_attendance': recent_attendance,
        'unread_notifications': unread_notifications,
    }
    return render(request, 'core/dashboard.html', context)

@login_required
@teacher_required
def mark_attendance(request):
    """Mark individual attendance"""
    form = AttendanceForm(request.POST or None)
    
    # Filter courses for teacher
    try:
        teacher = Teacher.objects.get(user=request.user)
        form.fields['course'].queryset = Course.objects.filter(teacher=teacher)
    except Teacher.DoesNotExist:
        pass
    
    if form.is_valid():
        attendance = form.save(commit=False)
        attendance.marked_by = request.user
        attendance._current_user = request.user
        attendance.save()
        
        # Log the change
        log_attendance_change(
            'CREATE', attendance, request.user,
            new_status=attendance.status,
            ip_address=get_client_ip(request)
        )
        
        messages.success(request, f'Attendance marked successfully for {attendance.student.name}.')
        return redirect('mark_attendance')
    
    return render(request, 'core/attendance.html', {
        'form': form,
        'today': date.today()
    })

@login_required
@teacher_required
def teacher_dashboard(request):
    """
    Teacher Dashboard - Shows courses assigned to the logged-in teacher.
    Only accessible by users who have a Teacher profile linked to their account.
    """
    teacher = Teacher.objects.get(user=request.user)
    courses = Course.objects.filter(teacher=teacher).select_related('teacher').prefetch_related('students')
    
    # Prepare course data with statistics
    course_data = []
    for course in courses:
        stats = get_course_attendance_stats(course)
        course_data.append({
            'course': course,
            **stats
        })
    
    # Get unread notifications
    unread_notifications = Notification.objects.filter(user=request.user, is_read=False).count()
    
    context = {
        'teacher': teacher,
        'course_data': course_data,
        'total_courses': courses.count(),
        'unread_notifications': unread_notifications,
    }
    
    return render(request, 'core/teacher_dashboard.html', context)

@login_required
@student_required
def student_dashboard(request):
    """
    Student Dashboard - Shows courses enrolled and attendance records for the logged-in student.
    Only accessible by users who have a Student profile linked to their account.
    """
    student = Student.objects.get(user=request.user)
    courses = Course.objects.filter(students=student).select_related('teacher').prefetch_related('students')
    
    # Prepare course data with attendance statistics
    course_data = []
    total_present = 0
    total_absent = 0
    total_classes = 0
    
    for course in courses:
        attendance_records = Attendance.objects.filter(
            student=student,
            course=course
        ).order_by('-date')
        
        present_count = attendance_records.filter(status=True).count()
        absent_count = attendance_records.filter(status=False).count()
        total_records = attendance_records.count()
        
        attendance_percentage = calculate_attendance_percentage(student, course)
        recent_attendance = attendance_records[:5]
        
        course_data.append({
            'course': course,
            'teacher_name': course.teacher.name,
            'present_count': present_count,
            'absent_count': absent_count,
            'total_records': total_records,
            'attendance_percentage': attendance_percentage,
            'recent_attendance': recent_attendance,
        })
        
        total_present += present_count
        total_absent += absent_count
        total_classes += total_records
    
    overall_percentage = 0
    if total_classes > 0:
        overall_percentage = round((total_present / total_classes) * 100, 1)
    
    # Get unread notifications
    unread_notifications = Notification.objects.filter(user=request.user, is_read=False).count()
    
    # Get recent grades
    recent_grades = Grade.objects.filter(student=student).order_by('-created_at')[:5]
    
    context = {
        'student': student,
        'course_data': course_data,
        'total_courses': courses.count(),
        'total_present': total_present,
        'total_absent': total_absent,
        'total_classes': total_classes,
        'overall_percentage': overall_percentage,
        'unread_notifications': unread_notifications,
        'recent_grades': recent_grades,
    }
    
    return render(request, 'core/student_dashboard.html', context)

@login_required
@admin_or_teacher_required
def bulk_attendance(request):
    """
    Bulk attendance marking page.
    Supports pre-selecting a course via ?course=ID query parameter (from teacher dashboard).
    SECURITY: Teachers can only see and mark attendance for their own courses.
    """
    # SECURITY: Filter courses based on user role
    try:
        teacher = Teacher.objects.get(user=request.user)
        courses = Course.objects.filter(teacher=teacher)
        is_teacher = True
    except Teacher.DoesNotExist:
        courses = Course.objects.all()
        is_teacher = False
        teacher = None
    
    # Get course from query parameter if provided
    course_id_from_url = request.GET.get('course')
    students = None
    selected_course = None
    form = BulkAttendanceForm(user=request.user)
    
    # If course ID provided in URL, pre-select it
    if course_id_from_url:
        try:
            selected_course = Course.objects.get(id=course_id_from_url)
            if is_teacher and selected_course.teacher != teacher:
                selected_course = None
            else:
                students = selected_course.students.all().order_by('roll_no')
        except Course.DoesNotExist:
            pass
    
    if request.method == "POST":
        form = BulkAttendanceForm(request.POST, user=request.user)
        
        if form.is_valid():
            course = form.cleaned_data['course']
            attendance_date = form.cleaned_data['date']
            
            # SECURITY: Verify teacher can access this course
            if is_teacher and course.teacher != teacher:
                messages.error(request, "You don't have permission to mark attendance for this course.")
                return redirect('bulk_attendance')
            
            students = course.students.all().order_by('roll_no')
            marked_count = 0
            
            with transaction.atomic():
                for student in students:
                    status = request.POST.get(f"status_{student.id}") == "on"
                    
                    attendance, created = Attendance.objects.update_or_create(
                        student=student,
                        course=course,
                        date=attendance_date,
                        defaults={
                            'status': status,
                            'marked_by': request.user
                        }
                    )
                    
                    # Log the change
                    action = 'CREATE' if created else 'UPDATE'
                    old_status = None if created else Attendance.objects.filter(
                        student=student, course=course, date=attendance_date
                    ).exclude(pk=attendance.pk).first()
                    old_status = old_status.status if old_status else None
                    
                    log_attendance_change(
                        action, attendance, request.user,
                        old_status=old_status,
                        new_status=status,
                        ip_address=get_client_ip(request)
                    )
                    
                    marked_count += 1
            
            messages.success(request, f'Attendance marked for {marked_count} students successfully.')
            return redirect('bulk_attendance')
    
    return render(request, 'core/bulk_attendance.html', {
        'form': form,
        'courses': courses,
        'students': students,
        'selected_course': selected_course,
        'today': date.today()
    })

@login_required
@admin_or_teacher_required
def attendance_reports(request):
    """Attendance reports with filtering"""
    form = AttendanceFilterForm(request.GET or None)
    
    # Filter courses based on user role
    try:
        teacher = Teacher.objects.get(user=request.user)
        form.fields['course'].queryset = Course.objects.filter(teacher=teacher)
        form.fields['student'].queryset = Student.objects.filter(
            course__teacher=teacher
        ).distinct()
    except Teacher.DoesNotExist:
        pass
    
    # Get all attendance records
    attendance_list = Attendance.objects.select_related('student', 'course', 'marked_by').order_by('-date')
    
    # Apply filters
    if form.is_valid():
        attendance_list = filter_attendance(
            attendance_list,
            course_id=form.cleaned_data.get('course'),
            student_id=form.cleaned_data.get('student'),
            date_from=form.cleaned_data.get('date_from'),
            date_to=form.cleaned_data.get('date_to'),
            status=form.cleaned_data.get('status')
        )
    
    # Pagination
    paginator = Paginator(attendance_list, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'form': form,
        'page_obj': page_obj,
        'total_records': attendance_list.count(),
    }
    
    return render(request, 'core/attendance_reports.html', context)

@login_required
@admin_or_teacher_required
def attendance_per_student(request, student_id=None):
    """Attendance report per student"""
    if student_id:
        student = get_object_or_404(Student, id=student_id)
    else:
        # Get student from query parameter or show form
        student_id = request.GET.get('student')
        if student_id:
            student = get_object_or_404(Student, id=student_id)
        else:
            # Show student selection form
            students = Student.objects.all()
            if not request.user.is_staff and not request.user.is_superuser:
                try:
                    teacher = Teacher.objects.get(user=request.user)
                    students = Student.objects.filter(course__teacher=teacher).distinct()
                except Teacher.DoesNotExist:
                    pass
            return render(request, 'core/attendance_per_student_select.html', {'students': students})
    
    # Get all courses for this student
    courses = Course.objects.filter(students=student)
    
    # Calculate attendance for each course
    course_stats = []
    for course in courses:
        percentage = calculate_attendance_percentage(student, course)
        total_records = Attendance.objects.filter(student=student, course=course).count()
        present_count = Attendance.objects.filter(student=student, course=course, status=True).count()
        absent_count = total_records - present_count
        
        course_stats.append({
            'course': course,
            'percentage': percentage,
            'total_records': total_records,
            'present_count': present_count,
            'absent_count': absent_count,
        })
    
    # Overall statistics
    all_attendance = Attendance.objects.filter(student=student)
    total_all = all_attendance.count()
    present_all = all_attendance.filter(status=True).count()
    overall_percentage = round((present_all / total_all * 100), 2) if total_all > 0 else 0
    
    context = {
        'student': student,
        'course_stats': course_stats,
        'total_all': total_all,
        'present_all': present_all,
        'overall_percentage': overall_percentage,
    }
    
    return render(request, 'core/attendance_per_student.html', context)

@login_required
@admin_or_teacher_required
def attendance_per_course(request, course_id=None):
    """Attendance report per course"""
    if course_id:
        course = get_object_or_404(Course, id=course_id)
    else:
        course_id = request.GET.get('course')
        if course_id:
            course = get_object_or_404(Course, id=course_id)
        else:
            # Show course selection form
            courses = Course.objects.all()
            if not request.user.is_staff and not request.user.is_superuser:
                try:
                    teacher = Teacher.objects.get(user=request.user)
                    courses = Course.objects.filter(teacher=teacher)
                except Teacher.DoesNotExist:
                    pass
            return render(request, 'core/attendance_per_course_select.html', {'courses': courses})
    
    # Security check for teachers
    if not request.user.is_staff and not request.user.is_superuser:
        try:
            teacher = Teacher.objects.get(user=request.user)
            if course.teacher != teacher:
                raise PermissionDenied("You don't have permission to view this course.")
        except Teacher.DoesNotExist:
            pass
    
    stats = get_course_attendance_stats(course)
    
    # Get attendance by student
    students = course.students.all().order_by('roll_no')
    student_stats = []
    for student in students:
        percentage = calculate_attendance_percentage(student, course)
        total_records = Attendance.objects.filter(student=student, course=course).count()
        present_count = Attendance.objects.filter(student=student, course=course, status=True).count()
        absent_count = total_records - present_count
        
        student_stats.append({
            'student': student,
            'percentage': percentage,
            'total_records': total_records,
            'present_count': present_count,
            'absent_count': absent_count,
        })
    
    context = {
        'course': course,
        'stats': stats,
        'student_stats': student_stats,
    }
    
    return render(request, 'core/attendance_per_course.html', context)

@login_required
@admin_or_teacher_required
def export_attendance(request):
    """Export attendance data to CSV/Excel"""
    format_type = request.GET.get('format', 'csv')
    
    # Get filtered attendance
    form = AttendanceFilterForm(request.GET or None)
    attendance_list = Attendance.objects.select_related('student', 'course', 'marked_by').order_by('-date')
    
    # Filter courses based on user role
    try:
        teacher = Teacher.objects.get(user=request.user)
        form.fields['course'].queryset = Course.objects.filter(teacher=teacher)
        form.fields['student'].queryset = Student.objects.filter(
            course__teacher=teacher
        ).distinct()
    except Teacher.DoesNotExist:
        pass
    
    if form.is_valid():
        attendance_list = filter_attendance(
            attendance_list,
            course_id=form.cleaned_data.get('course'),
            student_id=form.cleaned_data.get('student'),
            date_from=form.cleaned_data.get('date_from'),
            date_to=form.cleaned_data.get('date_to'),
            status=form.cleaned_data.get('status')
        )
    
    if format_type == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="attendance_{date.today()}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Date', 'Student Name', 'Roll No', 'Course', 'Status', 'Marked By'])
        
        for attendance in attendance_list:
            writer.writerow([
                attendance.date,
                attendance.student.name,
                attendance.student.roll_no,
                attendance.course.name,
                'Present' if attendance.status else 'Absent',
                attendance.marked_by.username if attendance.marked_by else 'N/A'
            ])
        
        return response
    
    elif format_type == 'json':
        data = []
        for attendance in attendance_list:
            data.append({
                'date': str(attendance.date),
                'student_name': attendance.student.name,
                'roll_no': attendance.student.roll_no,
                'course': attendance.course.name,
                'status': 'Present' if attendance.status else 'Absent',
                'marked_by': attendance.marked_by.username if attendance.marked_by else 'N/A'
            })
        
        response = HttpResponse(json.dumps(data, indent=2), content_type='application/json')
        response['Content-Disposition'] = f'attachment; filename="attendance_{date.today()}.json"'
        return response
    
    return redirect('attendance_reports')

@login_required
@admin_or_teacher_required
def audit_logs(request):
    """View audit logs for attendance changes"""
    logs = AuditLog.objects.select_related('user', 'student', 'course', 'attendance').order_by('-timestamp')
    
    # Filter by course if teacher
    if not request.user.is_staff and not request.user.is_superuser:
        try:
            teacher = Teacher.objects.get(user=request.user)
            logs = logs.filter(course__teacher=teacher)
        except Teacher.DoesNotExist:
            pass
    
    # Apply filters
    course_id = request.GET.get('course')
    student_id = request.GET.get('student')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    
    if course_id:
        logs = logs.filter(course_id=course_id)
    if student_id:
        logs = logs.filter(student_id=student_id)
    if date_from:
        logs = logs.filter(timestamp__date__gte=date_from)
    if date_to:
        logs = logs.filter(timestamp__date__lte=date_to)
    
    # Pagination
    paginator = Paginator(logs, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'total_logs': logs.count(),
    }
    
    return render(request, 'core/audit_logs.html', context)

@login_required
@student_required
def my_grades(request):
    """Student view of their grades"""
    student = Student.objects.get(user=request.user)
    grades = Grade.objects.filter(student=student).select_related('course', 'created_by').order_by('-created_at')
    
    # Group by course
    courses_with_grades = {}
    for grade in grades:
        course_name = grade.course.name
        if course_name not in courses_with_grades:
            courses_with_grades[course_name] = {
                'course': grade.course,
                'grades': [],
                'average': 0,
            }
        courses_with_grades[course_name]['grades'].append(grade)
    
    # Calculate averages
    for course_data in courses_with_grades.values():
        if course_data['grades']:
            total_score = sum(g.score for g in course_data['grades'])
            total_max = sum(g.max_score for g in course_data['grades'])
            if total_max > 0:
                course_data['average'] = round((total_score / total_max) * 100, 2)
    
    context = {
        'student': student,
        'courses_with_grades': courses_with_grades,
    }
    
    return render(request, 'core/my_grades.html', context)

@login_required
@admin_or_teacher_required
def manage_grades(request):
    """Teacher/Admin view to manage grades"""
    grades = Grade.objects.select_related('student', 'course', 'created_by').order_by('-created_at')
    
    # Filter by course if teacher
    if not request.user.is_staff and not request.user.is_superuser:
        try:
            teacher = Teacher.objects.get(user=request.user)
            grades = grades.filter(course__teacher=teacher)
        except Teacher.DoesNotExist:
            pass
    
    # Apply filters
    course_id = request.GET.get('course')
    student_id = request.GET.get('student')
    
    if course_id:
        grades = grades.filter(course_id=course_id)
    if student_id:
        grades = grades.filter(student_id=student_id)
    
    # Pagination
    paginator = Paginator(grades, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
    }
    
    return render(request, 'core/manage_grades.html', context)

@login_required
@admin_or_teacher_required
def add_grade(request):
    """Add a new grade"""
    if request.method == 'POST':
        form = GradeForm(request.POST)
        
        # Filter courses for teacher
        try:
            teacher = Teacher.objects.get(user=request.user)
            form.fields['course'].queryset = Course.objects.filter(teacher=teacher)
            form.fields['student'].queryset = Student.objects.filter(
                course__teacher=teacher
            ).distinct()
        except Teacher.DoesNotExist:
            pass
        
        if form.is_valid():
            grade = form.save(commit=False)
            grade.created_by = request.user
            grade.save()
            messages.success(request, 'Grade added successfully.')
            return redirect('manage_grades')
    else:
        form = GradeForm()
        
        # Filter courses for teacher
        try:
            teacher = Teacher.objects.get(user=request.user)
            form.fields['course'].queryset = Course.objects.filter(teacher=teacher)
            form.fields['student'].queryset = Student.objects.filter(
                course__teacher=teacher
            ).distinct()
        except Teacher.DoesNotExist:
            pass
    
    return render(request, 'core/add_grade.html', {'form': form})

@login_required
def notifications(request):
    """View notifications"""
    notifications_list = Notification.objects.filter(user=request.user).order_by('-created_at')
    
    # Mark as read if requested
    if request.GET.get('mark_read'):
        notification_id = request.GET.get('mark_read')
        try:
            notification = Notification.objects.get(id=notification_id, user=request.user)
            notification.is_read = True
            notification.save()
            messages.success(request, 'Notification marked as read.')
        except Notification.DoesNotExist:
            pass
    
    # Mark all as read
    if request.method == 'POST' and 'mark_all_read' in request.POST:
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        messages.success(request, 'All notifications marked as read.')
        return redirect('notifications')
    
    # Pagination
    paginator = Paginator(notifications_list, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'unread_count': Notification.objects.filter(user=request.user, is_read=False).count(),
    }
    
    return render(request, 'core/notifications.html', context)

@login_required
def calendar(request):
    """Calendar view for events"""
    events = Event.objects.all().order_by('start_date')
    
    # Filter by course if teacher
    if not request.user.is_staff and not request.user.is_superuser:
        try:
            teacher = Teacher.objects.get(user=request.user)
            events = events.filter(course__teacher=teacher)
        except Teacher.DoesNotExist:
            try:
                student = Student.objects.get(user=request.user)
                events = events.filter(course__students=student)
            except Student.DoesNotExist:
                events = Event.objects.none()
    
    # Filter by date range
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    
    if date_from:
        events = events.filter(start_date__date__gte=date_from)
    if date_to:
        events = events.filter(start_date__date__lte=date_to)
    
    context = {
        'events': events,
    }
    
    return render(request, 'core/calendar.html', context)

@login_required
@admin_or_teacher_required
def add_event(request):
    """Add a new event"""
    if request.method == 'POST':
        form = EventForm(request.POST)
        
        # Filter courses for teacher
        try:
            teacher = Teacher.objects.get(user=request.user)
            form.fields['course'].queryset = Course.objects.filter(teacher=teacher)
        except Teacher.DoesNotExist:
            pass
        
        if form.is_valid():
            event = form.save(commit=False)
            event.created_by = request.user
            event.save()
            messages.success(request, 'Event added successfully.')
            return redirect('calendar')
    else:
        form = EventForm()
        
        # Filter courses for teacher
        try:
            teacher = Teacher.objects.get(user=request.user)
            form.fields['course'].queryset = Course.objects.filter(teacher=teacher)
        except Teacher.DoesNotExist:
            pass
    
    return render(request, 'core/add_event.html', {'form': form})

# Admin Management Views
@login_required
@admin_required
def manage_courses(request):
    """List all courses"""
    courses = Course.objects.select_related('teacher').prefetch_related('students').all().order_by('name')
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        courses = courses.filter(
            Q(name__icontains=search_query) |
            Q(code__icontains=search_query) |
            Q(teacher__name__icontains=search_query)
        )
    
    # Pagination
    paginator = Paginator(courses, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'total_courses': courses.count(),
    }
    return render(request, 'core/admin/manage_courses.html', context)

@login_required
@admin_required
def add_course(request):
    """Add a new course"""
    if request.method == 'POST':
        form = CourseForm(request.POST)
        if form.is_valid():
            course = form.save()
            messages.success(request, f'Course "{course.name}" has been created successfully.')
            return redirect('manage_courses')
    else:
        form = CourseForm()
    
    return render(request, 'core/admin/course_form.html', {
        'form': form,
        'title': 'Add Course',
        'action': 'Add'
    })

@login_required
@admin_required
def edit_course(request, course_id):
    """Edit an existing course"""
    course = get_object_or_404(Course, id=course_id)
    
    if request.method == 'POST':
        form = CourseForm(request.POST, instance=course)
        if form.is_valid():
            course = form.save()
            messages.success(request, f'Course "{course.name}" has been updated successfully.')
            return redirect('manage_courses')
    else:
        form = CourseForm(instance=course)
    
    return render(request, 'core/admin/course_form.html', {
        'form': form,
        'course': course,
        'title': 'Edit Course',
        'action': 'Update'
    })

@login_required
@admin_required
def delete_course(request, course_id):
    """Delete a course"""
    course = get_object_or_404(Course, id=course_id)
    
    if request.method == 'POST':
        course_name = course.name
        course.delete()
        messages.success(request, f'Course "{course_name}" has been deleted successfully.')
        return redirect('manage_courses')
    
    return render(request, 'core/admin/delete_course.html', {'course': course})

@login_required
@admin_required
def manage_teachers(request):
    """List all teachers"""
    teachers = Teacher.objects.select_related('user').all().order_by('name')
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        teachers = teachers.filter(
            Q(name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(subject__icontains=search_query)
        )
    
    # Pagination
    paginator = Paginator(teachers, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'total_teachers': teachers.count(),
    }
    return render(request, 'core/admin/manage_teachers.html', context)

@login_required
@admin_required
def add_teacher(request):
    """Add a new teacher"""
    if request.method == 'POST':
        form = TeacherForm(request.POST)
        if form.is_valid():
            teacher = form.save(commit=False)
            
            # Create user if requested
            create_user = form.cleaned_data.get('create_user', False)
            if create_user:
                username = form.cleaned_data.get('username', '').strip()
                password = form.cleaned_data.get('password', '').strip()
                
                if username and password:
                    user = User.objects.create_user(
                        username=username,
                        email=teacher.email,
                        password=password,
                        is_active=True,
                        is_staff=False,
                        is_superuser=False,
                    )
                    teacher.user = user
            
            teacher.save()
            messages.success(request, f'Teacher "{teacher.name}" has been created successfully.')
            return redirect('manage_teachers')
    else:
        form = TeacherForm()
    
    return render(request, 'core/admin/teacher_form.html', {
        'form': form,
        'title': 'Add Teacher',
        'action': 'Add'
    })

@login_required
@admin_required
def edit_teacher(request, teacher_id):
    """Edit an existing teacher"""
    teacher = get_object_or_404(Teacher, id=teacher_id)
    
    if request.method == 'POST':
        form = TeacherForm(request.POST, instance=teacher)
        if form.is_valid():
            teacher = form.save(commit=False)
            
            # Create user if requested and doesn't exist
            create_user = form.cleaned_data.get('create_user', False)
            if create_user and not teacher.user:
                username = form.cleaned_data.get('username', '').strip()
                password = form.cleaned_data.get('password', '').strip()
                
                if username and password:
                    user = User.objects.create_user(
                        username=username,
                        email=teacher.email,
                        password=password,
                        is_active=True,
                        is_staff=False,
                        is_superuser=False,
                    )
                    teacher.user = user
            
            teacher.save()
            messages.success(request, f'Teacher "{teacher.name}" has been updated successfully.')
            return redirect('manage_teachers')
    else:
        form = TeacherForm(instance=teacher)
        # Pre-fill username if user exists
        if teacher.user:
            form.fields['username'].initial = teacher.user.username
    
    return render(request, 'core/admin/teacher_form.html', {
        'form': form,
        'teacher': teacher,
        'title': 'Edit Teacher',
        'action': 'Update'
    })

@login_required
@admin_required
def delete_teacher(request, teacher_id):
    """Delete a teacher"""
    teacher = get_object_or_404(Teacher, id=teacher_id)
    
    if request.method == 'POST':
        teacher_name = teacher.name
        teacher.delete()
        messages.success(request, f'Teacher "{teacher_name}" has been deleted successfully.')
        return redirect('manage_teachers')
    
    return render(request, 'core/admin/delete_teacher.html', {'teacher': teacher})

@login_required
@admin_required
def manage_students(request):
    """List all students"""
    students = Student.objects.select_related('user').all().order_by('roll_no')
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        students = students.filter(
            Q(name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(roll_no__icontains=search_query)
        )
    
    # Pagination
    paginator = Paginator(students, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'total_students': students.count(),
    }
    return render(request, 'core/admin/manage_students.html', context)

@login_required
@admin_required
def add_student(request):
    """Add a new student"""
    if request.method == 'POST':
        form = StudentForm(request.POST)
        if form.is_valid():
            student = form.save(commit=False)
            
            # Create user if requested
            create_user = form.cleaned_data.get('create_user', False)
            if create_user:
                username = form.cleaned_data.get('username', '').strip()
                password = form.cleaned_data.get('password', '').strip()
                
                if username and password:
                    user = User.objects.create_user(
                        username=username,
                        email=student.email,
                        password=password,
                        is_active=True,
                        is_staff=False,
                        is_superuser=False,
                    )
                    student.user = user
            
            student.save()
            messages.success(request, f'Student "{student.name}" has been created successfully.')
            return redirect('manage_students')
    else:
        form = StudentForm()
    
    return render(request, 'core/admin/student_form.html', {
        'form': form,
        'title': 'Add Student',
        'action': 'Add'
    })

@login_required
@admin_required
def edit_student(request, student_id):
    """Edit an existing student"""
    student = get_object_or_404(Student, id=student_id)
    
    if request.method == 'POST':
        form = StudentForm(request.POST, instance=student)
        if form.is_valid():
            student = form.save(commit=False)
            
            # Create user if requested and doesn't exist
            create_user = form.cleaned_data.get('create_user', False)
            if create_user and not student.user:
                username = form.cleaned_data.get('username', '').strip()
                password = form.cleaned_data.get('password', '').strip()
                
                if username and password:
                    user = User.objects.create_user(
                        username=username,
                        email=student.email,
                        password=password,
                        is_active=True,
                        is_staff=False,
                        is_superuser=False,
                    )
                    student.user = user
            
            student.save()
            messages.success(request, f'Student "{student.name}" has been updated successfully.')
            return redirect('manage_students')
    else:
        form = StudentForm(instance=student)
        # Pre-fill username if user exists
        if student.user:
            form.fields['username'].initial = student.user.username
    
    return render(request, 'core/admin/student_form.html', {
        'form': form,
        'student': student,
        'title': 'Edit Student',
        'action': 'Update'
    })

@login_required
@admin_required
def delete_student(request, student_id):
    """Delete a student"""
    student = get_object_or_404(Student, id=student_id)
    
    if request.method == 'POST':
        student_name = student.name
        student.delete()
        messages.success(request, f'Student "{student_name}" has been deleted successfully.')
        return redirect('manage_students')
    
    return render(request, 'core/admin/delete_student.html', {'student': student})

# Error handlers
def handler403(request, exception):
    return render(request, '403.html', status=403)

def handler404(request, exception):
    return render(request, '404.html', status=404)

def handler500(request):
    return render(request, '500.html', status=500)
