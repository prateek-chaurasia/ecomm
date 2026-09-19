import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail
from django.template.loader import render_to_string
from weasyprint import HTML
from decouple import config

logger = logging.getLogger(__name__)


def send_account_activation_email(email, email_token):
    subject = "Your account needs to be verified"
    email_from = settings.DEFAULT_FROM_EMAIL

    # Use BASE_URL from settings (configured via environment variable)
    activation_link = f'{settings.BASE_URL}/accounts/activate/{email_token}'

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
    subject = f'Shop Easy order confirmation - {order.order_id}'
    email_from = settings.DEFAULT_FROM_EMAIL
    recipient = order.guest_email or (order.user.email if order.user else '')
    if not recipient:
        return

    customer_name = order.guest_name or (
        order.user.get_full_name() if order.user else 'Customer'
    )
    order_items = order.order_items.select_related('product').all()

    html_message = render_to_string(
        'emails/guest_order_confirmation.html',
        {
            'order': order,
            'order_items': order_items,
            'customer_name': customer_name,
        },
    )
    plain_lines = [
        f'Hello {customer_name},',
        '',
        f'Your Shop Easy order {order.order_id} has been placed.',
        'Items:',
    ]
    for item in order_items:
        plain_lines.append(
            f'- {item.product.product_name} x {item.quantity}: '
            f'INR {item.product_price}'
        )
    plain_lines.extend([
        '',
        f'Total: INR {order.grand_total}',
        f'Payment mode: {order.payment_mode}',
        f'Shipping address: {order.shipping_address}',
    ])
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
        bcc=[settings.ADMIN_EMAIL] if settings.ADMIN_EMAIL else [],
    )
    email.attach_alternative(html_message, 'text/html')
    email.attach(
        f'invoice_{order.order_id}.pdf', invoice_pdf, 'application/pdf')
    email.send()
    logger.info(f"Order confirmation email sent for order {order.order_id} to {recipient}")
