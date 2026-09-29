from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse


def send_back_in_stock_email(wishlist_item):
    product = wishlist_item.product
    product_url = f'{settings.PUBLIC_BASE_URL}{reverse("get_product", args=[product.slug])}'
    return send_mail(
        subject=f'{product.product_name} is back in stock',
        message=(
            f"Hello {wishlist_item.user.get_full_name() or wishlist_item.user.username},\n\n"
            f'{product.product_name} from your wishlist is back in stock.\n'
            f'View the product: {product_url}\n\n'
            'Thank you for shopping with BundleofGifts.com.'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[wishlist_item.user.email],
        fail_silently=False,
    )