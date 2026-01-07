from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.http import Http404
from django.db.models import Count, Q
from django.contrib import messages
from .forms import AttendanceForm, UserRegistrationForm
from .models import Course, Attendance, Student, Teacher
from datetime import date

# Create your views here.

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
    
    # Get statistics for the general dashboard
    total_students = Student.objects.count()
    total_teachers = Teacher.objects.count()
    total_courses = Course.objects.count()
    today_attendance = Attendance.objects.filter(date=date.today()).count()
    
    context = {
        'total_students': total_students,
        'total_teachers': total_teachers,
        'total_courses': total_courses,
        'today_attendance': today_attendance,
    }
    return render(request, 'core/dashboard.html', context)

@login_required
def mark_attendance(request):
    form = AttendanceForm(request.POST or None)
    if form.is_valid():
        form.save()
    return render(request, 'core/attendance.html', {'form': form})

@login_required
def teacher_dashboard(request):
    """
    Teacher Dashboard - Shows courses assigned to the logged-in teacher.
    Only accessible by users who have a Teacher profile linked to their account.
    """
    # Check if user has a Teacher profile
    try:
        teacher = Teacher.objects.get(user=request.user)
    except Teacher.DoesNotExist:
        # User is not a teacher - deny access
        raise Http404("You must be a teacher to access this page.")
    
    # Get all courses assigned to this teacher
    courses = Course.objects.filter(teacher=teacher).select_related('teacher').prefetch_related('students')
    
    # Prepare course data with statistics
    course_data = []
    for course in courses:
        # Count total number of unique dates (classes conducted)
        total_classes = Attendance.objects.filter(course=course).values('date').distinct().count()
        
        # Count total attendance records for this course
        total_attendance_records = Attendance.objects.filter(course=course).count()
        
        # Count present records
        present_count = Attendance.objects.filter(course=course, status=True).count()
        
        # Count absent records
        absent_count = Attendance.objects.filter(course=course, status=False).count()
        
        # Count enrolled students
        enrolled_students = course.students.count()
        
        course_data.append({
            'course': course,
            'total_classes': total_classes,
            'total_attendance_records': total_attendance_records,
            'present_count': present_count,
            'absent_count': absent_count,
            'enrolled_students': enrolled_students,
        })
    
    context = {
        'teacher': teacher,
        'course_data': course_data,
        'total_courses': courses.count(),
    }
    
    return render(request, 'core/teacher_dashboard.html', context)

@login_required
def student_dashboard(request):
    """
    Student Dashboard - Shows courses enrolled and attendance records for the logged-in student.
    Only accessible by users who have a Student profile linked to their account.
    """
    # Check if user has a Student profile
    try:
        student = Student.objects.get(user=request.user)
    except Student.DoesNotExist:
        # User is not a student - deny access
        raise Http404("You must be a student to access this page.")
    
    # Get all courses the student is enrolled in
    courses = Course.objects.filter(students=student).select_related('teacher').prefetch_related('students')
    
    # Prepare course data with attendance statistics
    course_data = []
    total_present = 0
    total_absent = 0
    total_classes = 0
    
    for course in courses:
        # Get attendance records for this student in this course
        attendance_records = Attendance.objects.filter(
            student=student,
            course=course
        ).order_by('-date')
        
        # Count attendance statistics
        present_count = attendance_records.filter(status=True).count()
        absent_count = attendance_records.filter(status=False).count()
        total_records = attendance_records.count()
        
        # Calculate attendance percentage
        attendance_percentage = 0
        if total_records > 0:
            attendance_percentage = round((present_count / total_records) * 100, 1)
        
        # Get recent attendance (last 5 records)
        recent_attendance = attendance_records[:5]
        
        # Get teacher name
        teacher_name = course.teacher.name
        
        course_data.append({
            'course': course,
            'teacher_name': teacher_name,
            'present_count': present_count,
            'absent_count': absent_count,
            'total_records': total_records,
            'attendance_percentage': attendance_percentage,
            'recent_attendance': recent_attendance,
        })
        
        # Aggregate totals
        total_present += present_count
        total_absent += absent_count
        total_classes += total_records
    
    # Calculate overall attendance percentage
    overall_percentage = 0
    if total_classes > 0:
        overall_percentage = round((total_present / total_classes) * 100, 1)
    
    context = {
        'student': student,
        'course_data': course_data,
        'total_courses': courses.count(),
        'total_present': total_present,
        'total_absent': total_absent,
        'total_classes': total_classes,
        'overall_percentage': overall_percentage,
    }
    
    return render(request, 'core/student_dashboard.html', context)

@login_required
def bulk_attendance(request):
    """
    Bulk attendance marking page.
    Supports pre-selecting a course via ?course=ID query parameter (from teacher dashboard).
    SECURITY: Teachers can only see and mark attendance for their own courses.
    """
    # SECURITY: Filter courses based on user role
    try:
        teacher = Teacher.objects.get(user=request.user)
        # Teachers can only see their own courses
        courses = Course.objects.filter(teacher=teacher)
        is_teacher = True
    except Teacher.DoesNotExist:
        # Admin/staff can see all courses
        courses = Course.objects.all()
        is_teacher = False
    
    # Get course from query parameter if provided (from teacher dashboard)
    course_id_from_url = request.GET.get('course')
    students = None
    selected_course = None

    # If course ID provided in URL, pre-select it (with security check)
    if course_id_from_url:
        try:
            selected_course = Course.objects.get(id=course_id_from_url)
            # SECURITY: Verify teacher can access this course
            if is_teacher and selected_course.teacher != teacher:
                selected_course = None  # Don't allow access to other teachers' courses
            else:
                students = selected_course.students.all()
        except Course.DoesNotExist:
            pass  # Invalid course ID, ignore

    if request.method == "POST":
        course_id = request.POST.get('course')
        attendance_date = request.POST.get('date')

        # Prevent future dates
        if attendance_date > str(date.today()):
            return render(request, 'core/bulk_attendance.html', {
                'courses': courses,
                'today': date.today(),
                'error': "Future attendance not allowed",
                'selected_course': selected_course,
                'students': students,
            })

        selected_course = Course.objects.get(id=course_id)
        
        # SECURITY: Verify teacher can access this course
        if is_teacher and selected_course.teacher != teacher:
            return render(request, 'core/bulk_attendance.html', {
                'courses': courses,
                'today': date.today(),
                'error': "You don't have permission to mark attendance for this course.",
                'selected_course': None,
                'students': None,
            })
        
        students = selected_course.students.all()

        for student in students:
            status = request.POST.get(f"status_{student.id}") == "on"

            Attendance.objects.update_or_create(
                student=student,
                course=selected_course,
                date=attendance_date,
                defaults={'status': status}
            )

        return redirect('bulk_attendance')

    return render(request, 'core/bulk_attendance.html', {
        'courses': courses,
        'students': students,
        'selected_course': selected_course,
        'today': date.today()
    })
