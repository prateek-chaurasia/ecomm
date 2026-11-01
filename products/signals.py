import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from base.emails import send_admin_low_stock_email
from products.emails import send_back_in_stock_email
from products.models import Product, Wishlist

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Product)
def capture_previous_stock(sender, instance, **kwargs):
    if instance.pk:
        previous_stock = sender.objects.filter(
            pk=instance.pk,
        ).values_list('stock_quantity', 'low_stock_threshold').first()
        instance._previous_stock_quantity = (
            previous_stock[0] if previous_stock else None
        )
        instance._previous_low_stock_threshold = (
            previous_stock[1] if previous_stock else None
        )
    else:
        instance._previous_stock_quantity = None
        instance._previous_low_stock_threshold = None


@receiver(post_save, sender=Product)
def notify_admins_when_stock_becomes_low(sender, instance, created, **kwargs):
    if created:
        return

    previous_stock = getattr(instance, '_previous_stock_quantity', None)
    previous_threshold = getattr(instance, '_previous_low_stock_threshold', None)
    if previous_stock is None or previous_threshold is None:
        return

    was_low_stock = 0 < previous_stock <= previous_threshold
    is_low_stock = 0 < instance.stock_quantity <= instance.low_stock_threshold
    if not is_low_stock or was_low_stock:
        return

    try:
        send_admin_low_stock_email(instance)
    except Exception:
        logger.exception(
            'Low-stock email failed for product %s',
            instance.uid,
        )


@receiver(post_save, sender=Product)
def notify_wishlist_users_when_restocked(sender, instance, created, **kwargs):
    previous_stock = getattr(instance, '_previous_stock_quantity', None)
    if created or previous_stock != 0 or instance.stock_quantity <= 0:
        return

    subscriptions = Wishlist.objects.filter(
        product=instance,
        notify_on_restock=True,
        restock_notification_sent=False,
    ).select_related('user', 'product')
    for wishlist_item in subscriptions:
        if not wishlist_item.user.email:
            continue
        try:
            send_back_in_stock_email(wishlist_item)
        except Exception:
            logger.exception(
                'Back-in-stock email failed for product %s and user %s',
                instance.uid,
                wishlist_item.user_id,
            )
            continue
        Wishlist.objects.filter(pk=wishlist_item.pk).update(
            restock_notification_sent=True)