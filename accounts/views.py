import os
import json
import uuid
import hmac
import logging
from collections import Counter
from urllib.parse import urlencode
import razorpay
from weasyprint import CSS, HTML
from products.models import *
from django.urls import reverse
from django.conf import settings
from django.contrib import messages
from django.forms import ImageField
from django.http import JsonResponse
from django.core.exceptions import ValidationError
from django.http import Http404
from home.models import City, DeliveryGuideline, ShippingAddress, State
from home.delivery import (
    get_automatic_delivery_selection, get_delivery_fee, get_delivery_options,
)
from django.contrib.auth.models import User
from django.template.loader import get_template
from accounts.models import (
    Profile, Cart, CartItem, Order, OrderItem, BundleCartItem,
    BundleOrderItem, BundleOrderProduct, ServiceablePincode, ReturnRequest,
    ReturnShipmentImage, ReturnRequestImage,
)
from base.emails import (
    send_account_activation_email,
    send_admin_new_order_email,
    send_admin_order_cancelled_email,
    send_admin_return_request_email,
    send_admin_return_shipment_email,
    send_order_cancelled_customer_email,
    send_order_confirmation_email,
)
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Q
from django.http import HttpResponseRedirect, HttpResponse
from django.contrib.auth import authenticate, login, logout
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.shortcuts import redirect, render, get_object_or_404
from accounts.forms import (
    UserUpdateForm, UserProfileForm, RegistrationForm, ShippingAddressForm,
    GuestShippingAddressForm, CustomPasswordChangeForm, ReturnRequestForm,
)
from products.forms import ReviewForm
from accounts.returns import (
    return_eligibility_error,
    return_request_timeline,
)
from home.models import ReturnRefundPolicy

logger = logging.getLogger(__name__)


def _service_area_error(city, country='IN'):
    country_code = getattr(country, 'code', country)
    if str(country_code or '').upper() != 'IN':
        return 'We currently deliver within India only.'

    service_city = str(settings.SERVICE_CITY).strip()
    if service_city.casefold() == 'india':
        return None

    city_name = getattr(city, 'name', city)
    if str(city_name or '').strip().casefold() != service_city.casefold():
        return (
            f'We are currently operational in {service_city} city only. '
            f'Please choose a delivery address in {service_city}.'
        )
    return None


def _automatic_delivery_selection(
    city, pincode, latitude, longitude, accuracy_meters, order_total,
):
    is_serviceable = ServiceablePincode.objects.filter(
        pincode=(pincode or '').strip(), is_active=True,
    ).exists()
    return get_automatic_delivery_selection(
        DeliveryGuideline.get_solo(),
        city,
        is_serviceable,
        latitude,
        longitude,
        accuracy_meters,
        order_total,
    )


def _cart_delivery_fee(cart):
    return get_delivery_fee(
        DeliveryGuideline.get_solo(),
        cart.get_cart_total_price_after_coupon(),
    )


def _cart_pricing_context(cart):
    delivery_guideline = DeliveryGuideline.get_solo()
    cart_total_after_coupon = cart.get_cart_total_price_after_coupon()
    cart_has_items = cart.cart_items.exists() or cart.bundle_items.exists()
    delivery_fee = (
        get_delivery_fee(delivery_guideline, cart_total_after_coupon)
        if cart_has_items else None
    )
    if delivery_fee is None:
        delivery_fee_reason = ''
    elif delivery_guideline.delivery_charge_threshold is None:
        delivery_fee_reason = 'No free-delivery threshold is configured.'
    elif cart_total_after_coupon < delivery_guideline.delivery_charge_threshold:
        delivery_fee_reason = (
            f'Applies because the order total is below the '
            f'₹{delivery_guideline.delivery_charge_threshold} free-delivery threshold.'
        )
    else:
        delivery_fee_reason = ''

    return {
        'cart_has_items': cart_has_items,
        'cart_subtotal': cart.get_cart_total() + (delivery_fee or 0),
        'cart_discount': cart.get_cart_total() - cart_total_after_coupon,
        'delivery_fee': delivery_fee,
        'delivery_fee_reason': delivery_fee_reason,
        'cart_total_after_delivery': cart_total_after_coupon + (delivery_fee or 0),
    }


def generate_order_id():
    return f'BOG-{timezone.localdate():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}'


# Create your views here.


