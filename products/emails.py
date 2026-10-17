from django.conf import settings
from django.urls import reverse

from base.emails import prepare_product_email_thumbnail, send_customer_email


def send_back_in_stock_email(wishlist_item):
    product = wishlist_item.product
    product_url = f'{settings.PUBLIC_BASE_URL}{reverse("get_product", args=[product.slug])}'
    customer_name = (
        wishlist_item.user.get_full_name() or wishlist_item.user.username
    )
    prepared_thumbnail = prepare_product_email_thumbnail(product)
    inline_images = [prepared_thumbnail] if prepared_thumbnail else []
    return send_customer_email(
        f'{product.product_name} is back in stock',
        (
            f"Hello {customer_name},\n\n"
            f'{product.product_name} from your wishlist is back in stock.\n'
            f'View the product: {product_url}\n\n'
            'Thank you for shopping with BundleofGifts.com.'
        ),
        wishlist_item.user.email,
        'emails/back_in_stock.html',
        {
            'customer_name': customer_name,
            'product': product,
            'product_url': product_url,
            'product_thumbnail_cid': (
                prepared_thumbnail[0] if prepared_thumbnail else ''
            ),
        },
        inline_images=inline_images,
    )