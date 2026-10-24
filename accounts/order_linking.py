from django.contrib.auth.models import User

from .models import Order, Profile


def attach_verified_guest_orders(user):
    email = (user.email or '').strip()
    if not email or not Profile.objects.filter(
        user=user,
        is_email_verified=True,
    ).exists():
        return 0

    matching_users = User.objects.filter(email__iexact=email)
    if matching_users.count() != 1:
        return 0

    return Order.objects.filter(
        user__isnull=True,
        guest_email__iexact=email,
    ).update(user=user)
