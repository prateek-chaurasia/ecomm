import json
import logging
import secrets
import string
import uuid
from datetime import timedelta
from decouple import config

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Avg, Case, When, Value, IntegerField, Count
from django.db.models.functions import Coalesce
from django.core.mail import EmailMessage
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.contrib import messages
from products.models import Product, Category, Tag, Coupon
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from base.emails import send_customer_email
from home.models import (
    HomeBanner, NewsletterSubscriber, ReplacePolicy, ReturnRefundPolicy,
)
from home.forms import ContactForm, NewsletterSignupForm

logger = logging.getLogger(__name__)

VERIFY_TOKEN = config("WHATSAPP_VERIFY_TOKEN")
NEWSLETTER_CONFIRMATION_RESEND_DELAY = timedelta(minutes=5)


@require_POST
def newsletter_subscribe(request):
    if not settings.NEWSLETTER_ENABLED:
        raise Http404

    form = NewsletterSignupForm(request.POST)
    if not form.is_valid():
        message = next(iter(form.errors.values()))[0]
        return JsonResponse({'success': False, 'message': message}, status=400)
    if form.cleaned_data['website']:
        return JsonResponse({
            'success': False,
            'message': 'We could not process your request.',
        }, status=400)

    email = form.cleaned_data['email']
    now = timezone.now()
    send_confirmation = False
    with transaction.atomic():
        subscriber, created = NewsletterSubscriber.objects.get_or_create(
            email=email,
            defaults={'consented_at': now},
        )
        if subscriber.is_subscribed:
            return JsonResponse({
                'success': True,
                'message': 'This email is already subscribed to our newsletter.',
            })

        if not created:
            subscriber = NewsletterSubscriber.objects.select_for_update().get(
                pk=subscriber.pk,
            )
            if subscriber.is_subscribed:
                return JsonResponse({
                    'success': True,
                    'message': 'This email is already subscribed to our newsletter.',
                })
            if subscriber.unsubscribed_at:
                subscriber.confirmation_token = uuid.uuid4()
                subscriber.unsubscribe_token = uuid.uuid4()
                subscriber.unsubscribed_at = None
                subscriber.confirmation_sent_at = None
                subscriber.confirmed_at = None
            subscriber.consented_at = now

        if (
            subscriber.confirmation_sent_at is None
            or subscriber.confirmation_sent_at
            <= now - NEWSLETTER_CONFIRMATION_RESEND_DELAY
        ):
            subscriber.confirmation_sent_at = now
            send_confirmation = True
        subscriber.save()

    if send_confirmation:
        confirmation_url = request.build_absolute_uri(reverse(
            'newsletter_confirm',
            args=[subscriber.confirmation_token],
        ))
        try:
            send_customer_email(
                'Confirm your Bundle of Gifts newsletter subscription',
                (
                    'Please confirm that you want to receive newsletter and '
                    'promotional emails from Bundle of Gifts. Confirm your '
                    f'subscription here: {confirmation_url}\n\n'
                    'After confirming, you will receive a unique 5% discount '
                    'code for your first order.'
                ),
                email,
                'emails/newsletter_confirmation.html',
                {'confirmation_url': confirmation_url},
            )
        except Exception:
            logger.exception('Newsletter confirmation email failed for %s', email)
            NewsletterSubscriber.objects.filter(
                pk=subscriber.pk,
                is_subscribed=False,
            ).update(confirmation_sent_at=None)
            return JsonResponse({
                'success': False,
                'message': 'We could not send the confirmation email. Please try again later.',
            }, status=503)

    return JsonResponse({
        'success': True,
        'message': 'Please check your inbox and confirm your email to receive your 5% first-order discount.',
    })


