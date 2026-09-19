import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from accounts.models import Order, Profile
from base.emails import send_order_status_update_email

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()


@receiver(pre_save, sender=Order)
def capture_order_status_change(sender, instance, **kwargs):
    if not instance.pk:
        instance._status_changed = False
        return

    previous_status = sender.objects.filter(pk=instance.pk).values_list(
        'status', flat=True).first()
    instance._status_changed = previous_status != instance.status


@receiver(post_save, sender=Order)
def notify_customer_of_order_status(sender, instance, created, **kwargs):
    if created or not getattr(instance, '_status_changed', False):
        return

    try:
        send_order_status_update_email(instance)
    except Exception:
        logger.exception(
            'Order status email failed for order %s', instance.order_id)
