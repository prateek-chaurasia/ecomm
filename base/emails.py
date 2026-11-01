import logging
import uuid
from io import BytesIO

from email.mime.image import MIMEImage

from PIL import Image, ImageOps
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.mail import EmailMultiAlternatives
from django.core.mail.message import SafeMIMEMultipart
from django.template.loader import render_to_string
from django.urls import reverse
from weasyprint import HTML
from decouple import config
from accounts.invoices import build_invoice_context

logger = logging.getLogger(__name__)


class RelatedEmailMultiAlternatives(EmailMultiAlternatives):
    def __init__(self, *args, inline_images=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.inline_images = [
            self._make_inline_image(content_id, image_data, subtype)
            for content_id, image_data, subtype in inline_images
        ]

    @staticmethod
    def _make_inline_image(content_id, image_data, subtype):
        image = MIMEImage(image_data, _subtype=subtype)
        image.add_header('Content-ID', f'<{content_id}>')
        image.add_header(
            'Content-Disposition',
            'inline',
            filename=f'{content_id}.{subtype}',
        )
        return image

    def _create_alternatives(self, msg):
        if not self.alternatives or not self.inline_images:
            return super()._create_alternatives(msg)

        encoding = self.encoding or settings.DEFAULT_CHARSET
        alternative_msg = SafeMIMEMultipart(
            _subtype=self.alternative_subtype,
            encoding=encoding,
        )
        if self.body:
            alternative_msg.attach(msg)

        for content, mimetype in self.alternatives:
            content_msg = self._create_mime_attachment(content, mimetype)
            if mimetype == 'text/html':
                related_msg = SafeMIMEMultipart(
                    _subtype='related',
                    encoding=encoding,
                )
                related_msg.attach(content_msg)
                for image in self.inline_images:
                    related_msg.attach(image)
                content_msg = related_msg
            alternative_msg.attach(content_msg)
        return alternative_msg


def send_customer_email(
    subject, plain_message, recipient, html_template, context, bcc=None,
    attachments=(), inline_images=(), from_email=None,
):
    brand_icon_cid = 'bundleofgifts-brand-icon'
    brand_icon_path = finders.find('images/bundleofgifts-icon.png')
    if not brand_icon_path:
        raise FileNotFoundError('Email brand icon PNG could not be found.')
    with open(brand_icon_path, 'rb') as icon:
        brand_icon = icon.read()
    email = RelatedEmailMultiAlternatives(
        subject,
        plain_message,
        from_email or settings.DEFAULT_FROM_EMAIL,
        [recipient],
        bcc=bcc or [],
        inline_images=(
            (brand_icon_cid, brand_icon, 'png'),
            *(
                (content_id, image_data, 'jpeg')
                for content_id, image_data in inline_images
            ),
        ),
    )
    email.attach_alternative(
        render_to_string(
            html_template,
            {
                'brand_url': settings.PUBLIC_BASE_URL.rstrip('/'),
                'brand_icon_cid': brand_icon_cid,
                **context,
            },
        ),
        'text/html',
    )
    for filename, content, mimetype in attachments:
        email.attach(filename, content, mimetype)
    return email.send(fail_silently=False)


def _make_product_thumbnail(product):
    product_image = next(
        (
            image for image in product.product_images.all()
            if image.image_url
        ),
        None,
    )
    if not product_image:
        return None

    try:
        with product_image.image_url.open('rb') as source_file:
            with Image.open(source_file) as source_image:
                image = ImageOps.exif_transpose(source_image)
                image.thumbnail((96, 96), Image.Resampling.LANCZOS)
                rgba_image = image.convert('RGBA')
                image = Image.new('RGB', rgba_image.size, 'white')
                image.paste(rgba_image, mask=rgba_image.getchannel('A'))
                output = BytesIO()
                image.save(output, format='JPEG', quality=78, optimize=True)
    except (OSError, ValueError):
        logger.warning(
            'Could not create email thumbnail for product %s',
            product.uid,
            exc_info=True,
        )
        return None

    return output.getvalue()


def prepare_product_email_thumbnail(product):
    thumbnail = _make_product_thumbnail(product)
    if not thumbnail:
        return None
    return f'product-{uuid.uuid4().hex}', thumbnail


def _prepare_order_thumbnails(order_items):
    inline_images = []
    product_thumbnails = {}
    for item in order_items:
        if not item.product:
            continue
        if item.product_id not in product_thumbnails:
            prepared_thumbnail = prepare_product_email_thumbnail(item.product)
            product_thumbnails[item.product_id] = prepared_thumbnail
            if prepared_thumbnail:
                inline_images.append(prepared_thumbnail)
        prepared_thumbnail = product_thumbnails[item.product_id]
        if prepared_thumbnail:
            item.email_thumbnail_cid = prepared_thumbnail[0]
    return inline_images


def _admin_email_recipients():
    return [
        address.strip()
        for address in settings.ADMIN_EMAIL.split(',')
        if address.strip()
    ]


def _send_admin_notification(
    subject,
    heading,
    summary,
    details,
    action_url,
    action_label,
    recipients=None,
    items=(),
):
    recipients = recipients if recipients is not None else _admin_email_recipients()
    if not recipients:
        return False

    plain_lines = [heading, '', summary, '']
    plain_lines.extend(
        f'{label}: {value}'
        for label, value in details
    )
    if items:
        plain_lines.extend(['', 'Items:'])
        plain_lines.extend(
            f'- {item["name"]} | SKU {item["sku"]} | '
            f'Qty {item["quantity"]} | INR {item["unit_price"]} each'
            for item in items
        )
    plain_lines.extend(['', f'{action_label}: {action_url}'])

    email = EmailMultiAlternatives(
        subject,
        '\n'.join(plain_lines),
        settings.DEFAULT_FROM_EMAIL,
        recipients,
    )
    email.attach_alternative(
        render_to_string(
            'emails/admin_notification.html',
            {
                'heading': heading,
                'summary': summary,
                'details': [
                    {'label': label, 'value': value}
                    for label, value in details
                ],
                'items': items,
                'action_url': action_url,
                'action_label': action_label,
            },
        ),
        'text/html',
    )
    email.send(fail_silently=False)
    return True


def _admin_order_item_details(order):
    order_items = order.order_items.select_related(
        'product', 'size_variant', 'color_variant',
    )
    details = [
        {
            'name': item.product.product_name if item.product else 'Product unavailable',
            'sku': item.product.sku if item.product else 'N/A',
            'variant': ', '.join(
                value for value in (
                    item.size_variant.size_name if item.size_variant else '',
                    item.color_variant.color_name if item.color_variant else '',
                ) if value
            ) or 'Standard',
            'quantity': item.quantity,
            'unit_price': item.product_price if item.product_price is not None else 'N/A',
            'line_total': (
                item.product_price * item.quantity
                if item.product_price is not None else 'N/A'
            ),
        }
        for item in order_items
    ]
    for bundle in order.bundle_items.prefetch_related(
        'selected_products__product',
    ):
        included_products = ', '.join(
            selected.product.product_name
            for selected in bundle.selected_products.all()
        ) or 'No product details'
        details.append({
            'name': f'{bundle.title} (includes: {included_products})',
            'sku': 'BUNDLE',
            'variant': bundle.packaging_name or 'Bundle',
            'quantity': bundle.quantity,
            'unit_price': bundle.unit_price,
            'line_total': bundle.get_total_price(),
        })
    return details


def send_account_activation_email(email, email_token):
    subject = "Your account needs to be verified"

    activation_link = f'{settings.PUBLIC_BASE_URL}/accounts/activate/{email_token}'

    plain_message = f'Hi, please verify your account by clicking the link: {activation_link}'

    send_customer_email(
        subject,
        plain_message,
        email,
        'emails/account_activation.html',
        {'activation_link': activation_link},
    )


def send_order_confirmation_email(order):
    subject = f'BundleofGifts.com order confirmation - {order.order_id}'
    recipient = order.guest_email or (order.user.email if order.user else '')
    if not recipient:
        return

    if not order.guest_access_token:
        order.guest_access_token = uuid.uuid4().hex
        order.save(update_fields=['guest_access_token', 'updated_at'])

    customer_name = order.guest_name or (
        order.user.get_full_name() if order.user else 'Customer'
    )
    tracking_link = (
        f'{settings.PUBLIC_BASE_URL}{reverse("track_order", kwargs={"order_id": order.order_id, "access_token": order.guest_access_token})}'
    )
    order_items = list(
        order.order_items.select_related('product')
        .prefetch_related('product__product_images')
    )
    inline_images = _prepare_order_thumbnails(order_items)
    invoice_context = build_invoice_context(order)

    plain_lines = [
        f'Hello {customer_name},',
        '',
        f'Your BundleofGifts.com order {order.order_id} has been placed.',
        'Items:',
    ]
    for item in order_items:
        product_name = (
            item.product.product_name if item.product else 'Product'
        )
        plain_lines.append(
            f'- {product_name} x {item.quantity}: '
            f'INR {item.product_price}'
        )
    if order.coupon_id:
        plain_lines.extend([
            '',
            (
                f'Coupon applied: {order.coupon.coupon_code} '
                f'({order.coupon.discount_percentage}% off, '
                f'maximum INR {order.coupon.discount_amount})'
            ),
            f'Coupon discount: INR {invoice_context["coupon_discount_total"]}',
        ])
    plain_lines.extend([
        '',
        f'Delivery charges: INR {order.additional_delivery_fee}' if order.additional_delivery_fee else 'Delivery charges: Free',
        f'Next-day priority delivery: INR {order.priority_delivery_fee}' if order.priority_delivery_fee else '',
        'Delivery area: Outside Standard Service Area (estimated 4-6 days)' if order.outside_service_area else '',
        f'Total: INR {order.grand_total}',
        f'Payment mode: {order.payment_mode}',
        f'Delivery option: {order.get_delivery_option_display()}' if order.delivery_option else '',
        order.delivery_promise,
        f'Shipping address: {order.shipping_address}',
        f'Order status: {order.get_status_display()}',
        '',
        f'Track your order: {tracking_link}',
    ])
    plain_lines = [line for line in plain_lines if line]
    if order.payment_mode == 'Cash on Delivery':
        plain_lines.extend(['', 'Please pay when your order is delivered.'])

    invoice_html = render_to_string(
        'accounts/order_pdf_generate.html',
        invoice_context,
    )
    invoice_pdf = HTML(string=invoice_html).write_pdf()

    send_customer_email(
        subject,
        '\n'.join(plain_lines),
        recipient,
        'emails/guest_order_confirmation.html',
        {
            'order': order,
            'order_items': order_items,
            'customer_name': customer_name,
            'tracking_link': tracking_link,
            'coupon_discount_total': invoice_context['coupon_discount_total'],
        },
        attachments=(
            (f'invoice_{order.order_id}.pdf', invoice_pdf, 'application/pdf'),
        ),
        inline_images=inline_images,
    )
    logger.info(f"Order confirmation email sent for order {order.order_id} to {recipient}")


def send_admin_new_order_email(order):
    seller_recipients = _admin_email_recipients()
    if not seller_recipients:
        logger.warning("New order %s has no configured seller recipients", order.order_id)
        return

    order_link = (
        f'{settings.PUBLIC_BASE_URL}{reverse("admin:accounts_order_change", args=[order.pk])}'
    )
    customer_name = order.guest_name or (
        order.user.get_full_name() or order.user.username
        if order.user else 'Guest customer'
    )
    items = _admin_order_item_details(order)
    item_count = sum(item['quantity'] for item in items)
    details = [
        ('Order ID', order.order_id),
        ('Order placed', order.created_at.strftime('%d %b %Y, %I:%M %p')),
        ('Customer', customer_name),
        ('Customer email', order.guest_email or (
            order.user.email if order.user else 'Not provided'
        )),
        ('Order status', order.get_status_display()),
        ('Delivery pincode', order.delivery_pincode or 'Not provided'),
        ('Delivery option', order.get_delivery_option_display() or 'Not specified'),
        ('Delivery promise', order.delivery_promise or 'Not specified'),
        ('Item count', item_count),
        ('Payment method', order.payment_mode),
        ('Payment status', order.payment_status),
        ('Delivery charges', f'INR {order.additional_delivery_fee or 0}'),
        ('Priority delivery fee', f'INR {order.priority_delivery_fee or 0}'),
        ('Total', f'INR {order.grand_total}'),
    ]
    if order.outside_service_area:
        details.append((
            'Delivery area',
            'Outside Standard Service Area (estimated 4-6 days)',
        ))

    _send_admin_notification(
        subject=f'NEW ORDER RECEIVED | {order.order_id} | {item_count} item(s)',
        heading='New order received',
        summary='Review payment and delivery details, then prepare the order.',
        details=details,
        action_url=order_link,
        action_label='Open order details in seller portal',
        recipients=seller_recipients,
        items=items,
    )
    logger.info('New order notification sent for %s', order.order_id)


def send_admin_low_stock_email(product):
    recipients = _admin_email_recipients()
    if not recipients:
        logger.warning(
            'Low-stock product %s has no configured admin recipients',
            product.uid,
        )
        return

    product_link = (
        f'{settings.PUBLIC_BASE_URL}{reverse("admin:products_product_change", args=[product.pk])}'
    )
    _send_admin_notification(
        subject=f'LOW STOCK ALERT | {product.sku} | {product.product_name}',
        heading='Product inventory is low',
        summary='Stock has entered the configured low-stock range. Review inventory and restock as needed.',
        details=[
            ('Product', product.product_name),
            ('SKU', product.sku),
            ('Available stock', product.stock_quantity),
            ('Low-stock threshold', product.low_stock_threshold),
            ('Stock status', product.stock_status),
            ('Unit price', f'INR {product.price}'),
        ],
        action_url=product_link,
        action_label='Open product inventory',
        recipients=recipients,
    )


def send_order_status_update_email(order):
    recipient = order.guest_email or (order.user.email if order.user else '')
    if not recipient:
        return

    if not order.guest_access_token:
        order.guest_access_token = uuid.uuid4().hex
        order.save(update_fields=['guest_access_token', 'updated_at'])

    tracking_link = (
        f'{settings.PUBLIC_BASE_URL}{reverse("track_order", kwargs={"order_id": order.order_id, "access_token": order.guest_access_token})}'
    )
    customer_name = order.guest_name or (
        order.user.get_full_name() if order.user else 'Customer'
    )
    status_label = order.get_status_display()
    subject = f'BundleofGifts.com order update - {order.order_id}'
    order_items = list(
        order.order_items.select_related('product')
        .prefetch_related('product__product_images')
    )
    inline_images = _prepare_order_thumbnails(order_items)
    plain_message = (
        f'Hello {customer_name},\n\n'
        f'Your order {order.order_id} is now: {status_label}.\n\n'
    )
    if order.additional_delivery_fee:
        plain_message += (
            f'Additional delivery fee: INR {order.additional_delivery_fee}.\n\n'
        )
    plain_message += (
        f'Track your order: {tracking_link}\n\n'
        'Thank you for shopping with BundleofGifts.com.'
    )
    send_customer_email(
        subject,
        plain_message,
        recipient,
        'emails/order_status_update.html',
        {
            'order': order,
            'customer_name': customer_name,
            'status_label': status_label,
            'tracking_link': tracking_link,
            'order_items': order_items,
        },
        inline_images=inline_images,
    )
    logger.info(
        'Order status email sent for order %s to %s', order.order_id, recipient)


def send_admin_order_status_update_email(order):
    recipients = _admin_email_recipients()
    if not recipients:
        return

    customer_name = order.guest_name or (
        order.user.get_full_name() or order.user.username
        if order.user else 'Guest customer'
    )
    order_link = (
        f'{settings.PUBLIC_BASE_URL}{reverse("admin:accounts_order_change", args=[order.pk])}'
    )
    _send_admin_notification(
        subject=f'ORDER STATUS UPDATE | {order.order_id} | {order.get_status_display()}',
        heading='Order status updated',
        summary='An order status changed. Review the current fulfillment and payment state.',
        details=[
            ('Order ID', order.order_id),
            ('Customer', customer_name),
            ('Customer email', order.guest_email or (
                order.user.email if order.user else 'Not provided'
            )),
            ('Current status', order.get_status_display()),
            ('Payment method', order.payment_mode),
            ('Payment status', order.payment_status),
            ('Order total', f'INR {order.grand_total}'),
        ],
        action_url=order_link,
        action_label='Open order details in seller portal',
        recipients=recipients,
        items=_admin_order_item_details(order),
    )


def send_order_cancelled_customer_email(order):
    recipient = order.guest_email or (order.user.email if order.user else '')
    if not recipient:
        logger.warning(
            'Cancelled order %s has no customer email address', order.order_id)
        return

    tracking_link = (
        f'{settings.PUBLIC_BASE_URL}{reverse("track_order", kwargs={"order_id": order.order_id, "access_token": order.guest_access_token})}'
    )
    customer_name = order.guest_name or (
        order.user.get_full_name() if order.user else 'Customer'
    )
    refund_note = (
        'Our team will review any applicable refund.'
        if order.payment_status == 'Refund pending'
        else 'No payment refund is due for this order.'
    )
    subject = f'Order cancelled - {order.order_id}'
    order_items = list(
        order.order_items.select_related('product')
        .prefetch_related('product__product_images')
    )
    inline_images = _prepare_order_thumbnails(order_items)
    message = (
        f'Hello {customer_name},\n\n'
        f'Your order {order.order_id} has been cancelled.\n'
        f'{refund_note}\n\n'
        f'Order total: INR {order.grand_total}\n'
        f'Track your order: {tracking_link}\n\n'
        'Thank you for shopping with BundleofGifts.com.'
    )
    send_customer_email(
        subject,
        message,
        recipient,
        'emails/order_cancelled.html',
        {
            'order': order,
            'customer_name': customer_name,
            'refund_note': refund_note,
            'tracking_link': tracking_link,
            'order_items': order_items,
        },
        inline_images=inline_images,
    )
    logger.info(
        'Cancellation email sent for order %s to %s', order.order_id, recipient)


def send_admin_order_cancelled_email(order):
    seller_recipients = _admin_email_recipients()
    if not seller_recipients:
        logger.warning(
            'Cancelled order %s has no configured seller recipients', order.order_id)
        return

    customer_name = order.guest_name or (
        order.user.get_full_name() or order.user.username
        if order.user else 'Guest customer'
    )
    customer_email = order.guest_email or (
        order.user.email if order.user else 'Not provided'
    )
    seller_order_link = (
        f'{settings.PUBLIC_BASE_URL}{reverse("admin:accounts_order_change", args=[order.pk])}'
    )
    _send_admin_notification(
        subject=f'Order cancelled | {order.order_id}',
        heading='Order cancelled',
        summary='The customer cancelled this order. Review its payment and fulfillment state.',
        details=[
            ('Order ID', order.order_id),
            ('Customer', customer_name),
            ('Customer email', customer_email),
            ('Order total', f'INR {order.grand_total}'),
            ('Payment method', order.payment_mode),
            ('Payment status', order.payment_status),
        ],
        action_url=seller_order_link,
        action_label='Open order details',
        recipients=seller_recipients,
    )
    logger.info('Seller cancellation notification sent for order %s', order.order_id)


def send_admin_return_request_email(return_request):
    recipients = _admin_email_recipients()
    if not recipients:
        logger.error(
            'Return request for order %s has no configured seller recipients',
            return_request.order.order_id,
        )
        raise RuntimeError('No seller email recipients are configured.')

    seller_link = (
        f'{settings.PUBLIC_BASE_URL}{reverse("admin:accounts_returnrequest_change", args=[return_request.pk])}'
    )
    order = return_request.order
    is_replacement = (
        return_request.request_type == 'replacement'
    )
    request_label = 'replacement' if is_replacement else 'return'
    customer_name = order.guest_name or (
        order.user.get_full_name() if order.user else 'Guest'
    )
    _send_admin_notification(
        subject=f'{request_label.upper()} REQUEST | {order.order_id}',
        heading=f'{request_label.title()} request received',
        summary='Review the request details and attached customer evidence in the seller portal.',
        details=[
            ('Order ID', order.order_id),
            ('Customer', customer_name),
            ('Customer email', order.guest_email or (
                order.user.email if order.user else 'Not provided'
            )),
            ('Request type', return_request.get_request_type_display()),
            ('Requested item', return_request.requested_item_name),
            ('Replacement reason', (
                return_request.replacement_reason if is_replacement
                else 'Not applicable'
            )),
            ('Customer reason', return_request.reason),
            ('Request photos', return_request.request_images.count()),
            ('Order total', f'INR {order.grand_total}'),
        ],
        action_url=seller_link,
        action_label='Review request',
        recipients=recipients,
    )


def send_admin_return_shipment_email(return_request):
    recipients = _admin_email_recipients()
    if not recipients:
        logger.warning(
            'Return shipment for order %s has no configured seller recipients',
            return_request.order.order_id,
        )
        return

    seller_link = (
        f'{settings.PUBLIC_BASE_URL}{reverse("admin:accounts_returnrequest_change", args=[return_request.pk])}'
    )
    order = return_request.order
    request_label = return_request.get_request_type_display()
    _send_admin_notification(
        subject=f'RETURN SHIPPED | {order.order_id}',
        heading=f'{request_label} shipment submitted',
        summary='The customer submitted shipment details. Review tracking and photos.',
        details=[
            ('Order ID', order.order_id),
            ('Request type', request_label),
            ('Requested item', return_request.requested_item_name),
            ('Courier partner', return_request.courier_partner_name or 'Not provided'),
            ('Carrier tracking number', return_request.tracking_number or 'Not provided'),
            ('Shipment photos', return_request.shipment_images.count()),
        ],
        action_url=seller_link,
        action_label='Review shipment',
        recipients=recipients,
    )


def send_admin_return_status_update_email(return_request):
    recipients = _admin_email_recipients()
    if not recipients:
        return

    order = return_request.order
    customer_name = order.guest_name or (
        order.user.get_full_name() or order.user.username
        if order.user else 'Guest customer'
    )
    request_label = return_request.get_request_type_display()
    request_link = (
        f'{settings.PUBLIC_BASE_URL}{reverse("admin:accounts_returnrequest_change", args=[return_request.pk])}'
    )
    details = [
        ('Order ID', order.order_id),
        ('Customer', customer_name),
        ('Customer email', order.guest_email or (
            order.user.email if order.user else 'Not provided'
        )),
        ('Request type', request_label),
        ('Request status', return_request.get_status_display()),
        ('Requested item', return_request.requested_item_name),
    ]
    if return_request.refund_amount is not None:
        details.append(('Refund amount', f'INR {return_request.refund_amount}'))
    if return_request.refund_reference:
        details.append(('Refund reference', return_request.refund_reference))
    if return_request.admin_note:
        details.append(('Admin note', return_request.admin_note))

    _send_admin_notification(
        subject=f'{request_label.upper()} STATUS UPDATE | {order.order_id}',
        heading=f'{request_label} status updated',
        summary='A return or replacement request changed status.',
        details=details,
        action_url=request_link,
        action_label='Open request details',
        recipients=recipients,
    )


def send_return_status_update_email(return_request):
    order = return_request.order
    recipient = order.guest_email or (order.user.email if order.user else '')
    if not recipient:
        logger.warning(
            'Return update for order %s has no customer email address',
            order.order_id,
        )
        return

    if not order.guest_access_token:
        order.guest_access_token = uuid.uuid4().hex
        order.save(update_fields=['guest_access_token', 'updated_at'])
    if order.user:
        status_path = reverse(
            'return_request', kwargs={'order_id': order.order_id})
    else:
        status_path = reverse(
            'return_request_guest',
            kwargs={
                'order_id': order.order_id,
                'access_token': order.guest_access_token,
            },
        )
    status_link = f'{settings.PUBLIC_BASE_URL.rstrip("/")}{status_path}'

    is_replacement = return_request.request_type == 'replacement'
    status_messages = {
        'requested': (
            'Your request has been submitted and is awaiting seller review.'
        ),
        'approved': (
            'Your replacement request has been approved. Our team will prepare '
            'the same item for replacement. You do not need to ship anything '
            'back unless we contact you.'
            if is_replacement
            else
            'Your return request has been approved. Please ship the item to the '
            'return address below at your own cost and then submit your carrier '
            'tracking number and shipment photos on the return page.'
        ),
        'shipped': 'Your shipment details and photos have been received.',
        'received': 'Your returned item has been received and is awaiting inspection.',
        'refund_pending': 'Your refund has been initiated to the original payment method.',
        'refund_failed': (
            'The payment provider could not complete the refund. Our team will review it.'
        ),
        'replacement_preparing': 'Our team is preparing the same item for replacement.',
        'replacement_dispatched': (
            'Your replacement item has been dispatched.'
            + (
                f' Tracking number: {return_request.replacement_tracking_number}.'
                if return_request.replacement_tracking_number else ''
            )
        ),
        'replacement_completed': 'Your replacement request has been completed.',
        'refunded': (
            'Our team has marked your refund as processed.'
            if order.payment_mode == 'Cash on Delivery'
            else 'Your refund has been processed to the original payment method.'
        ),
        'rejected': (
            'Your replacement request was not approved.'
            if is_replacement
            else 'Your return request was not approved.'
        ),
    }
    customer_name = order.guest_name or (
        order.user.get_full_name() if order.user else 'Customer'
    )
    status_label = return_request.get_status_display()
    message = (
        f'Hello {customer_name},\n\n'
        f'Update for {"replacement" if is_replacement else "return"} '
        f'request on order {order.order_id}: '
        f'{status_label}.\n'
        f'Item: {return_request.requested_item_name}\n\n'
        f'{status_messages.get(return_request.status, "Your return request has been updated.")}'
        f'\n\nView your complete request status and updates:\n{status_link}'
    )
    return_address = ''
    refund_amount = None
    refund_reference = ''
    deduction_amount = None
    deduction_reason = ''
    admin_note = ''
    if return_request.status == 'approved' and not is_replacement:
        from home.models import ReturnRefundPolicy

        address = ReturnRefundPolicy.get_solo().return_shipping_address.strip()
        if address:
            return_address = address
            message += f'\n\nReturn shipping address:\n{address}'
        message += '\n\nReturn shipping costs are the customer\'s responsibility.'
    elif return_request.status in {
        'refund_pending', 'refund_failed', 'refunded',
    }:
        if return_request.refund_amount is not None:
            refund_amount = return_request.refund_amount
            message += f'\n\nRefund amount: INR {return_request.refund_amount}.'
        if return_request.refund_reference:
            refund_reference = return_request.refund_reference
            message += f'\nRefund reference: {return_request.refund_reference}.'
        if return_request.deduction_amount:
            deduction_amount = return_request.deduction_amount
            deduction_reason = return_request.deduction_reason
            message += (
                f'\nCondition deduction: INR {return_request.deduction_amount}. '
                f'Deduction reason: {return_request.deduction_reason}'
            )
    elif return_request.status == 'rejected' and return_request.admin_note.strip():
        admin_note = return_request.admin_note
        message += f'\n\nReason: {return_request.admin_note}'

    request_label = 'replacement' if is_replacement else 'return'
    order_items = list(
        order.order_items.select_related('product')
        .prefetch_related('product__product_images')
    )
    inline_images = _prepare_order_thumbnails(order_items)
    send_customer_email(
        f'{"Replacement" if is_replacement else "Return"} update - {order.order_id}',
        message,
        recipient,
        'emails/return_status_update.html',
        {
            'order': order,
            'customer_name': customer_name,
            'request_label': request_label,
            'requested_item_name': return_request.requested_item_name,
            'status_label': status_label,
            'status_message': status_messages.get(
                return_request.status,
                'Your return request has been updated.',
            ),
            'status_link': status_link,
            'return_address': return_address,
            'has_refund_amount': refund_amount is not None,
            'refund_amount': refund_amount,
            'refund_reference': refund_reference,
            'has_deduction_amount': deduction_amount is not None,
            'deduction_amount': deduction_amount,
            'deduction_reason': deduction_reason,
            'admin_note': admin_note,
            'order_items': order_items,
        },
        inline_images=inline_images,
    )
