from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.utils import timezone

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
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['roll_no']
    
class Teacher(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=50)
    email = models.EmailField()
    subject = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']

class Course(models.Model):
    name = models.CharField(max_length=50)
    code = models.CharField(max_length=20, unique=True, null=True, blank=True)
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE)
    students = models.ManyToManyField(Student)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']

class Attendance(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    date = models.DateField()
    status = models.BooleanField(
        choices=((True, 'Present'), (False, 'Absent')),
        default=False
    )  # Present or Absent
    marked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='attendance_marked')
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    # This prevents duplicate attendance records for the same student, course, and date
    # even admin cannot bypass this
    class Meta:
        unique_together = ('student', 'course', 'date')
        ordering = ['-date', 'student']
        indexes = [
            models.Index(fields=['course', 'date']),
            models.Index(fields=['student', 'course']),
        ]

    def clean(self):
        # Check if student is enrolled in course
        if self.course_id and self.student_id:
            if not self.course.students.filter(id=self.student_id).exists():
                raise ValidationError("Student is not enrolled in this course.")
        
        # Prevent future dates
        if self.date and self.date > timezone.now().date():
            raise ValidationError("Cannot mark attendance for future dates.")

    def save(self, *args, **kwargs):
        # Set marked_by if not set and user is available
        if not self.marked_by and hasattr(self, '_current_user'):
            self.marked_by = self._current_user
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student} - {self.course} - {self.date} - {'Present' if self.status else 'Absent'}"

class AuditLog(models.Model):
    """Track all changes to attendance records"""
    ACTION_CHOICES = [
        ('CREATE', 'Created'),
        ('UPDATE', 'Updated'),
        ('DELETE', 'Deleted'),
    ]
    
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, null=True, blank=True)
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    student = models.ForeignKey(Student, on_delete=models.SET_NULL, null=True)
    course = models.ForeignKey(Course, on_delete=models.SET_NULL, null=True)
    date = models.DateField(null=True)
    old_status = models.BooleanField(null=True, blank=True)
    new_status = models.BooleanField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['course', 'date']),
        ]

    def __str__(self):
        return f"{self.action} - {self.student} - {self.course} - {self.timestamp}"

class Grade(models.Model):
    """Grade/Assignment management"""
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    assignment_name = models.CharField(max_length=100)
    score = models.DecimalField(max_digits=5, decimal_places=2)
    max_score = models.DecimalField(max_digits=5, decimal_places=2, default=100)
    grade = models.CharField(max_length=2, blank=True)  # A, B, C, D, F
    due_date = models.DateField(null=True, blank=True)
    submitted_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='grades_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-due_date', '-created_at']
        unique_together = ('student', 'course', 'assignment_name')

    def calculate_grade(self):
        """Calculate letter grade based on percentage"""
        if self.max_score > 0:
            percentage = (self.score / self.max_score) * 100
            if percentage >= 90:
                return 'A'
            elif percentage >= 80:
                return 'B'
            elif percentage >= 70:
                return 'C'
            elif percentage >= 60:
                return 'D'
            else:
                return 'F'
        return ''

    def save(self, *args, **kwargs):
        if not self.grade:
            self.grade = self.calculate_grade()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student} - {self.assignment_name} - {self.score}/{self.max_score}"

class Notification(models.Model):
    """System notifications"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    notification_type = models.CharField(max_length=50, default='info')  # info, success, warning, error
    created_at = models.DateTimeField(auto_now_add=True)
    link = models.URLField(blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.title}"

class Assignment(models.Model):
    """Course assignments"""
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='assignments')
    title = models.CharField(max_length=200)
    description = models.TextField()
    due_date = models.DateTimeField()
    max_score = models.DecimalField(max_digits=5, decimal_places=2, default=100)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_published = models.BooleanField(default=False)

    class Meta:
        ordering = ['-due_date']
        indexes = [
            models.Index(fields=['course', 'due_date']),
        ]

    def __str__(self):
        return f"{self.course} - {self.title}"

class Event(models.Model):
    """Calendar events"""
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(null=True, blank=True)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, null=True, blank=True, related_name='events')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    event_type = models.CharField(max_length=50, default='general')  # general, exam, holiday, class
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_date']
        indexes = [
            models.Index(fields=['start_date']),
            models.Index(fields=['course', 'start_date']),
        ]

    def __str__(self):
        return f"{self.title} - {self.start_date}"

class StudyMaterial(models.Model):
    """Study materials uploaded by teachers for courses"""
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='study_materials')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to='study_materials/%Y/%m/%d/', null=True, blank=True)
    file_url = models.URLField(blank=True, help_text="External URL for the material (if not uploading a file)")
    material_type = models.CharField(
        max_length=50,
        choices=[
            ('pdf', 'PDF'),
            ('document', 'Document'),
            ('video', 'Video'),
            ('link', 'Link'),
            ('other', 'Other'),
        ],
        default='other'
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='study_materials_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_published = models.BooleanField(default=True, help_text="Published materials are visible to students")

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['course', 'created_at']),
            models.Index(fields=['is_published', 'created_at']),
        ]

    def __str__(self):
        return f"{self.course} - {self.title}"

    def get_file_url(self):
        """Return file URL or external URL"""
        if self.file:
            # Django's file.url should automatically include MEDIA_URL
            # But we'll ensure it's correct by always prepending /media/ if needed
            url = self.file.url
            # Ensure URL starts with /media/
            if url and not url.startswith('/media/') and not url.startswith('http'):
                url = '/media/' + url.lstrip('/')
            return url
        return self.file_url if self.file_url else None