from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.db.models import Count, Q
from .forms import AttendanceForm
from .models import Course, Attendance, Student, Teacher
from datetime import date

# Create your views here.

@login_required
def dashboard(request):
    """
    Main dashboard - redirects teachers to teacher_dashboard, others see general dashboard.
    """
    # Check if user is a teacher and redirect to teacher dashboard
    try:
        Teacher.objects.get(user=request.user)
        return redirect('teacher_dashboard')
    except Teacher.DoesNotExist:
        pass  # Not a teacher, show general dashboard
    
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
