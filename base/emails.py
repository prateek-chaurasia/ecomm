import logging
import uuid

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from weasyprint import HTML
from decouple import config

logger = logging.getLogger(__name__)


def send_account_activation_email(email, email_token):
    subject = "Your account needs to be verified"
    email_from = settings.DEFAULT_FROM_EMAIL

    activation_link = f'{settings.PUBLIC_BASE_URL}/accounts/activate/{email_token}'

    html_message = render_to_string(
        'emails/account_activation.html', {'activation_link': activation_link})
    plain_message = f'Hi, please verify your account by clicking the link: {activation_link}'

    send_mail(
        subject,
        plain_message,
        email_from,
        [email],
        html_message=html_message
    )


def send_order_confirmation_email(order):
    subject = f'BundleofGifts.com order confirmation - {order.order_id}'
    email_from = settings.DEFAULT_FROM_EMAIL
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
    order_items = order.order_items.select_related('product').all()

    html_message = render_to_string(
        'emails/guest_order_confirmation.html',
        {
            'order': order,
            'order_items': order_items,
            'customer_name': customer_name,
            'tracking_link': tracking_link,
        },
    )
    plain_lines = [
        f'Hello {customer_name},',
        '',
        f'Your BundleofGifts.com order {order.order_id} has been placed.',
        'Items:',
    ]
    for item in order_items:
        plain_lines.append(
            f'- {item.product.product_name} x {item.quantity}: '
            f'INR {item.product_price}'
        )
    plain_lines.extend([
        '',
        f'Delivery charges: INR {order.additional_delivery_fee}' if order.additional_delivery_fee else 'Delivery charges: Free',
        'Delivery area: Outside Standard Service Area (estimated 4-6 days)' if order.outside_service_area else '',
        f'Total: INR {order.grand_total}',
        f'Payment mode: {order.payment_mode}',
        f'Delivery option: {order.get_delivery_option_display()}' if order.delivery_option else '',
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
        {'order': order, 'order_items': order_items},
    )
    invoice_pdf = HTML(string=invoice_html).write_pdf()

    email = EmailMultiAlternatives(
        subject,
        '\n'.join(plain_lines),
        email_from,
        [recipient],
    )
    email.attach_alternative(html_message, 'text/html')
    email.attach(
        f'invoice_{order.order_id}.pdf', invoice_pdf, 'application/pdf')
    email.send()
    logger.info(f"Order confirmation email sent for order {order.order_id} to {recipient}")


def send_admin_new_order_email(order):
    admin_recipients = [
        address.strip()
        for address in settings.ADMIN_EMAIL.split(',')
        if address.strip()
    ]
    if not admin_recipients:
        logger.warning("New order %s has no configured admin recipients", order.order_id)
        return

    admin_order_link = (
        f'{settings.PUBLIC_BASE_URL}{reverse("admin:accounts_order_change", args=[order.pk])}'
    )
    customer_name = order.guest_name or (
        order.user.get_full_name() or order.user.username
        if order.user else 'Guest customer'
    )
    order_items = list(order.order_items.all())
    item_count = sum(item.quantity for item in order_items)
    subject = f'New BundleofGifts.com order - {order.order_id}'
    plain_lines = [
        f'New order received: {order.order_id}\n\n'
        f'Customer: {customer_name}\n'
        f'Email: {order.guest_email or (order.user.email if order.user else "Not provided")}\n'
        f'Items: {item_count}\n'
        f'Delivery charges: INR {order.additional_delivery_fee if order.additional_delivery_fee else 0}',
    ]
    if order.outside_service_area:
        plain_lines.append('Delivery area: Outside Standard Service Area (estimated 4-6 days)')
    plain_lines.extend([
        f'Total: INR {order.grand_total}',
        f'Payment: {order.payment_mode} ({order.payment_status})',
        '',
        f'Open this order in admin: {admin_order_link}',
    ])
    plain_message = '\n'.join(plain_lines)
    html_message = render_to_string(
        'emails/admin_new_order.html',
        {
            'order': order,
            'customer_name': customer_name,
            'item_count': item_count,
            'admin_order_link': admin_order_link,
        },
    )
    email = EmailMultiAlternatives(
        subject,
        plain_message,
        settings.DEFAULT_FROM_EMAIL,
        admin_recipients,
    )
    email.attach_alternative(html_message, 'text/html')
    email.send()
    logger.info('New order notification sent for %s', order.order_id)


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
    html_message = render_to_string(
        'emails/order_status_update.html',
        {
            'order': order,
            'customer_name': customer_name,
            'status_label': status_label,
            'tracking_link': tracking_link,
        },
    )
    email = EmailMultiAlternatives(
        subject,
        plain_message,
        settings.DEFAULT_FROM_EMAIL,
        [recipient],
        bcc=[settings.ADMIN_EMAIL] if settings.ADMIN_EMAIL else [],
    )
    email.attach_alternative(html_message, 'text/html')
    email.send()
    logger.info(
        'Order status email sent for order %s to %s', order.order_id, recipient)


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
    message = (
        f'Hello {customer_name},\n\n'
        f'Your order {order.order_id} has been cancelled.\n'
        f'{refund_note}\n\n'
        f'Order total: INR {order.grand_total}\n'
        f'Track your order: {tracking_link}\n\n'
        'Thank you for shopping with BundleofGifts.com.'
    )
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [recipient],
        fail_silently=False,
    )
    logger.info(
        'Cancellation email sent for order %s to %s', order.order_id, recipient)


def send_admin_order_cancelled_email(order):
    admin_recipients = [
        address.strip()
        for address in settings.ADMIN_EMAIL.split(',')
        if address.strip()
    ]
    if not admin_recipients:
        logger.warning(
            'Cancelled order %s has no configured admin recipients', order.order_id)
        return

    customer_name = order.guest_name or (
        order.user.get_full_name() or order.user.username
        if order.user else 'Guest customer'
    )
    customer_email = order.guest_email or (
        order.user.email if order.user else 'Not provided'
    )
    admin_order_link = (
        f'{settings.PUBLIC_BASE_URL}{reverse("admin:accounts_order_change", args=[order.pk])}'
    )
    subject = f'Order cancelled - {order.order_id}'
    message = (
        f'Order {order.order_id} was cancelled by the customer.\n\n'
        f'Customer: {customer_name}\n'
        f'Email: {customer_email}\n'
        f'Total: INR {order.grand_total}\n'
        f'Payment mode: {order.payment_mode}\n'
        f'Payment status: {order.payment_status}\n\n'
        f'Open order in admin: {admin_order_link}'
    )
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        admin_recipients,
        fail_silently=False,
    )
    logger.info('Admin cancellation notification sent for order %s', order.order_id)
