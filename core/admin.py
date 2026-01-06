from django.contrib import admin
from django.contrib.auth.models import User
from django.forms import ModelForm
from django import forms
from django.core.exceptions import ValidationError
from .models import Student, Teacher, Course, Attendance

# Register your models here.

@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('name', 'roll_no', 'email', 'user')
    search_fields = ('name', 'roll_no')

class TeacherAdminForm(ModelForm):
    """Custom form to help create User when creating Teacher"""
    
    create_user = forms.BooleanField(
        required=False,
        initial=True,
        help_text="Check to automatically create a User account for this teacher. Uncheck if linking to an existing user."
    )
    username = forms.CharField(
        required=False,
        max_length=150,
        help_text="Username for the new user account (required if creating new user)"
    )
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True),
        help_text="Password for the new user account (required if creating new user). Leave blank when editing."
    )
    
    class Meta:
        model = Teacher
        fields = '__all__'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get('instance')
        if instance and instance.pk and instance.user:
            # If teacher already has a user, don't show create user option
            self.fields['create_user'].initial = False
            self.fields['create_user'].help_text = "User account already exists. Uncheck to link a different user."
            self.fields['username'].help_text = "Leave blank to keep existing user account"
            self.fields['password'].help_text = "Leave blank to keep existing password. Enter new password to change it."
        else:
            # New teacher - make username and password required if creating user
            self.fields['create_user'].initial = True
    
    def clean(self):
        cleaned_data = super().clean()
        create_user = cleaned_data.get('create_user', False)
        username = cleaned_data.get('username', '').strip()
        password = cleaned_data.get('password', '').strip()
        user = cleaned_data.get('user')
        
        # If creating new user
        if create_user:
            if not username:
                raise ValidationError({
                    'username': 'Username is required when creating a new user account.'
                })
            
            # Check if username already exists
            if User.objects.filter(username=username).exclude(pk=user.pk if user else None).exists():
                raise ValidationError({
                    'username': 'A user with this username already exists. Please choose a different username or uncheck "Create User" to link an existing user.'
                })
            
            # Password required for new users
            if not self.instance.pk and not password:
                raise ValidationError({
                    'password': 'Password is required when creating a new user account.'
                })
        
        # If not creating user but username/password provided, that's okay (might be updating)
        return cleaned_data

@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    form = TeacherAdminForm
    list_display = ('name', 'email', 'subject', 'user', 'has_user_account', 'course_count')
    list_filter = ('subject',)
    search_fields = ('name', 'email', 'subject', 'user__username')
    readonly_fields = ('course_count',)
    
    fieldsets = (
        ('Teacher Information', {
            'fields': ('name', 'email', 'subject')
        }),
        ('User Account', {
            'fields': ('user', 'create_user', 'username', 'password'),
            'description': 'Link to an existing user account or create a new one. If creating a new user, provide username and password.'
        }),
        ('Statistics', {
            'fields': ('course_count',),
            'classes': ('collapse',)
        }),
    )
    
    def has_user_account(self, obj):
        """Display if teacher has a user account"""
        return bool(obj.user)
    has_user_account.short_description = "Has Login"
    has_user_account.boolean = True
    
    def course_count(self, obj):
        """Display number of courses assigned to this teacher"""
        if obj.pk:
            count = obj.course_set.count()
            return f"{count} course{'s' if count != 1 else ''}"
        return "0 courses"
    course_count.short_description = "Courses Assigned"
    
    def save_model(self, request, obj, form, change):
        """Override save to create User if requested"""
        create_user = form.cleaned_data.get('create_user', False)
        username = form.cleaned_data.get('username', '').strip()
        password = form.cleaned_data.get('password', '').strip()
        
        # If creating new user
        if create_user and username:
            if not obj.user:  # Only create if no user exists
                # Create new user
                user = User.objects.create_user(
                    username=username,
                    email=obj.email,
                    password=password,
                    is_active=True,
                    is_staff=False,  # Teachers don't need admin access by default
                    is_superuser=False,
                )
                obj.user = user
            elif password:  # Update password if provided
                obj.user.set_password(password)
                obj.user.save()
        
        # Save the teacher
        super().save_model(request, obj, form, change)
    
    def get_form(self, request, obj=None, **kwargs):
        """Customize form based on whether we're adding or changing"""
        form = super().get_form(request, obj, **kwargs)
        
        # If editing existing teacher with user, make password optional
        if obj and obj.user:
            form.base_fields['password'].required = False
        
        return form

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('name', 'teacher', 'student_count')
    filter_horizontal = ('students',)
    list_filter = ('teacher',)
    search_fields = ('name', 'teacher__name')
    
    def student_count(self, obj):
        """Display number of students enrolled"""
        if obj.pk:
            count = obj.students.count()
            return f"{count} student{'s' if count != 1 else ''}"
        return "0 students"
    student_count.short_description = "Enrolled Students"

@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('student', 'course', 'date', 'status')
    list_filter = ('course', 'date', 'status', 'course__teacher')
    list_editable = ('status',)
    readonly_fields = ('date',)
    search_fields = ('student__name', 'course__name')
    date_hierarchy = 'date'