def login_page(request):
    next_url = request.GET.get('next')
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password')

        user_obj = authenticate(request, username=username, password=password)
        if user_obj and getattr(user_obj.profile, 'is_email_verified', False):
            login(request, user_obj)
            messages.success(request, 'Login Successfull.')

            if url_has_allowed_host_and_scheme(
                url=next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            else:
                return redirect('index')

        messages.error(request, 'Invalid username or password.')
        return HttpResponseRedirect(request.path_info)

    return render(request, 'accounts/login.html')


def register_page(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        registration_form = RegistrationForm(request.POST)
        if not registration_form.is_valid():
            return render(
                request,
                'accounts/register.html',
                {'registration_form': registration_form},
            )
        first_name = registration_form.cleaned_data['first_name']
        last_name = registration_form.cleaned_data['last_name']
        email = registration_form.cleaned_data['email']
        password = registration_form.cleaned_data['password']

        user_obj = User.objects.filter(Q(username=username) | Q(email=email))

        if user_obj.exists():
            messages.error(request, 'Username or email already exists!')
            return HttpResponseRedirect(request.path_info)

        user_obj = User.objects.create(
            username=username, first_name=first_name, last_name=last_name, email=email)
        user_obj.set_password(password)
        user_obj.save()

        profile = Profile.objects.get(user=user_obj)
        profile.email_token = str(uuid.uuid4())
        profile.save()

        send_account_activation_email(email, profile.email_token)
        messages.success(request, "An email has been sent to your mail.")
        return HttpResponseRedirect(request.path_info)

    return render(
        request,
        'accounts/register.html',
        {'registration_form': RegistrationForm()},
    )


@require_POST
@login_required
def user_logout(request):
    logout(request)
    messages.warning(request, "Logged Out Successfully!")
    return redirect('index')


def activate_email_account(request, email_token):
    try:
        user = Profile.objects.get(email_token=email_token)
        user.is_email_verified = True
        user.save()
        messages.success(request, 'Account verification successful.')
        return redirect('login')
    except Exception as e:
        logger.exception("Account activation failed")
        return HttpResponse('Invalid email token.')


def get_active_cart(request, create=False):
    if request.user.is_authenticated:
        cart = Cart.objects.filter(user=request.user, is_paid=False).first()
        if not cart and create:
            cart = Cart.objects.create(user=request.user)
        return cart

    if not request.session.session_key:
        if not create:
            return None
        request.session.create()
    cart = Cart.objects.filter(
        session_key=request.session.session_key, is_paid=False).first()
    if not cart and create:
        Cart.objects.filter(
            session_key=request.session.session_key, is_paid=True
        ).update(session_key=None)
        cart = Cart.objects.create(session_key=request.session.session_key)
    return cart


def _cart_has_available_stock(cart):
    if CartItem.objects.filter(
        cart=cart,
        product__isnull=False,
        quantity__gt=F('product__stock_quantity'),
    ).exists():
        return False
    for bundle_item in cart.bundle_items.prefetch_related('selected_products__product'):
        if any(
            selected.product.stock_quantity < bundle_item.quantity
            for selected in bundle_item.selected_products.all()
        ):
            return False
    return True


def cart_owner_filter(request):
    if request.user.is_authenticated:
        return {'user': request.user}
    return {'session_key': request.session.session_key}


def _apply_coupon(request, cart_obj):
    coupon_obj = Coupon.objects.filter(
        coupon_code__exact=request.POST.get('coupon'),
    ).first()
    if not coupon_obj:
        messages.error(request, 'Invalid coupon code.')
        return False
    if cart_obj.coupon:
        messages.error(
            request, 'Remove the applied coupon before applying another one.')
        return False
    if coupon_obj.is_expired:
        messages.error(request, 'Coupon code expired.')
        return False
    if cart_obj.get_cart_total() < coupon_obj.minimum_amount:
        messages.error(
            request, f'Amount should be greater than {coupon_obj.minimum_amount}')
        return False

    cart_obj.coupon = coupon_obj
    cart_obj.save(update_fields=['coupon', 'updated_at'])
    messages.success(request, 'Coupon applied successfully.')
    return True


@require_POST
def add_to_cart(request, uid):
    buy_now = request.POST.get('buy_now') == '1'
    buy_now_added = False
    if buy_now:
        request.session.pop('buy_now_checkout_cart_uid', None)
    try:
        variant = request.POST.get('size') or request.GET.get('size')
        product = get_object_or_404(Product, uid=uid)
        try:
            quantity = max(1, int(request.POST.get('quantity') or request.GET.get('quantity', 1)))
        except (TypeError, ValueError):
            quantity = 1

        gift_wrap = (request.POST.get('gift_wrap') or request.GET.get('gift_wrap')) == '1' and product.gift_wrap_available
        size_variant = get_object_or_404(
            SizeVariant, size_name=variant) if variant else None
        if buy_now:
            existing_cart = get_active_cart(request)
            existing_item = (
                CartItem.objects.filter(
                    cart=existing_cart,
                    product=product,
                    size_variant=size_variant,
                    color_variant=None,
                ).first()
                if existing_cart else None
            )
            if existing_item:
                if existing_item.gift_wrap != gift_wrap:
                    existing_item.gift_wrap = gift_wrap
                    existing_item.save(update_fields=['gift_wrap', 'updated_at'])
                request.session['buy_now_checkout_cart_uid'] = str(
                    existing_item.cart_id)
                return redirect(
                    'checkout' if request.user.is_authenticated else 'guest-checkout')

        if product.stock_quantity < 1:
            messages.warning(request, 'This product is out of stock.')
            return redirect(reverse('get_product', kwargs={'slug': product.slug}))

        cart = get_active_cart(request, create=True)

        cart_item = CartItem.objects.filter(
            cart=cart,
            product=product,
            size_variant=size_variant,
            color_variant=None,
        ).first()
        already_in_cart = cart_item.quantity if cart_item else 0
        quantity_after_add = already_in_cart + quantity
        if quantity_after_add > product.stock_quantity:
            available_to_add = max(product.stock_quantity - already_in_cart, 0)
            available_label = 'item' if available_to_add == 1 else 'items'
            messages.error(
                request,
                f'Only {available_to_add} more {available_label} can be added. '
                f'{already_in_cart} already in your cart; {product.stock_quantity} in stock.',
            )
        else:
            cart_item, created = CartItem.objects.get_or_create(
                cart=cart,
                product=product,
                size_variant=size_variant,
                color_variant=None,
                defaults={'quantity': quantity, 'gift_wrap': gift_wrap},
            )
            if created:
                buy_now_added = True
                messages.success(request, 'Item added to cart successfully.')
            else:
                quantity_after_add = cart_item.quantity + quantity
                if quantity_after_add > product.stock_quantity:
                    available_to_add = max(
                        product.stock_quantity - cart_item.quantity, 0)
                    available_label = 'item' if available_to_add == 1 else 'items'
                    messages.error(
                        request,
                        f'Only {available_to_add} more {available_label} can be added. '
                        f'{cart_item.quantity} already in your cart; '
                        f'{product.stock_quantity} in stock.',
                    )
                else:
                    cart_item.quantity = quantity_after_add
                    cart_item.gift_wrap = gift_wrap
                    cart_item.save()
                    buy_now_added = True
                    messages.success(request, 'Item added to cart successfully.')

    except Exception as e:
        logger.exception("Add to cart failed for product %s", uid)
        messages.error(request, 'Error adding item to cart.')

    if buy_now and buy_now_added:
        request.session['buy_now_checkout_cart_uid'] = str(cart.uid)
        return redirect(
            'checkout' if request.user.is_authenticated else 'guest-checkout')

    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect(reverse('cart'))


def cart(request):
    request.session.pop('buy_now_checkout_cart_uid', None)
    cart_obj = get_active_cart(request)
    if not cart_obj:
        messages.warning(
            request, "Your cart is empty. Please add a product to cart.")
        return redirect(reverse('index'))

    if request.method == 'POST':
        _apply_coupon(request, cart_obj)
        return redirect('cart')

    cart_items = list(cart_obj.cart_items.select_related('product'))
    stock_notices = []
    for cart_item in cart_items:
        available_quantity = cart_item.product.stock_quantity if cart_item.product else 0
        if available_quantity and cart_item.quantity > available_quantity:
            stock_notices.append({
                'product_name': cart_item.product.product_name,
                'available_quantity': available_quantity,
            })
            cart_item.quantity = available_quantity
            cart_item.save(update_fields=['quantity', 'updated_at'])

    context = {
        'cart': cart_obj,
        'cart_items': cart_items,
        'stock_notices': stock_notices,
        'current_step': 'cart',
        'base_url': settings.BASE_URL,
        'available_coupons': Coupon.objects.filter(
            is_expired=False,
        ).order_by('minimum_amount', 'coupon_code'),
        **_cart_pricing_context(cart_obj),
    }
    return render(request, 'accounts/cart.html', context)


@login_required
def checkout(request):
    cart_obj = get_active_cart(request)
    if not cart_obj or not cart_obj.cart_items.exists() and not cart_obj.bundle_items.exists():
        messages.warning(request, 'Your cart is empty. Please add a product to cart.')
        return redirect('cart')

    coupon_enabled = (
        request.session.get('buy_now_checkout_cart_uid') == str(cart_obj.uid)
    )
    if request.method == 'POST' and (
        'apply_coupon' in request.POST or 'remove_checkout_coupon' in request.POST
    ):
        if not coupon_enabled:
            messages.error(request, 'Coupons can only be changed from the cart.')
            return redirect('checkout')
        if 'remove_checkout_coupon' in request.POST:
            cart_obj.coupon = None
            cart_obj.save(update_fields=['coupon', 'updated_at'])
            messages.success(request, 'Coupon Removed.')
        else:
            _apply_coupon(request, cart_obj)
        return redirect('checkout')

    addresses = list(
        ShippingAddress.objects.filter(user=request.user)
        .order_by('-is_default', '-created_at')
    )
    selected_address_id = (
        request.POST.get('selected_address_id')
        or request.GET.get('address')
    )
    selected_address = None
    if selected_address_id:
        selected_address = next(
            (address for address in addresses if str(address.uid) == selected_address_id),
            None,
        )
        if selected_address is None:
            messages.error(request, 'Please choose a valid delivery address.')
            return redirect('checkout')

    if request.method == 'POST':
        if not selected_address:
            messages.error(request, 'Please choose a delivery address.')
        elif not _cart_has_available_stock(cart_obj):
            messages.error(
                request,
                'One or more products are no longer available in the requested quantity.',
            )
        else:
            request.session.pop('buy_now_checkout_cart_uid', None)
            return redirect(f'{reverse("payment")}?address={selected_address.uid}')

    if selected_address is None and addresses:
        profile = Profile.objects.filter(user=request.user).select_related(
            'shipping_address',
        ).first()
        profile_address = profile.shipping_address if profile else None
        if profile_address and profile_address.user_id == request.user.id:
            selected_address = next(
                (address for address in addresses if address.pk == profile_address.pk),
                None,
            )
        if selected_address is None:
            selected_address = next(
                (address for address in addresses if address.is_default),
                addresses[0],
            )

    context = {
        'cart': cart_obj,
        'current_step': 'checkout',
        'addresses': addresses,
        'selected_address_id': str(selected_address.uid) if selected_address else '',
        'coupon_enabled': coupon_enabled,
        'available_coupons': Coupon.objects.filter(
            is_expired=False,
        ).order_by('minimum_amount', 'coupon_code') if coupon_enabled else (),
        **_cart_pricing_context(cart_obj),
    }
    return render(request, 'accounts/checkout.html', context)


@login_required
def payment(request):
    cart_obj = get_active_cart(request)
    if not cart_obj or not cart_obj.cart_items.exists() and not cart_obj.bundle_items.exists():
        messages.warning(request, 'Your cart is empty. Please add a product to cart.')
        return redirect('cart')

    address_id = request.GET.get('address')
    selected_address = ShippingAddress.objects.filter(
        user=request.user, uid=address_id,
    ).first() if address_id else None
    if not selected_address:
        messages.error(request, 'Please choose a valid delivery address before payment.')
        return redirect('checkout')
    if not _cart_has_available_stock(cart_obj):
        messages.error(
            request,
            'One or more products are no longer available in the requested quantity.',
        )
        return redirect('cart')

    context = {
        'cart': cart_obj,
        'current_step': 'payment',
        'selected_address': selected_address,
        'razorpay_key_id': settings.RAZORPAY_KEY_ID,
        'online_payment_enabled': settings.ONLINE_PAYMENT_ENABLED,
        **_cart_pricing_context(cart_obj),
    }
    return render(request, 'accounts/payment.html', context)


@require_POST
def delivery_options(request):
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid delivery selection request.'}, status=400)

    if request.user.is_authenticated:
        selected_address = ShippingAddress.objects.filter(
            user=request.user,
            uid=payload.get('selected_address_id'),
        ).first()
        if not selected_address:
            return JsonResponse({'error': 'Choose a saved delivery address first.'}, status=400)
        city = selected_address.city.name
    else:
        city = (payload.get('city') or '').strip()

    service_area_error = _service_area_error(city)
    if service_area_error:
        return JsonResponse({'error': service_area_error}, status=409)

    cart_obj = get_active_cart(request)
    if not cart_obj:
        return JsonResponse({'error': 'Your cart is empty.'}, status=400)

    try:
        options = get_delivery_options(
            DeliveryGuideline.get_solo(),
            city,
            payload.get('latitude'),
            payload.get('longitude'),
            payload.get('accuracy_meters'),
            cart_obj.get_cart_total_price_after_coupon(),
        )
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=400)
    return JsonResponse(options)


@require_GET
def cities_for_state(request):
    try:
        state_id = int(request.GET.get('state'))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Choose a valid state.'}, status=400)
    state = State.objects.filter(pk=state_id, country__code='IN').first()
    if not state:
        return JsonResponse({'error': 'Choose a valid state.'}, status=400)
    cities = City.objects.filter(state=state).order_by('name').values('id', 'name')
    return JsonResponse({'cities': list(cities)})


@require_POST
@login_required
def create_payment_order(request):
    selected_address_id = request.POST.get('selected_address_id') or request.GET.get('selected_address_id')
    if not selected_address_id and request.content_type == 'application/json':
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            payload = {}
        selected_address_id = payload.get('selected_address_id')

    if selected_address_id:
        selected_address = ShippingAddress.objects.filter(user=request.user, uid=selected_address_id).first()
        if not selected_address:
            return JsonResponse({'error': 'Please choose a valid delivery address.'}, status=400)
        service_area_error = _service_area_error(
            selected_address.city, selected_address.country,
        )
        if service_area_error:
            return JsonResponse({'error': service_area_error}, status=409)
        profile, _ = Profile.objects.get_or_create(user=request.user)
        profile.shipping_address = selected_address
        profile.save(update_fields=['shipping_address', 'updated_at'])

    if not settings.ONLINE_PAYMENT_ENABLED:
        return JsonResponse({'error': 'Online payments are coming soon.'}, status=503)

    cart_obj = get_object_or_404(Cart, user=request.user, is_paid=False)
    if not _cart_has_available_stock(cart_obj):
        return JsonResponse({'error': 'One or more products are no longer available in the requested quantity.'}, status=409)
    amount = int((cart_obj.get_cart_total_price_after_coupon() + _cart_delivery_fee(cart_obj)) * 100)

    if amount < 100:
        return JsonResponse(
            {'error': 'The minimum payment amount is 100 paise.'}, status=400)

    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        return JsonResponse(
            {'error': 'Razorpay credentials are not configured.'}, status=401)

    try:
        client = razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        # client.enable_retry(True)
        payment = client.order.create({
            'amount': amount,
            'currency': 'INR',
            'receipt': f'cart_{cart_obj.uid}',
            'payment_capture': 1,
        })
    except Exception as e:
        status_code = getattr(e, 'status_code', None)
        error_text = str(e).lower()

        if status_code == 401 or 'auth' in error_text:
            return JsonResponse(
                {'error': 'Razorpay authentication failed.'}, status=401)

        logger.exception("Razorpay order creation failed")
        return JsonResponse(
            {'error': 'Unable to create the Razorpay order.'}, status=500)

    cart_obj.razorpay_order_id = payment['id']
    cart_obj.save(update_fields=['razorpay_order_id', 'updated_at'])
    logger.info("Razorpay order created for cart %s", cart_obj.uid)
    return JsonResponse({
        'order_id': payment['id'],
        'amount': payment['amount'],
        'currency': payment['currency'],
    })


@require_POST
@login_required
def verify_payment(request):
    if not settings.ONLINE_PAYMENT_ENABLED:
        return JsonResponse({'error': 'Online payments are coming soon.'}, status=503)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)

    order_id = data.get('razorpay_order_id')
    payment_id = data.get('razorpay_payment_id')
    signature = data.get('razorpay_signature')
    selected_address_id = data.get('selected_address_id')
    delivery_latitude = data.get('delivery_latitude')
    delivery_longitude = data.get('delivery_longitude')
    delivery_accuracy = data.get('delivery_accuracy_meters')
    if not all((order_id, payment_id, signature)):
        return JsonResponse(
            {'error': 'Missing payment verification fields.'}, status=400)

    cart_obj = get_object_or_404(
        Cart, user=request.user, is_paid=False, razorpay_order_id=order_id)
    expected_signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        f'{order_id}|{payment_id}'.encode(),
        digestmod='sha256',
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        return JsonResponse({'error': 'Payment signature mismatch.'}, status=400)

    try:
        client = razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        payment = client.payment.fetch(payment_id)
    except Exception:
        logger.exception("Unable to fetch Razorpay payment %s", payment_id)
        return JsonResponse({'error': 'Unable to validate the payment.'}, status=502)

    expected_amount = int((cart_obj.get_cart_total_price_after_coupon() + _cart_delivery_fee(cart_obj)) * 100)
    if (
        payment.get('order_id') != order_id or
        payment.get('amount') != expected_amount or
        payment.get('currency') != 'INR' or
        payment.get('status') != 'captured'
    ):
        return JsonResponse({'error': 'Payment validation failed.'}, status=400)

    selected_address = ShippingAddress.objects.filter(
        user=request.user,
        uid=selected_address_id,
    ).first() if selected_address_id else None
    if not selected_address:
        try:
            client.payment.refund(payment_id, {})
        except Exception:
            logger.exception('Unable to refund payment %s without a shipping address', payment_id)
        return JsonResponse({'error': 'Please choose a valid delivery address.'}, status=409)

    delivery_option, delivery_distance = _automatic_delivery_selection(
        selected_address.city.name,
        selected_address.zip_code,
        delivery_latitude,
        delivery_longitude,
        delivery_accuracy,
        cart_obj.get_cart_total_price_after_coupon(),
    )

    profile, _ = Profile.objects.get_or_create(user=request.user)
    profile.shipping_address = selected_address
    profile.save(update_fields=['shipping_address', 'updated_at'])

    try:
        order = create_order(
            cart_obj,
            shipping_address=selected_address,
            delivery_option=delivery_option,
            delivery_distance_km=delivery_distance,
            razorpay_payment_id=payment_id,
        )
    except ValueError as error:
        logger.warning("Payment %s could not reserve inventory: %s", payment_id, error)
        try:
            client.payment.refund(payment_id, {})
        except Exception:
            logger.exception("Unable to refund payment %s after inventory failure", payment_id)
        return JsonResponse({'error': str(error)}, status=409)

    cart_obj.razorpay_payment_id = payment_id
    cart_obj.razorpay_payment_signature = signature
    cart_obj.is_paid = True
    cart_obj.save(update_fields=[
        'razorpay_payment_id', 'razorpay_payment_signature', 'is_paid', 'updated_at'])
    try:
        send_order_confirmation_email(order)
    except Exception as error:
        logger.exception("Order email failed for order %s", order.order_id)
    try:
        send_admin_new_order_email(order)
    except Exception:
        logger.exception("Seller order notification failed for order %s", order.order_id)
    return JsonResponse({
        'success': True,
        'redirect_url': f'{reverse("success")}?order_id={order.order_id}',
    })


