from django.utils import timezone

from accounts.models import BundleOrderProduct, Order, OrderItem, ReturnRequest
from products.models import ReturnDetails


def _order_eligibility_error(order):
    if order.status != Order.Status.DELIVERED:
        return 'A return can only be requested after the order is delivered.'

    if order.payment_mode != 'Cash on Delivery' and order.payment_status.lower() != 'paid':
        return 'A return can only be requested for a paid order.'
    return ''


def _product_eligibility_error(order, product, request_type):
    delivered_at = order.delivered_at or order.order_date
    delivery_date = timezone.localtime(delivered_at).date()
    today = timezone.localdate()
    action = 'replacement' if request_type == 'replacement' else 'return'
    try:
        details = product.return_details
    except ReturnDetails.DoesNotExist:
        window_days = 7
    else:
        eligible_policies = (
            {'returnable', 'replaceable'}
            if request_type == ReturnRequest.RequestType.REPLACEMENT
            else {'returnable'}
        )
        if (
            not details.is_returnable
            or details.return_policy not in eligible_policies
        ):
            return f'{product.product_name} is not eligible for a {action}.'
        window_days = details.return_window_days

    if (today - delivery_date).days > window_days:
        return f'The {action} window for {product.product_name} has expired.'
    return ''


def eligible_return_items(order, request_type, existing_requests=None):
    if _order_eligibility_error(order):
        return []

    existing_requests = list(
        existing_requests
        if existing_requests is not None
        else ReturnRequest.objects.filter(order=order)
    )
    active_requests = [
        item for item in existing_requests
        if item.status != ReturnRequest.Status.REJECTED
    ]
    if any(
        not item.order_item_id and not item.bundle_order_product_id
        for item in active_requests
    ):
        return []

    requested_order_item_ids = {
        item.order_item_id for item in active_requests if item.order_item_id
    }
    requested_bundle_product_ids = {
        item.bundle_order_product_id
        for item in active_requests if item.bundle_order_product_id
    }
    candidates = []

    order_items = OrderItem.objects.filter(order=order).select_related(
        'product', 'size_variant', 'color_variant',
    )
    for item in order_items:
        if not item.product_id or item.pk in requested_order_item_ids:
            continue
        if _product_eligibility_error(order, item.product, request_type):
            continue
        options = []
        if item.size_variant_id:
            options.append(f'Size {item.size_variant.size_name}')
        if item.color_variant_id:
            options.append(f'Color {item.color_variant.color_name}')
        label = item.product.product_name
        if options:
            label = f'{label} ({", ".join(options)})'
        candidates.append({
            'key': f'{request_type}:order:{item.pk}',
            'label': label,
            'request_type': request_type,
            'order_item': item,
            'bundle_order_product': None,
            'product': item.product,
        })

    bundle_products = BundleOrderProduct.objects.filter(
        bundle_item__order=order,
    ).select_related('product', 'bundle_item')
    for item in bundle_products:
        if (
            not item.product_id
            or item.pk in requested_bundle_product_ids
            or _product_eligibility_error(order, item.product, request_type)
        ):
            continue
        candidates.append({
            'key': f'{request_type}:bundle:{item.pk}',
            'label': f'{item.product.product_name} ({item.bundle_item.title})',
            'request_type': request_type,
            'order_item': None,
            'bundle_order_product': item,
            'product': item.product,
        })
    return candidates


def return_eligibility_error(order, request_type='return'):
    order_error = _order_eligibility_error(order)
    if order_error:
        return order_error
    if eligible_return_items(order, request_type):
        return ''
    if not (
        order.order_items.filter(product__isnull=False).exists()
        or BundleOrderProduct.objects.filter(
            bundle_item__order=order,
            product__isnull=False,
        ).exists()
    ):
        return 'This order does not contain any eligible products.'

    existing_requests = list(ReturnRequest.objects.filter(order=order))
    active_requests = [
        item for item in existing_requests
        if item.status != ReturnRequest.Status.REJECTED
    ]
    if any(
        not item.order_item_id and not item.bundle_order_product_id
        for item in active_requests
    ):
        return 'No additional items are available for a return or replacement.'

    if active_requests:
        return 'No additional items are available for a return or replacement.'

    for item in order.order_items.select_related('product'):
        if item.product_id:
            error = _product_eligibility_error(order, item.product, request_type)
            if error:
                return error
    for item in BundleOrderProduct.objects.filter(
        bundle_item__order=order,
    ).select_related('product'):
        error = _product_eligibility_error(order, item.product, request_type)
        if error:
            return error
    return 'This order does not contain any eligible products.'


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
