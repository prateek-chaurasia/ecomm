from .models import DeliveryGuideline, ReturnRefundPolicy
from products.models import Category


def delivery_guideline(request):
    return {
        'delivery_guideline': DeliveryGuideline.get_solo(),
        'return_refund_policy': ReturnRefundPolicy.get_solo(),
        'footer_categories': Category.objects.order_by('category_name'),
    }