@require_POST
def place_cod_order(request):
    cart_obj = get_object_or_404(Cart, **cart_owner_filter(request), is_paid=False)

    if not cart_obj.cart_items.exists() and not cart_obj.bundle_items.exists():
        return JsonResponse({'error': 'Your cart is empty.'}, status=400)

    if not request.user.is_authenticated:
        return JsonResponse({
            'error': 'Please enter your shipping address before placing your order.',
            'shipping_address_url': (
                f'{reverse("guest-checkout")}?' +
                urlencode({'next': reverse('cart')})
            ),
        }, status=400)

    payload = {}
    if request.content_type == 'application/json':
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = request.POST
    selected_address_id = payload.get('selected_address_id')
    delivery_latitude = payload.get('delivery_latitude')
    delivery_longitude = payload.get('delivery_longitude')
    delivery_accuracy = payload.get('delivery_accuracy_meters')

    profile = Profile.objects.filter(user=request.user).first()
    shipping_address = profile.shipping_address if profile else None
    if selected_address_id:
        selected_address = ShippingAddress.objects.filter(user=request.user, uid=selected_address_id).first()
        if not selected_address:
            return JsonResponse({'error': 'Please choose a valid delivery address.'}, status=400)
        shipping_address = selected_address
        if profile:
            profile.shipping_address = selected_address
            profile.save(update_fields=['shipping_address', 'updated_at'])

    addresses = ShippingAddress.objects.filter(user=request.user).order_by('-is_default', '-created_at')
    if addresses.count() > 1 and not selected_address_id:
        return JsonResponse({'error': 'Please choose a delivery address.'}, status=400)

    if not shipping_address:
        return JsonResponse(
            {
                'error': 'Please add a shipping address before placing your order.',
                'shipping_address_url': (
                    f'{reverse("shipping-address")}?' +
                    urlencode({'next': reverse('cart')})
                ),
            },
            status=400,
        )

    service_area_error = _service_area_error(
        shipping_address.city, shipping_address.country,
    )
    if service_area_error:
        return JsonResponse({'error': service_area_error}, status=409)

    delivery_option, delivery_distance = _automatic_delivery_selection(
        shipping_address.city.name,
        shipping_address.zip_code,
        delivery_latitude,
        delivery_longitude,
        delivery_accuracy,
        cart_obj.get_cart_total_price_after_coupon(),
    )

    try:
        order = create_order(
            cart_obj,
            order_id=generate_order_id(),
            payment_status='Pending',
            payment_mode='Cash on Delivery',
            shipping_address=shipping_address,
            delivery_option=delivery_option,
            delivery_distance_km=delivery_distance,
        )
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=409)
    cart_obj.is_paid = True
    cart_obj.save(update_fields=['is_paid', 'updated_at'])
    try:
        send_order_confirmation_email(order)
    except Exception as error:
        logger.exception("Order email failed for order %s", order.order_id)
    try:
        send_admin_new_order_email(order)
    except Exception:
        logger.exception("Seller order notification failed for order %s", order.order_id)

    return JsonResponse({
        'success': True,
        'redirect_url': f'{reverse("success")}?order_id={order.order_id}',
    })


