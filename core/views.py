from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .forms import AttendanceForm
from .models import Course, Attendance
from datetime import date

# Create your views here.

@login_required
def dashboard(request):
    return render(request, 'core/dashboard.html')

@login_required
def mark_attendance(request):
    form = AttendanceForm(request.POST or None)
    if form.is_valid():
        form.save()
    return render(request, 'core/attendance.html', {'form': form})

@login_required
def bulk_attendance(request):
     courses = Course.objects.all()
     students = None
     selected_course = None

     if request.method == "POST":
        course_id = request.POST.get('course')
        attendance_date = request.POST.get('date')

        # Prevent future dates
        if attendance_date > str(date.today()):
            return render(request, 'core/bulk_attendance.html', {
                'courses': courses,
                'today': date.today(),
                'error': "Future attendance not allowed"
            })

        selected_course = Course.objects.get(id=course_id)
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
