from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User

# Create your models here.

# Relationships
# A Teacher teaches many Courses
# A Course has many Students
# A Student attends many Courses
# Attendance connects Student + Course + Date

class Student(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=50)
    roll_no = models.IntegerField(unique=True)
    email = models.EmailField()
    dob = models.DateField()

    def __str__(self):
        return self.name
    
class Teacher(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=50)
    email = models.EmailField()
    subject = models.CharField(max_length=20)

    def __str__(self):
        return self.name

class Course(models.Model):
    name = models.CharField(max_length=50)
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE)
    students = models.ManyToManyField(Student)

    def __str__(self):
        return self.name
    
class Attendance(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    date = models.DateField()
    status = models.BooleanField(
        choices=((True, 'Present'), (False, 'Absent')),
        default=False
    )  # Present or Absent

# This prevents duplicate attendance records for the same student, course, and date
# even admin cannot bypass this
    class Meta:
        unique_together = ('student', 'course', 'date')

    def clean(self):

        if not self.students.filter(id=self.student.id).exists():
            raise ValidationError("Student is not enrolled in this course.")


        if Attendance.objects.filter(
            student=self.student,
            course=self.course,
            date=self.date
        ).exists():
            raise ValidationError("Attendance already marked for this student, course, and date.")


    def __str__(self):
        return f"{self.student} - {self.course} - {self.date}"