def guest_checkout(request):
    if request.user.is_authenticated:
        return redirect('cart')

    cart_obj = get_active_cart(request)
    if not cart_obj or (not cart_obj.cart_items.exists() and not cart_obj.bundle_items.exists()):
        return redirect('cart')
    coupon_enabled = (
        request.session.get('buy_now_checkout_cart_uid') == str(cart_obj.uid)
    )
    if request.method == 'POST' and (
        'apply_coupon' in request.POST or 'remove_checkout_coupon' in request.POST
    ):
        if not coupon_enabled:
            messages.error(request, 'Coupons can only be changed from the cart.')
            return redirect('guest-checkout')
        if 'remove_checkout_coupon' in request.POST:
            cart_obj.coupon = None
            cart_obj.save(update_fields=['coupon', 'updated_at'])
            messages.success(request, 'Coupon Removed.')
        else:
            _apply_coupon(request, cart_obj)
        return redirect('guest-checkout')

    next_url = request.GET.get('next') or request.POST.get('next') or reverse('cart')
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = reverse('cart')

    quote_key = 'guest_checkout_quote'
    quote_ready = False
    editing_address = request.GET.get('edit') == '1'
    quote_message = ''
    delivery_fee = None
    cart_total_after_delivery = None
    shipping_address_summary = ''

    if request.method == 'POST' and request.POST.get('confirm_order') == '1':
        quote = request.session.get(quote_key)
        if not quote or quote.get('cart_uid') != str(cart_obj.uid):
            request.session.pop(quote_key, None)
            form = GuestShippingAddressForm()
            form.add_error(
                None,
                'Please enter your shipping address to view the delivery charges first.',
            )
        else:
            form = GuestShippingAddressForm(quote['address'])
            if form.is_valid():
                current_cart_total = cart_obj.get_cart_total_price_after_coupon()
                delivery_fee = _cart_delivery_fee(cart_obj)
                cart_total_after_delivery = current_cart_total + delivery_fee
                if (
                    str(current_cart_total) != quote['cart_total'] or
                    str(delivery_fee) != quote['delivery_fee']
                ):
                    quote['cart_total'] = str(current_cart_total)
                    quote['delivery_fee'] = str(delivery_fee)
                    quote_ready = True
                    quote_message = 'Your cart or delivery charge changed. Review the updated total before placing your order.'
                else:
                    delivery_option, delivery_distance = _automatic_delivery_selection(
                        form.cleaned_data['city'].name,
                        form.cleaned_data['zip_code'],
                        quote.get('delivery_latitude'),
                        quote.get('delivery_longitude'),
                        quote.get('delivery_accuracy_meters'),
                        current_cart_total,
                    )
                    try:
                        guest_address = form.save(commit=False)
                        order = create_order(
                            cart_obj,
                            order_id=generate_order_id(),
                            payment_status='Pending',
                            payment_mode='Cash on Delivery',
                            shipping_address=guest_address,
                            delivery_pincode=guest_address.zip_code,
                            guest_access_token=uuid.uuid4().hex,
                            guest_name=(
                                f'{form.cleaned_data["first_name"]} '
                                f'{form.cleaned_data["last_name"]}'
                            ).strip(),
                            guest_email=form.cleaned_data['email'],
                            delivery_option=delivery_option,
                            delivery_distance_km=delivery_distance,
                        )
                    except ValueError as error:
                        form.add_error(None, str(error))
                        quote_ready = True
                    else:
                        cart_obj.is_paid = True
                        cart_obj.session_key = None
                        cart_obj.save(update_fields=['is_paid', 'session_key', 'updated_at'])
                        try:
                            send_order_confirmation_email(order)
                        except Exception:
                            logger.exception("Guest order email failed for order %s", order.order_id)
                        try:
                            send_admin_new_order_email(order)
                        except Exception:
                            logger.exception("Seller order notification failed for order %s", order.order_id)
                        request.session.flush()
                        return redirect(
                            f'{reverse("success")}?order_id={order.order_id}'
                            f'&guest_token={order.guest_access_token}'
                        )
                if quote_ready:
                    request.session[quote_key] = quote
            else:
                request.session.pop(quote_key, None)
    elif request.method == 'POST':
        form = GuestShippingAddressForm(request.POST)
        if form.is_valid() and not _service_area_error(
            form.cleaned_data['city'], form.cleaned_data['country'],
        ):
            address = {
                field: form.cleaned_data[field]
                for field in (
                    'first_name', 'last_name', 'street', 'street_number',
                    'zip_code', 'phone', 'email',
                )
            }
            address['city'] = str(form.cleaned_data['city'].pk)
            address['country'] = form.cleaned_data['country']
            address['state'] = str(form.cleaned_data['state'].pk)
            current_cart_total = cart_obj.get_cart_total_price_after_coupon()
            delivery_fee = _cart_delivery_fee(cart_obj)
            cart_total_after_delivery = current_cart_total + delivery_fee
            quote = {
                'cart_uid': str(cart_obj.uid),
                'address': address,
                'cart_total': str(current_cart_total),
                'delivery_fee': str(delivery_fee),
                'delivery_latitude': request.POST.get('delivery_latitude'),
                'delivery_longitude': request.POST.get('delivery_longitude'),
                'delivery_accuracy_meters': request.POST.get('delivery_accuracy_meters'),
            }
            request.session[quote_key] = quote
            quote_ready = True
        elif form.is_valid():
            form.add_error(None, _service_area_error(
                form.cleaned_data['city'], form.cleaned_data['country'],
            ))
    else:
        quote = request.session.get(quote_key)
        if quote and quote.get('cart_uid') == str(cart_obj.uid):
            form = GuestShippingAddressForm(quote['address'])
            if form.is_valid() and not editing_address:
                delivery_fee = _cart_delivery_fee(cart_obj)
                cart_total_after_delivery = (
                    cart_obj.get_cart_total_price_after_coupon() + delivery_fee
                )
                quote_ready = True
        else:
            request.session.pop(quote_key, None)
            form = GuestShippingAddressForm()

    if quote_ready:
        address = form.cleaned_data
        country_name = dict(form.fields['country'].choices).get(
            address['country'], address['country'],
        )
        shipping_address_summary = {
            'name': f"{address['first_name']} {address['last_name']}",
            'street': f"{address['street']}, {address['street_number']}",
            'locality': (
                f"{address['city'].name}, {address['state'].name} {address['zip_code']}"
            ),
            'country': country_name,
            'phone': address['phone'],
            'email': address['email'],
        }

    return render(request, 'accounts/guest_checkout.html', {
        'form': form,
        'next_url': next_url,
        'quote_ready': quote_ready,
        'editing_address': editing_address,
        'quote_message': quote_message,
        'delivery_fee': delivery_fee,
        'cart_total_after_delivery': cart_total_after_delivery,
        'shipping_address_summary': shipping_address_summary,
        'cart': cart_obj,
        'coupon_enabled': coupon_enabled,
        'available_coupons': Coupon.objects.filter(
            is_expired=False,
        ).order_by('minimum_amount', 'coupon_code') if coupon_enabled else (),
        **_cart_pricing_context(cart_obj),
    })


