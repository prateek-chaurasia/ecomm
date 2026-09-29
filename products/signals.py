import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from products.emails import send_back_in_stock_email
from products.models import Product, Wishlist

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Product)
def capture_previous_stock(sender, instance, **kwargs):
    if instance.pk:
        instance._previous_stock_quantity = sender.objects.filter(
            pk=instance.pk,
        ).values_list('stock_quantity', flat=True).first()
    else:
        instance._previous_stock_quantity = None


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