@require_http_methods(['GET', 'POST'])
def newsletter_confirm(request, token):
    if not settings.NEWSLETTER_ENABLED:
        raise Http404

    subscriber = get_object_or_404(
        NewsletterSubscriber,
        confirmation_token=token,
    )
    if request.method == 'GET':
        return render(request, 'home/newsletter_confirm.html', {
            'subscriber': subscriber,
            'hide_newsletter_popup': True,
        })

    newly_confirmed = False
    with transaction.atomic():
        subscriber = get_object_or_404(
            NewsletterSubscriber.objects.select_for_update(),
            confirmation_token=token,
        )
        if not subscriber.is_subscribed:
            coupon = subscriber.coupon
            if coupon is None:
                alphabet = string.ascii_uppercase + string.digits
                coupon_code = ''.join(
                    secrets.choice(alphabet) for _ in range(10)
                )
                while Coupon.objects.filter(
                    coupon_code__iexact=coupon_code,
                ).exists():
                    coupon_code = ''.join(
                        secrets.choice(alphabet) for _ in range(10)
                    )
                coupon = Coupon.objects.create(
                    coupon_code=coupon_code,
                    subscriber_email=subscriber.email,
                    discount_percentage=5,
                    discount_amount=2_147_483_647,
                    minimum_amount=0,
                )
                subscriber.coupon = coupon
            subscriber.is_subscribed = True
            subscriber.confirmed_at = timezone.now()
            subscriber.unsubscribed_at = None
            subscriber.save(update_fields=[
                'coupon', 'is_subscribed', 'confirmed_at', 'unsubscribed_at',
            ])
            newly_confirmed = True

    coupon_email_sent = None
    if newly_confirmed:
        unsubscribe_url = request.build_absolute_uri(reverse(
            'newsletter_unsubscribe',
            args=[subscriber.unsubscribe_token],
        ))
        try:
            send_customer_email(
                'Your 5% first-order discount from Bundle of Gifts',
                (
                    'Thanks for confirming your newsletter subscription. '
                    'Use this unique code for 5% off your first order: '
                    f'{subscriber.coupon.coupon_code}\n\n'
                    f'Your code is for {subscriber.email} only.\n\n'
                    f'You can unsubscribe from promotional emails here: {unsubscribe_url}'
                ),
                subscriber.email,
                'emails/newsletter_welcome.html',
                {
                    'coupon_code': subscriber.coupon.coupon_code,
                    'subscriber_email': subscriber.email,
                    'unsubscribe_url': unsubscribe_url,
                },
            )
        except Exception:
            logger.exception(
                'Newsletter welcome email failed for %s',
                subscriber.email,
            )
            coupon_email_sent = False
        else:
            coupon_email_sent = True

    return render(request, 'home/newsletter_confirm.html', {
        'subscriber': subscriber,
        'coupon': subscriber.coupon,
        'confirmed': True,
        'coupon_email_sent': coupon_email_sent,
        'hide_newsletter_popup': True,
    })


@require_http_methods(['GET', 'POST'])
def newsletter_unsubscribe(request, token):
    subscriber = get_object_or_404(
        NewsletterSubscriber,
        unsubscribe_token=token,
    )
    if request.method == 'GET':
        return render(request, 'home/newsletter_unsubscribe.html', {
            'subscriber': subscriber,
            'hide_newsletter_popup': True,
        })

    with transaction.atomic():
        subscriber = get_object_or_404(
            NewsletterSubscriber.objects.select_for_update(),
            unsubscribe_token=token,
        )
        if subscriber.is_subscribed:
            subscriber.is_subscribed = False
            subscriber.unsubscribed_at = timezone.now()
            subscriber.confirmation_sent_at = None
            subscriber.confirmation_token = uuid.uuid4()
            subscriber.unsubscribe_token = uuid.uuid4()
            subscriber.save(update_fields=[
                'is_subscribed', 'unsubscribed_at', 'confirmation_sent_at',
                'confirmation_token', 'unsubscribe_token',
            ])
    return render(request, 'home/newsletter_unsubscribe.html', {
        'subscriber': subscriber,
        'unsubscribed': True,
        'hide_newsletter_popup': True,
    })

def _product_search_queryset(search_term):
    term = (search_term or '').strip()
    if not term:
        return Product.objects.none()

    term = term.replace('%', '\\%')
    return Product.objects.filter(
        Q(product_name__icontains=term) |
        Q(brand_name__icontains=term) |
        Q(product_desription__icontains=term) |
        Q(category__category_name__icontains=term) |
        Q(age_groups__name__icontains=term) |
        Q(tags__name__icontains=term),
        show_in_catalog=True,
    ).order_by('product_name', 'uid').distinct()


