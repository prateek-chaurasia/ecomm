from decimal import Decimal, ROUND_HALF_UP

from products.models import GSTConfiguration


MONEY = Decimal('0.01')


def _money(value):
    return Decimal(value or 0).quantize(MONEY, rounding=ROUND_HALF_UP)


def _allocate(total, weights):
    total = _money(total)
    if not weights:
        return []
    weight_total = sum(weights, Decimal('0.00'))
    if weight_total <= 0:
        weights = [Decimal('1.00')] * len(weights)
        weight_total = Decimal(len(weights))
    allocations = []
    remaining = total
    for weight in weights[:-1]:
        allocation = _money(total * weight / weight_total)
        allocations.append(allocation)
        remaining -= allocation
    allocations.append(remaining)
    return allocations


def build_invoice_context(order):
    invoice_customer_name = order.guest_name
    invoice_customer_email = order.guest_email
    if order.user_id:
        invoice_customer_email = order.user.email

    lines = []
    for item in order.order_items.select_related(
        'product', 'size_variant', 'color_variant',
    ):
        product = item.product
        amount = _money(item.product_price)
        if not amount and product:
            amount = _money(item.get_total_price())
        original_amount = (
            _money(item.original_product_price)
            if item.original_product_price is not None
            else amount
        )
        lines.append({
            'name': product.product_name if product else 'Product',
            'sku': product.sku if product else '',
            'quantity': item.quantity,
            'amount': amount,
            'original_amount': original_amount,
            'hsn_code': item.hsn_code or (product.hsn_code if product else ''),
            'gst_rate': item.gst_rate if item.hsn_code else (
                product.gst_rate if product else Decimal('0.00')
            ),
            'size': item.size_variant.size_name if item.size_variant else '',
            'color': item.color_variant.color_name if item.color_variant else '',
        })

    for bundle in order.bundle_items.prefetch_related(
        'selected_products__product',
    ):
        products = list(bundle.selected_products.all())
        if not products:
            continue
        product_prices = [
            Decimal(selected.product_price or 0)
            for selected in products
        ]
        original_prices = [
            Decimal(selected.original_product_price or 0)
            for selected in products
        ]
        if not any(product_prices):
            unit_prices = _allocate(
                bundle.unit_price,
                [Decimal(selected.product.price) for selected in products],
            )
            product_prices = unit_prices
        if not any(original_prices):
            original_unit_total = sum(
                (
                    Decimal(max(
                        selected.product.marked_price,
                        selected.product.price,
                    ))
                    for selected in products
                ),
                Decimal('0.00'),
            )
            original_unit_total = max(
                original_unit_total,
                Decimal(bundle.unit_price),
            )
            original_prices = _allocate(
                original_unit_total,
                [Decimal(selected.product.price) for selected in products],
            )
        for selected, unit_price, original_unit_price in zip(
            products, product_prices, original_prices,
        ):
            product = selected.product
            lines.append({
                'name': product.product_name,
                'sku': product.sku,
                'quantity': bundle.quantity,
                'amount': _money(unit_price * bundle.quantity),
                'original_amount': _money(original_unit_price * bundle.quantity),
                'hsn_code': selected.hsn_code or product.hsn_code,
                'gst_rate': selected.gst_rate if selected.hsn_code else product.gst_rate,
                'size': '',
                'color': '',
            })

    total_before_discount = sum(
        (line['amount'] for line in lines), Decimal('0.00'),
    )
    coupon_discount = (
        _money(order.coupon.calculate_discount(total_before_discount))
        if order.coupon_id else Decimal('0.00')
    )
    coupon_allocations = _allocate(
        coupon_discount, [line['amount'] for line in lines],
    )
    product_discount_total = sum(
        (
            max(line['original_amount'] - line['amount'], Decimal('0.00'))
            for line in lines
        ),
        Decimal('0.00'),
    )
    original_product_total = sum(
        (line['original_amount'] for line in lines),
        Decimal('0.00'),
    )
    product_discount_percentage = (
        _money(product_discount_total * Decimal('100') / original_product_total)
        if original_product_total else Decimal('0.00')
    )

    current_seller = GSTConfiguration.get_solo()
    seller = {
        'seller_name': order.seller_name or current_seller.seller_name,
        'seller_address': order.seller_address or current_seller.seller_address,
        'seller_gstin': order.seller_gstin or current_seller.seller_gstin,
        'seller_email': order.seller_email or current_seller.seller_email,
        'seller_state': order.seller_state or current_seller.seller_state,
        'seller_state_code': (
            order.seller_state_code or current_seller.seller_state_code
        ),
    }
    tax_type_configured = bool(
        seller['seller_state'] and order.place_of_supply_state
    )
    same_state = bool(
        tax_type_configured
        and seller['seller_state'].strip().casefold()
        == order.place_of_supply_state.strip().casefold()
    )
    taxable_total = Decimal('0.00')
    tax_total = Decimal('0.00')
    cgst_total = Decimal('0.00')
    sgst_total = Decimal('0.00')
    igst_total = Decimal('0.00')
    invoice_total = Decimal('0.00')

    total_discount = Decimal('0.00')
    for line, line_coupon_discount in zip(lines, coupon_allocations):
        product_discount = max(
            line['original_amount'] - line['amount'], Decimal('0.00'),
        )
        line['discount'] = product_discount + line_coupon_discount
        total_discount += line['discount']
        net_amount = max(
            line['amount'] - line_coupon_discount, Decimal('0.00'),
        )
        rate = Decimal(line['gst_rate'] or 0)
        if order.prices_include_tax and rate:
            line['taxable_amount'] = _money(
                net_amount * Decimal('100') / (Decimal('100') + rate),
            )
            line['tax_amount'] = net_amount - line['taxable_amount']
            line['line_total'] = net_amount
        else:
            line['taxable_amount'] = net_amount
            line['tax_amount'] = _money(net_amount * rate / Decimal('100'))
            line['line_total'] = line['taxable_amount'] + line['tax_amount']
        line['unit_price'] = _money(
            line['original_amount'] / line['quantity']
            if line['quantity'] else 0,
        )
        if same_state:
            line['cgst'] = _money(line['tax_amount'] / 2)
            line['sgst'] = line['tax_amount'] - line['cgst']
            line['igst'] = Decimal('0.00')
        else:
            line['cgst'] = Decimal('0.00')
            line['sgst'] = Decimal('0.00')
            line['igst'] = line['tax_amount']
        taxable_total += line['taxable_amount']
        tax_total += line['tax_amount']
        cgst_total += line['cgst']
        sgst_total += line['sgst']
        igst_total += line['igst']
        invoice_total += line['line_total']

    shipping_amount = _money(order.additional_delivery_fee)
    priority_delivery_amount = _money(order.priority_delivery_fee)
    invoice_total += shipping_amount + priority_delivery_amount
    return {
        'order': order,
        'invoice_lines': lines,
        'invoice_settings': seller,
        'invoice_customer_name': invoice_customer_name,
        'invoice_customer_email': invoice_customer_email,
        'invoice_number': f'INV-{order.order_id}',
        'invoice_date': order.order_date.date(),
        'date_of_supply': order.order_date.date(),
        'total_quantity': sum(line['quantity'] for line in lines),
        'taxable_total': _money(taxable_total),
        'tax_total': _money(tax_total),
        'cgst_total': _money(cgst_total),
        'sgst_total': _money(sgst_total),
        'igst_total': _money(igst_total),
        'discount_total': _money(total_discount),
        'product_discount_total': _money(product_discount_total),
        'product_discount_percentage': product_discount_percentage,
        'coupon_discount_total': _money(coupon_discount),
        'coupon_code': order.coupon.coupon_code if order.coupon_id else '',
        'coupon_discount_percentage': (
            order.coupon.discount_percentage if order.coupon_id else 0
        ),
        'coupon_discount_cap': (
            order.coupon.discount_amount if order.coupon_id else 0
        ),
        'shipping_amount': shipping_amount,
        'priority_delivery_amount': priority_delivery_amount,
        'invoice_total': _money(invoice_total),
        'round_off': _money(0),
        'same_state_supply': same_state,
        'tax_type_configured': tax_type_configured,
    }
