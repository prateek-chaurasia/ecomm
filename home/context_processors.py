from django.conf import settings
from .models import (
    DeliveryGuideline, MarqueeAnnouncement, ReplacePolicy, ReturnRefundPolicy,
    SiteTheme,
)
from products.models import Category


def delivery_guideline(request):
    service_city = str(settings.SERVICE_CITY).strip()
    service_area_message = (
        'We are currently operational across India.'
        if service_city.casefold() == 'india'
        else f'We are currently operational in {service_city} city only.'
    )
    return {
        'delivery_guideline': DeliveryGuideline.get_solo(),
        'return_refund_policy': ReturnRefundPolicy.get_solo(),
        'replace_policy': ReplacePolicy.get_solo(),
        'footer_categories': Category.objects.order_by('category_name'),
        'service_area_message': service_area_message,
        'active_site_theme': SiteTheme.get_active(),
        'marquee_announcements': MarqueeAnnouncement.objects.filter(
            is_active=True,
        ),
        'public_base_url': settings.PUBLIC_BASE_URL,
        'newsletter_enabled': settings.NEWSLETTER_ENABLED,
    }