@require_POST
def update_cart_item(request):
    cart_item_id = None
    try:
        data = json.loads(request.body)
        cart_item_id = data.get("cart_item_id")
        quantity = int(data.get("quantity"))
        if quantity < 0:
            return JsonResponse({"success": False, "error": "Quantity cannot be negative."}, status=400)

        cart = get_active_cart(request)
        cart_item = CartItem.objects.get(
            uid=cart_item_id, cart=cart, cart__is_paid=False)
        if quantity == 0:
            cart_item.delete()
            return JsonResponse({
                "success": True,
                "removed": True,
                "quantity": 0,
                "cart_count": CartItem.objects.filter(cart=cart, cart__is_paid=False).count(),
            })
        if not cart_item.product or cart_item.product.stock_quantity < 1:
            return JsonResponse({"success": False, "error": "This product is out of stock."}, status=400)
        if (
            quantity > cart_item.product.stock_quantity
            and quantity >= cart_item.quantity
        ):
            available = cart_item.product.stock_quantity
            return JsonResponse({
                "success": False,
                "error": f"Only {available} available. Choose a lower quantity.",
                "available_quantity": available,
            }, status=409)
        cart_item.quantity = quantity
        cart_item.save()

        return JsonResponse({
            "success": True,
            "removed": False,
            "quantity": cart_item.quantity,
            "cart_count": CartItem.objects.filter(cart=cart, cart__is_paid=False).count(),
        })
    except Exception as e:
        logger.exception("Cart item update failed for item %s", cart_item_id)
        return JsonResponse({"success": False, "error": "Unable to update cart item."})


@require_POST
def remove_cart(request, uid):
    try:
        cart_item = get_object_or_404(CartItem, uid=uid, cart=get_active_cart(request))
        cart_item.delete()
        messages.success(request, 'Item removed from cart.')

    except Exception as e:
        logger.exception("Cart item removal failed for item %s", uid)
        messages.error(request, 'Error removing item from cart.')

    return HttpResponseRedirect(request.META.get('HTTP_REFERER'))


@require_POST
def remove_bundle(request, uid):
    bundle_item = get_object_or_404(BundleCartItem, uid=uid, cart=get_active_cart(request), cart__is_paid=False)
    bundle_item.delete()
    messages.success(request, 'Bundle removed from cart.')
    return HttpResponseRedirect(request.META.get('HTTP_REFERER'))


@require_POST
def remove_coupon(request, cart_id):
    cart = get_object_or_404(Cart, uid=cart_id, is_paid=False)
    if cart != get_active_cart(request):
        return HttpResponse('Forbidden', status=403)
    cart.coupon = None
    cart.save()

    messages.success(request, 'Coupon Removed.')
    return redirect('cart')


def success(request):
    order_id = request.GET.get('order_id')
    if request.user.is_authenticated:
        order = get_object_or_404(Order, order_id=order_id, user=request.user)
    else:
        order = get_object_or_404(
            Order,
            order_id=order_id,
            user__isnull=True,
            guest_access_token=request.GET.get('guest_token'),
        )

    context = {'order_id': order_id, 'order': order}
    return render(request, 'payment_success/payment_success.html', context)


