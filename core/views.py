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
    CourseForm, TeacherForm, StudentForm, StudyMaterialForm
)
from .models import (
    Course, Attendance, Student, Teacher, AuditLog,
    Grade, Notification, Assignment, Event, StudyMaterial
)
from .decorators import teacher_required, student_required, admin_or_teacher_required, admin_required
from .utils import (
    log_attendance_change, get_client_ip, calculate_attendance_percentage,
    get_course_attendance_stats, filter_attendance
)

# Create your views here.

def custom_login(request, role=None):
    """
    Custom login view with role-based sections (Teacher and Student only)
    """
    # If user is already authenticated, redirect to appropriate dashboard
    if request.user.is_authenticated:
        try:
            Teacher.objects.get(user=request.user)
            return redirect('teacher_dashboard')
        except Teacher.DoesNotExist:
            try:
                Student.objects.get(user=request.user)
                return redirect('student_dashboard')
            except Student.DoesNotExist:
                # If user is admin/staff but admin login is disabled, redirect to login
                return redirect('login')
    
    # Only allow 'teacher' and 'student' roles
    if role and role not in ['teacher', 'student']:
        messages.error(request, 'Invalid login portal. Please select Teacher or Student login.')
        return redirect('login')
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        selected_role = request.POST.get('selected_role', role)
        
        # Validate selected role
        if selected_role not in ['teacher', 'student']:
            messages.error(request, 'Invalid login portal. Please select Teacher or Student login.')
            return redirect('login')
        
        if username and password:
            user = authenticate(request, username=username, password=password)
            if user is not None:
                # Check user's actual role
                is_teacher = Teacher.objects.filter(user=user).exists()
                is_student = Student.objects.filter(user=user).exists()
                
                # Validate selected role matches user's actual role
                if selected_role == 'teacher':
                    if not is_teacher:
                        messages.error(request, 'Invalid credentials. These credentials are not for teacher login. Please use the Teacher login portal.')
                        context = {'selected_role': 'teacher'}
                        return render(request, 'core/login.html', context)
                    # Role validation passed - proceed with login
                    login(request, user)
                    messages.success(request, f'Welcome back, {user.get_full_name() or user.username}!')
                    return redirect('teacher_dashboard')
                    
                elif selected_role == 'student':
                    if not is_student:
                        # Provide more helpful error message
                        if is_teacher:
                            messages.error(request, 'This account is registered as a Teacher. Please use the Teacher login portal.')
                        else:
                            messages.error(request, 'This account is not linked to a student profile. Please contact the administrator.')
                        context = {'selected_role': 'student'}
                        return render(request, 'core/login.html', context)
                    # Role validation passed - proceed with login
                    login(request, user)
                    messages.success(request, f'Welcome back, {user.get_full_name() or user.username}!')
                    return redirect('student_dashboard')
            else:
                messages.error(request, 'Invalid username or password. Please check your credentials and try again.')
                # Keep the selected role in context to show the form again
                context = {'selected_role': selected_role}
                return render(request, 'core/login.html', context)
        else:
            messages.error(request, 'Please provide both username and password.')
            # Keep the selected role in context to show the form again
            context = {'selected_role': selected_role}
            return render(request, 'core/login.html', context)
    
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
    # If user is admin (staff or superuser), show the admin dashboard
    # This takes precedence over Teacher/Student roles to allow Admins to manage the system
    if request.user.is_staff or request.user.is_superuser:
        pass
    else:
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
    return render(request, 'core/admin/dashboard.html', context)

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
        # Explicitly ensure status is set from form data
        # CheckboxInput: checked = True, unchecked = False (not in POST)
        status = form.cleaned_data.get('status', False)
        attendance.status = bool(status)  # Ensure it's a boolean
        attendance.marked_by = request.user
        attendance._current_user = request.user
        attendance.save()
        
        # Log the change
        log_attendance_change(
            'CREATE', attendance, request.user,
            new_status=attendance.status,
            ip_address=get_client_ip(request)
        )
        
        status_text = 'Present' if attendance.status else 'Absent'
        messages.success(request, f'Attendance marked as {status_text} for {attendance.student.name}.')
        return redirect('mark_attendance')
    
    return render(request, 'core/teacher/attendance.html', {
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
    
    return render(request, 'core/teacher/teacher_dashboard.html', context)

@login_required
@teacher_required
def teacher_students_classes(request):
    """
    Students/Classes page - Shows all students and classes for the logged-in teacher.
    """
    teacher = Teacher.objects.get(user=request.user)
    courses = Course.objects.filter(teacher=teacher).select_related('teacher').prefetch_related('students')
    
    # Get all unique students enrolled in teacher's courses
    all_students = Student.objects.filter(course__teacher=teacher).distinct().order_by('roll_no')
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        all_students = all_students.filter(
            Q(name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(roll_no__icontains=search_query)
        )
        courses = courses.filter(
            Q(name__icontains=search_query) |
            Q(code__icontains=search_query)
        )
    
    # Prepare student data with course counts for this teacher
    student_data = []
    for student in all_students:
        # Count courses for this student that belong to this teacher
        student_courses_count = student.course_set.filter(teacher=teacher).count()
        student_data.append({
            'student': student,
            'course_count': student_courses_count,
        })
    
    # Prepare course data with student counts
    course_data = []
    for course in courses:
        student_count = course.students.count()
        course_data.append({
            'course': course,
            'student_count': student_count,
        })
    
    # Pagination for students
    paginator = Paginator(student_data, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'teacher': teacher,
        'courses': courses,
        'course_data': course_data,
        'page_obj': page_obj,
        'search_query': search_query,
        'total_students': len(student_data),
        'total_courses': courses.count(),
    }
    
    return render(request, 'core/teacher/students_classes.html', context)

@login_required
@teacher_required
def teacher_settings_profile(request):
    """
    Settings and Profile page for teachers.
    """
    teacher = Teacher.objects.get(user=request.user)
    
    if request.method == 'POST':
        # Handle profile update
        teacher.name = request.POST.get('name', teacher.name)
        teacher.email = request.POST.get('email', teacher.email)
        teacher.subject = request.POST.get('subject', teacher.subject)
        teacher.save()
        
        # Update user info
        user = request.user
        user.first_name = request.POST.get('first_name', user.first_name)
        user.last_name = request.POST.get('last_name', user.last_name)
        user.email = request.POST.get('user_email', user.email)
        user.save()
        
        messages.success(request, 'Profile updated successfully!')
        return redirect('teacher_settings_profile')
    
    context = {
        'teacher': teacher,
        'user': request.user,
    }
    
    return render(request, 'core/teacher/settings_profile.html', context)

@login_required
@teacher_required
def teacher_exams(request):
    """
    Exams page - Shows all exams (grades and exam events) for teacher's courses.
    """
    teacher = Teacher.objects.get(user=request.user)
    courses = Course.objects.filter(teacher=teacher)
    
    # Get all grades (which can be exams) for teacher's courses
    grades = Grade.objects.filter(course__teacher=teacher).select_related('student', 'course').order_by('-due_date', '-created_at')
    
    # Get exam events
    exam_events = Event.objects.filter(
        course__teacher=teacher,
        event_type='exam'
    ).select_related('course').order_by('-start_date')
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        grades = grades.filter(
            Q(assignment_name__icontains=search_query) |
            Q(student__name__icontains=search_query) |
            Q(course__name__icontains=search_query)
        )
        exam_events = exam_events.filter(
            Q(title__icontains=search_query) |
            Q(course__name__icontains=search_query)
        )
    
    # Filter by course
    course_filter = request.GET.get('course', '')
    if course_filter:
        try:
            selected_course = Course.objects.get(id=course_filter, teacher=teacher)
            grades = grades.filter(course=selected_course)
            exam_events = exam_events.filter(course=selected_course)
        except Course.DoesNotExist:
            pass
    
    # Pagination for grades
    paginator = Paginator(grades, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'teacher': teacher,
        'courses': courses,
        'page_obj': page_obj,
        'exam_events': exam_events,
        'search_query': search_query,
        'course_filter': course_filter,
        'total_grades': grades.count(),
        'total_exam_events': exam_events.count(),
    }
    
    return render(request, 'core/teacher/exams.html', context)

@login_required
@teacher_required
def manage_study_materials(request):
    """
    Manage study materials - List all materials for teacher's courses
    """
    teacher = Teacher.objects.get(user=request.user)
    courses = Course.objects.filter(teacher=teacher)
    
    # Get all study materials for teacher's courses
    materials = StudyMaterial.objects.filter(course__teacher=teacher).select_related('course', 'created_by').order_by('-created_at')
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        materials = materials.filter(
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(course__name__icontains=search_query)
        )
    
    # Filter by course
    course_filter = request.GET.get('course', '')
    if course_filter:
        try:
            selected_course = Course.objects.get(id=course_filter, teacher=teacher)
            materials = materials.filter(course=selected_course)
        except Course.DoesNotExist:
            pass
    
    # Pagination
    paginator = Paginator(materials, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'teacher': teacher,
        'courses': courses,
        'page_obj': page_obj,
        'search_query': search_query,
        'course_filter': course_filter,
        'total_materials': materials.count(),
    }
    
    return render(request, 'core/teacher/manage_study_materials.html', context)

@login_required
@teacher_required
def add_study_material(request):
    """
    Add a new study material
    """
    if request.method == 'POST':
        form = StudyMaterialForm(request.POST, request.FILES, user=request.user)
        
        if form.is_valid():
            material = form.save(commit=False)
            material.created_by = request.user
            material.save()
            messages.success(request, f'Study material "{material.title}" added successfully!')
            return redirect('manage_study_materials')
    else:
        form = StudyMaterialForm(user=request.user)
    
    return render(request, 'core/teacher/add_study_material.html', {'form': form})

@login_required
@teacher_required
def delete_study_material(request, material_id):
    """
    Delete a study material
    """
    material = get_object_or_404(StudyMaterial, id=material_id, course__teacher__user=request.user)
    
    if request.method == 'POST':
        material_title = material.title
        material.delete()
        messages.success(request, f'Study material "{material_title}" deleted successfully!')
        return redirect('manage_study_materials')
    
    return render(request, 'core/teacher/delete_study_material.html', {'material': material})

@login_required
@student_required
def student_study_materials(request):
    """
    View study materials for enrolled courses
    """
    student = Student.objects.get(user=request.user)
    enrolled_courses = student.course_set.all()
    
    # Get all published study materials for enrolled courses
    materials = StudyMaterial.objects.filter(
        course__in=enrolled_courses,
        is_published=True
    ).select_related('course', 'created_by').order_by('-created_at')
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        materials = materials.filter(
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(course__name__icontains=search_query)
        )
    
    # Filter by course
    course_filter = request.GET.get('course', '')
    if course_filter:
        try:
            selected_course = Course.objects.get(id=course_filter, students=student)
            materials = materials.filter(course=selected_course)
        except Course.DoesNotExist:
            pass
    
    # Group materials by course
    materials_by_course = {}
    for material in materials:
        course_name = material.course.name
        if course_name not in materials_by_course:
            materials_by_course[course_name] = []
        materials_by_course[course_name].append(material)
    
    # Pagination
    paginator = Paginator(materials, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'student': student,
        'enrolled_courses': enrolled_courses,
        'page_obj': page_obj,
        'materials_by_course': materials_by_course,
        'search_query': search_query,
        'course_filter': course_filter,
        'total_materials': materials.count(),
    }
    
    return render(request, 'core/student/study_materials.html', context)

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
        
        # Get study materials for this course
        study_materials = StudyMaterial.objects.filter(
            course=course,
            is_published=True
        ).order_by('-created_at')[:3]  # Get 3 most recent materials
        
        study_materials_count = StudyMaterial.objects.filter(
            course=course,
            is_published=True
        ).count()
        
        course_data.append({
            'course': course,
            'teacher_name': course.teacher.name,
            'present_count': present_count,
            'absent_count': absent_count,
            'total_records': total_records,
            'attendance_percentage': attendance_percentage,
            'recent_attendance': recent_attendance,
            'study_materials': study_materials,
            'study_materials_count': study_materials_count,
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
    
    # Get total study materials count
    total_study_materials = StudyMaterial.objects.filter(
        course__in=courses,
        is_published=True
    ).count()
    
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
        'total_study_materials': total_study_materials,
    }
    
    return render(request, 'core/student/student_dashboard.html', context)

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
    attendance_date = None
    existing_attendance = {}  # Dictionary to store existing attendance status
    present_student_ids = set()  # Set of student IDs marked as present
    form = BulkAttendanceForm(user=request.user, data=request.GET if request.method == 'GET' else None)
    
    # Handle GET requests (form submission to load students)
    if request.method == 'GET' and form.is_valid():
        selected_course = form.cleaned_data.get('course')
        attendance_date = form.cleaned_data.get('date')
    elif course_id_from_url:
        # If course ID provided in URL, pre-select it
        try:
            selected_course = Course.objects.get(id=course_id_from_url)
            if is_teacher and selected_course.teacher != teacher:
                selected_course = None
            else:
                # Pre-select course in form
                form.fields['course'].initial = selected_course.id
                # Try to get date from GET parameters
                date_str = request.GET.get('date')
                if date_str:
                    try:
                        from datetime import datetime
                        attendance_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    except:
                        pass
        except Course.DoesNotExist:
            pass
    
    # If we have course and date, fetch students and existing attendance
    if selected_course and attendance_date:
        # Get students list
        students = selected_course.students.all().order_by('roll_no')
        
        # Fetch existing attendance records for this date
        existing_records = Attendance.objects.filter(
            course=selected_course,
            date=attendance_date
        ).select_related('student')
        
        for record in existing_records:
            existing_attendance[record.student.id] = record.status
            if record.status:
                present_student_ids.add(record.student.id)
    
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
            return redirect('teacher_dashboard')
    
    return render(request, 'core/teacher/bulk_attendance.html', {
        'form': form,
        'courses': courses,
        'students': students,
        'selected_course': selected_course,
        'attendance_date': attendance_date,
        'existing_attendance': existing_attendance,
        'present_student_ids': present_student_ids,
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
    
    return render(request, 'core/teacher/attendance_reports.html', context)

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
            return render(request, 'core/teacher/attendance_per_student_select.html', {'students': students})
    
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
    
    return render(request, 'core/teacher/attendance_per_student.html', context)

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
            return render(request, 'core/teacher/attendance_per_course_select.html', {'courses': courses})
    
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
    
    return render(request, 'core/teacher/attendance_per_course.html', context)

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
    
    return render(request, 'core/admin/audit_logs.html', context)

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
    
    return render(request, 'core/student/my_grades.html', context)

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
    
    return render(request, 'core/teacher/manage_grades.html', context)

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
    
    return render(request, 'core/teacher/add_grade.html', {'form': form})

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
    
    # Shared calendar view - check if student or teacher/admin  
    try:
        Student.objects.get(user=request.user)
        return render(request, 'core/student/calendar.html', context)
    except Student.DoesNotExist:
        if request.user.is_staff or request.user.is_superuser:
            return render(request, 'core/admin/calendar.html', context)
        return render(request, 'core/teacher/calendar.html', context)

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
    
    return render(request, 'core/teacher/add_event.html', {'form': form})

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