def index(request):
    query = Product.objects.filter(show_in_catalog=True).select_related('category').prefetch_related('product_images', 'age_groups').order_by('product_name', 'uid')
    categories = Category.objects.all()
    selected_sort = request.GET.get('sort')
    selected_category = request.GET.get('category')
    selected_brand = request.GET.get('brand')
    selected_age_group = request.GET.get('age_group')
    selected_tag = request.GET.get('tag')
    selected_query = request.GET.get('q', '').strip()
    now = timezone.now()
    banners = HomeBanner.objects.filter(
        is_active=True,
        starts_at__lte=now,
        ends_at__gte=now,
    ).order_by('-starts_at')

    if selected_query:
        query = _product_search_queryset(selected_query)

    if selected_category:
        query = query.filter(category__category_name=selected_category)

    if selected_brand:
        query = query.filter(brand_name=selected_brand)

    if selected_age_group:
        query = query.filter(age_groups__name=selected_age_group).distinct()

    if selected_tag:
        query = query.filter(tags__name=selected_tag).distinct()

    query = query.annotate(
        stock_priority=Case(
            When(stock_quantity__gt=0, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        avg_rating=Coalesce(Avg('reviews__stars'), 0.0),
        review_count=Count('reviews', distinct=True),
    )

    if selected_sort:
        if selected_sort == 'newest':
            query = query.filter(newest_product=True).order_by(
                'stock_priority', 'category_id', 'uid',
            )
        elif selected_sort == 'priceAsc':
            query = query.order_by('stock_priority', 'price', 'uid')
        elif selected_sort == 'priceDesc':
            query = query.order_by('stock_priority', '-price', 'uid')
        elif selected_sort == 'ratingAsc':
            query = query.order_by(
                'stock_priority', 'avg_rating', 'product_name', 'uid',
            )
        elif selected_sort == 'ratingDesc':
            query = query.order_by(
                'stock_priority', '-avg_rating', 'product_name', 'uid',
            )
        else:
            query = query.order_by('stock_priority', 'product_name', 'uid')
    else:
        query = query.order_by('stock_priority', 'product_name', 'uid')

    page = request.GET.get('page', 1)
    paginator = Paginator(query, 8)

    try:
        products = paginator.page(page)
    except PageNotAnInteger:
        products = paginator.page(1)
    except EmptyPage:
        products = paginator.page(paginator.num_pages)
    except Exception:
        logger.exception("Product pagination failed")

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        from django.template.loader import render_to_string
        return JsonResponse({
            'html': render_to_string(
                'product_parts/product_cards.html',
                {'products': products},
                request=request,
            ),
            'has_next': products.has_next(),
            'next_page': products.next_page_number() if products.has_next() else None,
        })

    context = {
        'products': products,
        'categories': categories,
        'selected_category': selected_category,
        'selected_sort': selected_sort,
        'selected_brand': selected_brand,
        'selected_age_group': selected_age_group,
        'selected_tag': selected_tag,
        'selected_query': selected_query,
        'brands': Product.objects.filter(show_in_catalog=True).exclude(brand_name='').values_list('brand_name', flat=True).distinct().order_by('brand_name'),
        'age_groups': sorted(set(
            Product.objects.filter(show_in_catalog=True).exclude(age_groups__isnull=True).values_list('age_groups__name', flat=True)
        )),
        'tags': Tag.objects.order_by('name'),
        'banners': banners,
    }
    return render(request, 'home/index.html', context)


def product_search(request):
    query = (request.GET.get('q', '') or '').strip()
    products = _product_search_queryset(query) if query else Product.objects.none()

    if request.GET.get('format') == 'json':
        suggestions = list(
            products.order_by('product_name').values_list('product_name', flat=True).distinct()[:10]
        )
        return JsonResponse({'suggestions': suggestions})

    context = {'query': query, 'products': products.select_related('category').prefetch_related('product_images', 'age_groups', 'tags')}
    return render(request, 'home/search.html', context)


def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == 'POST':
        admin_recipients = [
            address.strip()
            for address in settings.ADMIN_EMAIL.split(',')
            if address.strip()
        ]
        if form.is_valid() and not admin_recipients:
            messages.error(request, 'Contact email is temporarily unavailable. Please try again later.')
        elif form.is_valid():
            subject_label = dict(ContactForm.SUBJECT_CHOICES)[
                form.cleaned_data['subject']
            ]
            email_body = '\n'.join([
                f"Name: {form.cleaned_data['first_name']} {form.cleaned_data['last_name']}",
                f"Email: {form.cleaned_data['email']}",
                f"Subject: {subject_label}",
                f"Newsletter updates requested: {'Yes' if form.cleaned_data['newsletter'] else 'No'}",
                '',
                form.cleaned_data['message'],
            ])
            try:
                EmailMessage(
                    subject=f'Contact form: {subject_label}',
                    body=email_body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=admin_recipients,
                    reply_to=[form.cleaned_data['email']],
                ).send(fail_silently=False)
            except Exception:
                logger.exception('Contact form email delivery failed')
                messages.error(
                    request,
                    'We could not send your message right now. Please try again later.',
                )
            else:
                try:
                    acknowledgement = (
                        'Thank you for reaching out to us. Your query has been '
                        'received and is currently being reviewed by our team. '
                        'We will get back to you shortly with an update. Thank '
                        'you for your patience.\n\n'
                        'Best Regards,\n'
                        'Team BundleofGifts'
                    )
                    send_customer_email(
                        'Acknowledgement to your query',
                        acknowledgement,
                        form.cleaned_data['email'],
                        'emails/contact_acknowledgement.html',
                        {
                            'customer_name': form.cleaned_data['first_name'],
                            'acknowledgement_message': (
                                'Thank you for reaching out to us. Your query '
                                'has been received and is currently being '
                                'reviewed by our team. We will get back to '
                                'you shortly with an update. Thank you for '
                                'your patience.'
                            ),
                        },
                    )
                except Exception:
                    logger.exception(
                        'Contact form acknowledgement email delivery failed '
                        'for %s',
                        form.cleaned_data['email'],
                    )
                    messages.error(
                        request,
                        'Your message was sent, but we could not send the acknowledgement email.',
                    )
                else:
                    messages.success(
                        request,
                        'Your message has been sent. We will get back to you soon.',
                    )
                return redirect('contact')

    return render(request, 'home/contact.html', {'form': form})


def about(request):
    return render(request, 'home/about.html')


def dehradun_shopping_guide(request):
    categories = Category.objects.order_by('category_name')
    return render(
        request,
        'home/dehradun_shopping_guide.html',
        {'guide_categories': categories},
    )


def terms_and_conditions(request):
    return render(request, 'home/terms_and_conditions.html')


def privacy_policy(request):
    return render(request, 'home/privacy_policy.html')


def return_refund_replace_policy(request):
    return render(request, 'home/return_refund_replace_policy.html', {
        'return_refund_policy': ReturnRefundPolicy.get_solo(),
        'replace_policy': ReplacePolicy.get_solo(),
    })


@csrf_exempt
def whatsapp_webhook(request):
    # 1. Verification Handshake (GET Request)
    if request.method == "GET":
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        if mode == "subscribe" and token == VERIFY_TOKEN:
            logger.info("Webhook verified successfully!")
            return HttpResponse(challenge, status=200)
        else:
            logger.warning("Webhook verification failed. Token mismatch.")
            return HttpResponse("Forbidden", status=403)

    # 2. Receiving Webhook Data (POST Request)
    elif request.method == "POST":
        try:
            payload = json.loads(request.body.decode("utf-8"))

            # Print payload or pass it to an asynchronous task queue (like Celery)
            logger.info(f"Incoming WhatsApp payload: {payload}")

            # Safe navigation to extract the actual message array
            # Note: Meta can send statuses (sent, delivered, read) or actual messages
            entry = payload.get("entry", [])[0]
            changes = entry.get("changes", [])[0]
            value = changes.get("value", {})

            if "messages" in value:
                message_data = value["messages"][0]
                sender_phone = message_data.get("from")
                message_text = message_data.get("text", {}).get("body")

                print(f"Received message from {sender_phone}: {message_text}")
                # TODO: Process the business logic / generate a response here

            # Meta requires a rapid 200 OK response to prevent event retries
            return HttpResponse("EVENT_RECEIVED", status=200)

        except (IndexError, KeyError, json.JSONDecodeError) as e:
            logger.error(f"Error parsing payload: {str(e)}")
            # Still return 200 to prevent Meta from retrying broken payloads repeatedly
            return HttpResponse("Payload processed with errors", status=200)

    # Catch-all for unsupported methods
    return HttpResponse("Method Not Allowed", status=405)


def send_whatsapp_message(phone_number, message_text):
    import requests

    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone_number,
        "type": "text",
        "text": {"body": message_text},
    }

    response = requests.post(settings.WHATSAPP_URL, headers=headers, json=payload)
    if response.status_code == 200:
        logger.info(f"Message sent successfully to {phone_number}")
    else:
        logger.error(f"Failed to send message to {phone_number}: {response.text}")