# HTML to PDF Conversion
def render_to_pdf(template_src, context_dict={}):
    template = get_template(template_src)
    html = template.render(context_dict)

    static_root = settings.STATIC_ROOT
    css_files = [
        os.path.join(static_root, 'css', 'bootstrap.css'),
        os.path.join(static_root, 'css', 'responsive.css'),
        os.path.join(static_root, 'css', 'ui.css'),
    ]
    css_objects = [CSS(filename=css_file) for css_file in css_files]
    pdf_file = HTML(string=html).write_pdf(stylesheets=css_objects)

    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="invoice_{context_dict["order"].order_id}.pdf"'
    return response


@login_required
def download_invoice(request, order_id):
    order = get_object_or_404(Order, order_id=order_id, user=request.user)
    order_items = order.order_items.all()

    context = {
        'order': order,
        'order_items': order_items,
    }

    pdf = render_to_pdf('accounts/order_pdf_generate.html', context)
    if pdf:
        return pdf
    return HttpResponse("Error generating PDF", status=400)


@login_required
def profile_view(request, username):
    user_name = get_object_or_404(User, username=username)
    user = request.user
    profile = user.profile

    user_form = UserUpdateForm(instance=user)
    profile_form = UserProfileForm(instance=profile)

    if request.method == 'POST':
        user_form = UserUpdateForm(request.POST, instance=user)
        profile_form = UserProfileForm(
            request.POST, request.FILES, instance=profile)
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            messages.success(
                request, 'Your profile has been updated successfully!')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

    context = {
        'user_name': user_name,
        'user_form': user_form,
        'profile_form': profile_form
    }

    return render(request, 'accounts/profile.html', context)


@login_required
def change_password(request):
    if request.method == 'POST':
        form = CustomPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # Important!
            messages.success(
                request, 'Your password was successfully updated!')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))
        else:
            messages.error(request, 'Please correct the error below.')
    else:
        form = CustomPasswordChangeForm(request.user)
    return render(request, 'accounts/change_password.html', {'form': form})


@login_required
def update_shipping_address(request):
    next_url = request.GET.get('next') or request.POST.get('next')
    if next_url and not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = None

    saved_addresses = ShippingAddress.objects.filter(user=request.user).order_by('-is_default', '-created_at')
    address_id = request.GET.get('address_id')
    create_new = request.GET.get('new') == '1'

    selected_address = None
    if address_id:
        selected_address = saved_addresses.filter(uid=address_id).first()
    elif not create_new:
        selected_address = saved_addresses.filter(is_default=True).first() or saved_addresses.filter(current_address=True).first() or saved_addresses.first()

    if request.method == 'POST':
        form = ShippingAddressForm(request.POST, instance=request.POST.get('address_id') and saved_addresses.filter(uid=request.POST.get('address_id')).first())
        if form.is_valid():
            shipping_address = form.save(commit=False)
            shipping_address.user = request.user
            shipping_address.is_default = form.cleaned_data.get('is_default', False) or not ShippingAddress.objects.filter(user=request.user).exists()
            shipping_address.current_address = shipping_address.is_default
            shipping_address.save()
            if shipping_address.is_default:
                ShippingAddress.objects.filter(user=request.user).exclude(uid=shipping_address.uid).update(is_default=False, current_address=False)
            profile, _ = Profile.objects.get_or_create(user=request.user)
            profile.shipping_address = shipping_address
            profile.save(update_fields=['shipping_address', 'updated_at'])

            messages.success(
                request, "The Address Has Been Successfully Saved!")

            if next_url:
                return redirect(next_url)
            return redirect(f"{reverse('shipping-address')}?address_id={shipping_address.uid}")
        else:
            form = ShippingAddressForm(request.POST, instance=selected_address)
    else:
        form = ShippingAddressForm(instance=selected_address if not create_new else None)

    return render(
        request,
        'accounts/shipping_address_form.html',
        {
            'form': form,
            'next_url': next_url,
            'saved_addresses': saved_addresses,
            'selected_address': selected_address,
            'create_new': create_new,
        },
    )


@require_POST
@login_required
def delete_shipping_address(request, address_uid):
    address = get_object_or_404(ShippingAddress, uid=address_uid, user=request.user)

    if address.is_default:
        messages.error(request, 'Default address cannot be deleted. Please set another address as default first.')
        return redirect('shipping-address')

    address.delete()
    messages.success(request, 'Address deleted successfully.')
    return redirect('shipping-address')


# Order history view
@login_required
def order_history(request):
    orders = Order.objects.filter(user=request.user).order_by('-order_date')
    return render(request, 'accounts/order_history.html', {'orders': orders})


def track_order(request, order_id, access_token):
    order = get_object_or_404(
        Order.objects.prefetch_related('order_items__product', 'bundle_items'),
        order_id=order_id,
        guest_access_token=access_token,
    )
    return render(request, 'accounts/order_tracking.html', {'order': order})


