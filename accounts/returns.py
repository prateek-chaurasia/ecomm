from django.utils import timezone

from accounts.models import Order
from products.models import ReturnDetails


def return_eligibility_error(order, request_type='return'):
    if order.status != Order.Status.DELIVERED:
        return 'A return can only be requested after the order is delivered.'

    if order.payment_mode != 'Cash on Delivery' and order.payment_status.lower() != 'paid':
        return 'A return can only be requested for a paid order.'

    products = [
        item.product
        for item in order.order_items.select_related('product')
        if item.product_id
    ]
    for bundle in order.bundle_items.prefetch_related('selected_products__product'):
        products.extend(
            selected.product
            for selected in bundle.selected_products.all()
            if selected.product_id
        )
    if not products:
        return 'This order does not contain any eligible products.'

    delivered_at = order.delivered_at or order.order_date
    delivery_date = timezone.localtime(delivered_at).date()
    today = timezone.localdate()
    for product in products:
        try:
            details = product.return_details
        except ReturnDetails.DoesNotExist:
            window_days = 7
        else:
            eligible_policies = (
                {'returnable', 'replaceable'}
                if request_type == 'replacement'
                else {'returnable'}
            )
            if (
                not details.is_returnable
                or details.return_policy not in eligible_policies
            ):
                action = 'replacement' if request_type == 'replacement' else 'return'
                return f'{product.product_name} is not eligible for a {action}.'
            window_days = details.return_window_days

        if (today - delivery_date).days > window_days:
            action = 'replacement' if request_type == 'replacement' else 'return'
            return f'The {action} window for {product.product_name} has expired.'

    return ''


def return_request_timeline(return_request):
    if return_request.request_type == 'replacement':
        stages = [
            ('requested', 'Request submitted', return_request.created_at),
            ('approved', 'Request approved', None),
            (
                'replacement_preparing',
                'Replacement being prepared',
                None,
            ),
            (
                'replacement_dispatched',
                'Replacement dispatched',
                return_request.replacement_dispatched_at,
            ),
            (
                'replacement_completed',
                'Replacement completed',
                None,
            ),
        ]
        status_order = [stage[0] for stage in stages]
    else:
        stages = [
            ('requested', 'Request submitted', return_request.created_at),
            ('approved', 'Request approved', None),
            ('shipped', 'Return shipped', return_request.shipped_at),
            ('received', 'Item received and under review', return_request.received_at),
            ('refund_pending', 'Refund initiated', None),
            ('refunded', 'Refund processed', return_request.refunded_at),
        ]
        status_order = [stage[0] for stage in stages]

    status = return_request.status
    if status == 'rejected':
        stages = stages[:1] + [('rejected', 'Request not approved', None)]
        current_index = 1
    elif status == 'refund_failed':
        stages = stages[:4] + [
            ('refund_failed', 'Refund processing failed', None),
            ('refunded', 'Refund processed', return_request.refunded_at),
        ]
        current_index = 4
    else:
        current_index = status_order.index(status)

    return [
        {
            'label': label,
            'timestamp': timestamp,
            'state': (
                'complete' if index < current_index
                else 'current' if index == current_index
                else 'upcoming'
            ),
        }
        for index, (_stage_status, label, timestamp) in enumerate(stages)
    ]
