from django import template
from core.models import Teacher

register = template.Library()

@register.simple_tag
def is_teacher(user):
    """
    Check if a user is a teacher.
    Returns True if user has a Teacher profile, False otherwise.
    """
    if not user or not user.is_authenticated:
        return False
    return Teacher.objects.filter(user=user).exists()