def return_request(request, order_id, access_token=None):
    if access_token:
        order = get_object_or_404(
            Order,
            order_id=order_id,
            user__isnull=True,
            guest_access_token=access_token,
        )
    elif request.user.is_authenticated:
        order = get_object_or_404(
            Order, order_id=order_id, user=request.user)
    else:
        return redirect('login')

    current_request = ReturnRequest.objects.filter(order=order).first()
    form = ReturnRequestForm(order=order)
    eligibility_errors = {
        ReturnRequest.RequestType.RETURN: return_eligibility_error(order),
        ReturnRequest.RequestType.REPLACEMENT: return_eligibility_error(
            order, ReturnRequest.RequestType.REPLACEMENT),
    }
    available_request_types = tuple(
        choice for choice in ReturnRequest.RequestType.choices
        if not eligibility_errors[choice[0]]
    )
    default_request_type = (
        available_request_types[0][0]
        if len(available_request_types) == 1
        else ''
    )
    if default_request_type:
        form.initial['request_type'] = default_request_type

    if len(available_request_types) == 1:
        request_form_heading = (
            f"Start a {available_request_types[0][1].lower()}"
        )
    elif available_request_types:
        request_form_heading = 'Start a return or replacement'
    else:
        request_form_heading = 'Return or replacement unavailable'
    form.fields['request_type'].choices = available_request_types

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'request':
            if current_request:
                messages.error(
                    request,
                    'A return or replacement request already exists for this order.',
                )
            else:
                form = ReturnRequestForm(request.POST, order=order)
                form.fields['request_type'].choices = available_request_types
                request_type = request.POST.get(
                    'request_type', ReturnRequest.RequestType.RETURN)
                eligibility_error = eligibility_errors.get(
                    request_type, 'Choose a valid request type.')
                if eligibility_error:
                    messages.error(request, eligibility_error)
                elif not form.is_valid():
                    messages.error(request, 'Please correct the request details below.')
                elif (
                    request_type == ReturnRequest.RequestType.REPLACEMENT
                    and not request.FILES.getlist('request_images')
                ):
                    image_error = (
                        'Upload at least 1 clear product photo for a replacement request.'
                    )
                    messages.error(request, image_error)
                    form.add_error(None, image_error)
                elif len(request.FILES.getlist('request_images')) > 5:
                    image_error = 'Upload no more than 5 product photos.'
                    messages.error(request, image_error)
                    form.add_error(None, image_error)
                elif any(
                    image.size > 5 * 1024 * 1024
                    for image in request.FILES.getlist('request_images')
                ):
                    image_error = 'Each photo must be 5 MB or smaller.'
                    messages.error(request, image_error)
                    form.add_error(None, image_error)
                else:
                    request_images = request.FILES.getlist('request_images')
                    image_field = ImageField()
                    try:
                        validated_images = [
                            image_field.clean(image) for image in request_images
                        ]
                    except ValidationError:
                        messages.error(
                            request,
                            'Upload valid image files in JPG, PNG, or another supported image format.',
                        )
                        form.add_error(
                            None,
                            'Upload valid image files in JPG, PNG, or another supported image format.',
                        )
                    else:
                        with transaction.atomic():
                            current_request = form.save(commit=False)
                            current_request.order = order
                            current_request.save()
                            for image in validated_images:
                                ReturnRequestImage.objects.create(
                                    return_request=current_request,
                                    image=image,
                                )
                        try:
                            send_admin_return_request_email(current_request)
                        except Exception:
                            logger.exception(
                                'Return/replacement request notification failed for order %s',
                                order.order_id,
                            )
                            messages.error(
                                request,
                                'Your request was saved, but the seller notification could not be sent. Please contact support and quote your order number.',
                            )
                        else:
                            request_label = (
                                'replacement' if request_type == ReturnRequest.RequestType.REPLACEMENT
                                else 'return'
                            )
                            messages.success(
                                request,
                                f'Your {request_label} request has been submitted for seller review.',
                            )
                        route_name = (
                            'return_request_guest' if access_token else 'return_request'
                        )
                        return redirect(
                            route_name,
                            **(
                                {'order_id': order.order_id, 'access_token': access_token}
                                if access_token
                                else {'order_id': order.order_id}
                            ),
                        )
        elif action == 'shipment':
            current_request = get_object_or_404(
                ReturnRequest, order=order)
            if (
                current_request.request_type != ReturnRequest.RequestType.RETURN
                or current_request.status != ReturnRequest.Status.APPROVED
            ):
                messages.error(
                    request,
                    'Shipment details can only be submitted after the return is approved.',
                )
            else:
                courier_partner_name = (
                    request.POST.get('courier_partner_name') or ''
                ).strip()
                tracking_number = (request.POST.get('tracking_number') or '').strip()
                images = request.FILES.getlist('shipment_images')
                image_field = ImageField()
                if not courier_partner_name:
                    messages.error(request, 'Enter the courier partner name.')
                elif len(courier_partner_name) > 120:
                    messages.error(request, 'The courier partner name is too long.')
                elif not tracking_number:
                    messages.error(request, 'Enter the shipment tracking number.')
                elif len(tracking_number) > 120:
                    messages.error(request, 'The tracking number is too long.')
                elif not 1 <= len(images) <= 5:
                    messages.error(
                        request, 'Upload between 1 and 5 shipment photos.')
                elif any(image.size > 5 * 1024 * 1024 for image in images):
                    messages.error(
                        request, 'Each shipment photo must be 5 MB or smaller.')
                else:
                    try:
                        validated_images = [
                            image_field.clean(image) for image in images
                        ]
                    except ValidationError:
                        messages.error(
                            request,
                            'Upload valid image files in JPG, PNG, or another supported image format.',
                        )
                    else:
                        with transaction.atomic():
                            for image in validated_images:
                                ReturnShipmentImage.objects.create(
                                    return_request=current_request,
                                    image=image,
                                )
                            current_request.courier_partner_name = (
                                courier_partner_name
                            )
                            current_request.tracking_number = tracking_number
                            current_request.shipped_at = timezone.now()
                            current_request.status = ReturnRequest.Status.SHIPPED
                            current_request.save(update_fields=[
                                'courier_partner_name', 'tracking_number',
                                'shipped_at', 'status', 'updated_at',
                            ])
                        try:
                            send_admin_return_shipment_email(current_request)
                        except Exception:
                            logger.exception(
                                'Return shipment notification failed for order %s',
                                order.order_id,
                            )
                        messages.success(
                            request,
                            'Your shipment details and photos have been submitted.',
                        )
                        route_name = (
                            'return_request_guest' if access_token else 'return_request'
                        )
                        return redirect(
                            route_name,
                            **(
                                {'order_id': order.order_id, 'access_token': access_token}
                                if access_token
                                else {'order_id': order.order_id}
                            ),
                        )

    if current_request:
        current_request = ReturnRequest.objects.prefetch_related(
            'shipment_images').get(pk=current_request.pk)
    policy = ReturnRefundPolicy.get_solo()
    return render(request, 'accounts/return_request.html', {
        'order': order,
        'order_items': order.order_items.select_related(
            'product', 'size_variant', 'color_variant',
        ),
        'bundle_items': order.bundle_items.prefetch_related(
            'selected_products__product',
        ),
        'return_request': current_request,
        'return_timeline': (
            return_request_timeline(current_request)
            if current_request else []
        ),
        'form': form,
        'default_request_type': default_request_type,
        'request_form_heading': request_form_heading,
        'return_eligibility_error': eligibility_errors[
            ReturnRequest.RequestType.RETURN],
        'replacement_eligibility_error': eligibility_errors[
            ReturnRequest.RequestType.REPLACEMENT],
        'return_shipping_address': policy.return_shipping_address,
        'access_token': access_token,
        'is_cod_order': order.payment_mode == 'Cash on Delivery',
    })


# Create an order view
def create_order(
    cart, order_id=None, payment_status='Paid', payment_mode='Razorpay',
    shipping_address=None, delivery_pincode='', guest_access_token=None,
    guest_name='', guest_email='', delivery_option='', delivery_distance_km=None,
    razorpay_payment_id='',
):
    with transaction.atomic():
        cart_items = list(CartItem.objects.select_related(
            'product', 'size_variant', 'color_variant').filter(cart=cart))
        bundle_items = list(BundleCartItem.objects.select_related(
            'bundle_offer').prefetch_related('selected_products__product').filter(cart=cart))
        quantities = Counter()
        for cart_item in cart_items:
            if cart_item.product_id:
                quantities[cart_item.product_id] += cart_item.quantity
        for bundle_item in bundle_items:
            for selected in bundle_item.selected_products.all():
                quantities[selected.product_id] += bundle_item.quantity

        products = {
            product.uid: product
            for product in Product.objects.select_for_update().filter(uid__in=quantities)
        }
        if any(not cart_item.product_id for cart_item in cart_items) or any(
            product_id not in products
            or products[product_id].stock_quantity < quantity
            for product_id, quantity in quantities.items()
        ):
            raise ValueError('One or more products are no longer available in the requested quantity.')

        selected_address = shipping_address or (
            getattr(cart.user.profile, 'shipping_address', None) if cart.user else None
        )
        if selected_address:
            service_area_error = _service_area_error(
                selected_address.city, selected_address.country,
            )
            if service_area_error:
                raise ValueError(service_area_error)
        delivery_pincode = (
            delivery_pincode or getattr(selected_address, 'zip_code', '')
        ).strip()
        is_serviceable = ServiceablePincode.objects.filter(
            pincode=delivery_pincode, is_active=True).exists()
        delivery_fee = _cart_delivery_fee(cart)
        order, created = Order.objects.get_or_create(
            user=cart.user,
            order_id=order_id or generate_order_id(),
            guest_access_token=guest_access_token or uuid.uuid4().hex,
            guest_name=guest_name,
            guest_email=guest_email,
            status=Order.Status.ACCEPTED,
            delivery_pincode=delivery_pincode,
            outside_service_area=not is_serviceable,
            delivery_option=delivery_option,
            delivery_distance_km=delivery_distance_km,
            additional_delivery_fee=delivery_fee or None,
            payment_status=payment_status,
            razorpay_payment_id=razorpay_payment_id,
            shipping_address=str(selected_address) if selected_address else '',
            payment_mode=payment_mode,
            order_total_price=cart.get_cart_total(),
            coupon=cart.coupon,
            grand_total=cart.get_cart_total_price_after_coupon() + delivery_fee,
        )

        if created:
            for cart_item in cart_items:
                product = products[cart_item.product_id]
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    size_variant=cart_item.size_variant,
                    color_variant=cart_item.color_variant,
                    quantity=cart_item.quantity,
                    product_price=cart_item.get_product_price(),
                    gift_wrap=cart_item.gift_wrap,
                )
            for bundle_item in bundle_items:
                order_bundle = BundleOrderItem.objects.create(
                    order=order,
                    bundle_offer=bundle_item.bundle_offer,
                    title=bundle_item.bundle_offer.title if bundle_item.bundle_offer else 'Custom gift bundle',
                    quantity=bundle_item.quantity,
                    original_unit_price=bundle_item.original_unit_price,
                    unit_price=bundle_item.unit_price,
                    packaging_option=bundle_item.packaging_option,
                    packaging_name=bundle_item.packaging_option.name if bundle_item.packaging_option else '',
                    packaging_price=bundle_item.packaging_price,
                )
                BundleOrderProduct.objects.bulk_create([
                    BundleOrderProduct(bundle_item=order_bundle, product=selected.product)
                    for selected in bundle_item.selected_products.all()
                ])

            for product_id, quantity in quantities.items():
                product = products[product_id]
                product.stock_quantity -= quantity
                product.save(update_fields=['stock_quantity', 'updated_at'])

        return order


# Order Details view
@login_required
def order_details(request, order_id):
    order = get_object_or_404(Order, order_id=order_id, user=request.user)
    if not order.guest_access_token:
        order.guest_access_token = uuid.uuid4().hex
        order.save(update_fields=['guest_access_token', 'updated_at'])
    order_items = OrderItem.objects.filter(order=order).select_related(
        'product', 'size_variant', 'color_variant').prefetch_related(
            'product__product_images')
    bundle_items = order.bundle_items.prefetch_related(
        'selected_products__product__product_images')
    review_products = {}
    for item in order_items:
        if item.product_id:
            review_products.setdefault(item.product_id, item.product)
    for bundle_item in bundle_items:
        for selected_product in bundle_item.selected_products.all():
            review_products.setdefault(
                selected_product.product_id, selected_product.product)
    existing_reviews = ProductReview.objects.filter(
        user=request.user,
        product_id__in=review_products,
    ).prefetch_related('photos')
    reviews_by_product = {
        review.product_id: review for review in existing_reviews
    }
    for product in review_products.values():
        product.review_image = next(iter(product.product_images.all()), None)
        product.order_review = reviews_by_product.get(product.pk)
        product.review_stars = (
            product.order_review.stars if product.order_review else 0
        )
        product.review_photo_count = (
            len(product.order_review.photos.all())
            if product.order_review else 0
        )
        product.review_photo_remaining = 5 - product.review_photo_count
    return_request_obj = ReturnRequest.objects.filter(order=order).first()
    context = {
        'order': order,
        'order_items': order_items,
        'bundle_items': bundle_items,
        'review_products': list(review_products.values()),
        'return_request': return_request_obj,
        'return_timeline': (
            return_request_timeline(return_request_obj)
            if return_request_obj else []
        ),
        'return_eligibility_error': return_eligibility_error(order),
        'replacement_eligibility_error': return_eligibility_error(
            order, ReturnRequest.RequestType.REPLACEMENT),
        'order_total_price': sum(item.get_total_price() for item in order_items),
        'coupon_discount': order.coupon.discount_amount if order.coupon else 0,
        'grand_total': order.get_order_total_price()
    }
    return render(request, 'accounts/order_details.html', context)


@login_required
@require_POST
def review_order_product(request, order_id, product_uid):
    order = get_object_or_404(
        Order, order_id=order_id, user=request.user)
    if order.status != Order.Status.DELIVERED:
        messages.error(request, 'You can review products after the order is delivered.')
        return redirect('order_details', order_id=order.order_id)

    product = get_object_or_404(Product, uid=product_uid)
    is_ordered_product = (
        OrderItem.objects.filter(order=order, product=product).exists()
        or BundleOrderProduct.objects.filter(
            bundle_item__order=order, product=product).exists()
    )
    if not is_ordered_product:
        raise Http404

    review = ProductReview.objects.filter(
        user=request.user, product=product).first()
    review_form = ReviewForm(
        request.POST,
        request.FILES,
        instance=review,
    )
    if not review_form.is_valid():
        errors = '; '.join(
            f'{field}: {", ".join(field_errors)}'
            for field, field_errors in review_form.errors.items()
        )
        messages.error(request, f'Your review could not be saved. {errors}')
        return redirect('order_details', order_id=order.order_id)

    photos = review_form.cleaned_data['photos']
    existing_photo_count = review.photos.count() if review else 0
    if existing_photo_count + len(photos) > 5:
        messages.error(
            request,
            'A review can have up to five photos in total. Remove some photos or choose fewer new ones.',
        )
        return redirect('order_details', order_id=order.order_id)

    with transaction.atomic():
        review = review_form.save(commit=False)
        review.product = product
        review.user = request.user
        review.save()
        for photo in photos:
            ReviewPhoto.objects.create(review=review, image=photo)

    messages.success(
        request,
        f'Your review for {product.product_name} has been saved.',
    )
    return redirect('order_details', order_id=order.order_id)


@require_POST
def cancel_order(request, order_id, access_token):
    if request.user.is_authenticated:
        order_filter = {'user': request.user}
    else:
        order_filter = {
            'user__isnull': True,
            'guest_access_token': access_token,
        }

    was_cancelled = False
    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            order_id=order_id,
            **order_filter,
        )
        if not order.can_be_cancelled:
            messages.error(
                request,
                'Orders can only be cancelled before dispatch.',
            )
        else:
            quantities = Counter()
            for product_id, quantity in order.order_items.values_list(
                'product_id', 'quantity'
            ):
                if product_id:
                    quantities[product_id] += quantity
            for product_id, quantity in order.bundle_items.values_list(
                'selected_products__product_id', 'quantity'
            ):
                if product_id:
                    quantities[product_id] += quantity

            products = Product.objects.select_for_update().filter(
                uid__in=quantities.keys())
            for product in products:
                product.stock_quantity += quantities[product.uid]
                product.save(update_fields=['stock_quantity', 'updated_at'])

            if (
                order.payment_mode != 'Cash on Delivery' and
                order.payment_status.strip().lower() == 'paid'
            ):
                order.payment_status = 'Refund pending'
            order.status = Order.Status.CANCELLED
            order._cancelled_by_customer = True
            order.save(update_fields=[
                'status', 'payment_status', 'updated_at',
            ])
            was_cancelled = True
            messages.success(
                request,
                'Your order has been cancelled. Any applicable refund will be reviewed by our team.',
            )

    if was_cancelled:
        try:
            send_order_cancelled_customer_email(order)
        except Exception:
            logger.exception(
                'Customer cancellation email failed for order %s', order.order_id)
        try:
            send_admin_order_cancelled_email(order)
        except Exception:
            logger.exception(
                'Seller cancellation email failed for order %s', order.order_id)

    if request.user.is_authenticated:
        return redirect('order_details', order_id=order.order_id)
    return redirect(
        'track_order',
        order_id=order.order_id,
        access_token=order.guest_access_token,
    )


# Delete user account feature
@login_required
def delete_account(request):
    if request.method == 'POST':
        user = request.user
        logout(request)
        user.delete()
        messages.success(
            request, "Your account has been deleted successfully.")
        return redirect('index')
