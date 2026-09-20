import os
import json
import uuid
import hmac
import logging
import hashlib
from urllib.parse import urlencode
import razorpay
from weasyprint import CSS, HTML
from products.models import *
from django.urls import reverse
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from home.models import ShippingAddress
from django.contrib.auth.models import User
from django.template.loader import get_template
from accounts.models import (
    Profile, Cart, CartItem, Order, OrderItem, BundleCartItem,
    BundleOrderItem, BundleOrderProduct, ServiceablePincode,
)
from base.emails import send_account_activation_email
from base.emails import send_order_confirmation_email, send_admin_new_order_email
from django.views.decorators.http import require_POST
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Q
from django.http import HttpResponseRedirect, HttpResponse
from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.shortcuts import redirect, render, get_object_or_404
from accounts.forms import (
    UserUpdateForm, UserProfileForm, ShippingAddressForm,
    GuestShippingAddressForm, CustomPasswordChangeForm,
)

logger = logging.getLogger(__name__)

LOGIN_FAILURE_LIMIT = 5
LOGIN_THROTTLE_SECONDS = 15 * 60


def generate_order_id():
    return f'BOG-{timezone.localdate():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}'


# Create your views here.


def login_page(request):
    next_url = request.GET.get('next')
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password')
        client_ip = request.META.get('REMOTE_ADDR', 'unknown')
        throttle_digest = hashlib.sha256(
            f'{client_ip}:{username.casefold()}'.encode()
        ).hexdigest()
        failure_key = f'login-failures:{throttle_digest}'
        lock_key = f'login-lock:{throttle_digest}'

        if cache.get(lock_key):
            messages.warning(
                request,
                'Too many unsuccessful attempts. Please try again later.',
            )
            return HttpResponseRedirect(request.path_info)

        user_obj = authenticate(request, username=username, password=password)
        if user_obj and getattr(user_obj.profile, 'is_email_verified', False):
            cache.delete(failure_key)
            cache.delete(lock_key)
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

        failures = cache.get(failure_key, 0) + 1
        cache.set(failure_key, failures, LOGIN_THROTTLE_SECONDS)
        if failures >= LOGIN_FAILURE_LIMIT:
            cache.set(lock_key, True, LOGIN_THROTTLE_SECONDS)
        messages.warning(request, 'Invalid username or password.')
        return HttpResponseRedirect(request.path_info)

    return render(request, 'accounts/login.html')


def register_page(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        email = request.POST.get('email')
        password = request.POST.get('password')

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

    return render(request, 'accounts/register.html')


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


@require_POST
def add_to_cart(request, uid):
    try:
        variant = request.POST.get('size') or request.GET.get('size')
        product = get_object_or_404(Product, uid=uid)
        try:
            quantity = max(1, int(request.POST.get('quantity') or request.GET.get('quantity', 1)))
        except (TypeError, ValueError):
            quantity = 1

        if product.stock_quantity < 1:
            messages.warning(request, 'This product is out of stock.')
            return redirect(reverse('get_product', kwargs={'slug': product.slug}))

        quantity = min(quantity, product.stock_quantity)
        gift_wrap = (request.POST.get('gift_wrap') or request.GET.get('gift_wrap')) == '1' and product.gift_wrap_available
        cart = get_active_cart(request, create=True)
        size_variant = get_object_or_404(
            SizeVariant, size_name=variant) if variant else None

        cart_item, created = CartItem.objects.get_or_create(
            cart=cart, product=product, size_variant=size_variant, gift_wrap=gift_wrap)
        if not created:
            cart_item.quantity = min(cart_item.quantity + quantity, product.stock_quantity)
            cart_item.save()
        else:
            cart_item.quantity = quantity
            cart_item.save(update_fields=['quantity'])

        messages.success(request, 'Item added to cart successfully.')

    except Exception as e:
        logger.exception("Add to cart failed for product %s", uid)
        messages.error(request, 'Error adding item to cart.', str(e))

    return redirect(reverse('cart'))


def cart(request):
    cart_obj = None
    payment = None
    cart_obj = get_active_cart(request)
    if not cart_obj:
        messages.warning(
            request, "Your cart is empty. Please add a product to cart.")
        return redirect(reverse('index'))

    if request.method == 'POST':
        coupon = request.POST.get('coupon')
        coupon_obj = Coupon.objects.filter(coupon_code__exact=coupon).first()

        if not coupon_obj:
            messages.warning(request, 'Invalid coupon code.')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

        if cart_obj and cart_obj.coupon:
            messages.warning(request, 'Coupon already exists.')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

        if coupon_obj and coupon_obj.is_expired:
            messages.warning(request, 'Coupon code expired.')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

        if cart_obj and coupon_obj and cart_obj.get_cart_total() < coupon_obj.minimum_amount:
            messages.warning(
                request, f'Amount should be greater than {coupon_obj.minimum_amount}')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

        if cart_obj and coupon_obj:
            cart_obj.coupon = coupon_obj
            cart_obj.save()
            messages.success(request, 'Coupon applied successfully.')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

    saved_addresses = []
    selected_address_id = None
    if request.user.is_authenticated:
        saved_addresses = list(ShippingAddress.objects.filter(user=request.user).order_by('-is_default', '-created_at'))
        default_address = next((address for address in saved_addresses if address.is_default), None)
        if default_address:
            selected_address_id = str(default_address.uid)
        elif saved_addresses:
            selected_address_id = str(saved_addresses[0].uid)

    context = {
        'cart': cart_obj,
        'payment': payment,
        'razorpay_key_id': settings.RAZORPAY_KEY_ID,
        'quantity_range': range(1, 6),
        'base_url': settings.BASE_URL,
        'saved_addresses': saved_addresses,
        'selected_address_id': selected_address_id,
    }
    return render(request, 'accounts/cart.html', context)


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
        if selected_address:
            profile, _ = Profile.objects.get_or_create(user=request.user)
            profile.shipping_address = selected_address
            profile.save(update_fields=['shipping_address', 'updated_at'])

    cart_obj = get_object_or_404(Cart, user=request.user, is_paid=False)
    if not _cart_has_available_stock(cart_obj):
        return JsonResponse({'error': 'One or more products are no longer available in the requested quantity.'}, status=409)
    amount = int(cart_obj.get_cart_total_price_after_coupon() * 100)

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
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)

    order_id = data.get('razorpay_order_id')
    payment_id = data.get('razorpay_payment_id')
    signature = data.get('razorpay_signature')
    selected_address_id = data.get('selected_address_id')
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

    expected_amount = int(cart_obj.get_cart_total_price_after_coupon() * 100)
    if (
        payment.get('order_id') != order_id or
        payment.get('amount') != expected_amount or
        payment.get('currency') != 'INR' or
        payment.get('status') != 'captured'
    ):
        return JsonResponse({'error': 'Payment validation failed.'}, status=400)

    if selected_address_id:
        selected_address = ShippingAddress.objects.filter(user=request.user, uid=selected_address_id).first()
        if selected_address:
            profile, _ = Profile.objects.get_or_create(user=request.user)
            profile.shipping_address = selected_address
            profile.save(update_fields=['shipping_address', 'updated_at'])

    try:
        order = create_order(cart_obj, shipping_address=(
            ShippingAddress.objects.filter(user=request.user, uid=selected_address_id).first()
            if selected_address_id else None
        ))
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
        logger.exception("Admin order notification failed for order %s", order.order_id)
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

    selected_address_id = None
    if request.content_type == 'application/json':
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            payload = {}
        selected_address_id = payload.get('selected_address_id')
    else:
        selected_address_id = request.POST.get('selected_address_id')

    profile = Profile.objects.filter(user=request.user).first()
    shipping_address = profile.shipping_address if profile else None
    if selected_address_id:
        selected_address = ShippingAddress.objects.filter(user=request.user, uid=selected_address_id).first()
        if selected_address:
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

    try:
        order = create_order(
            cart_obj,
            order_id=generate_order_id(),
            payment_status='Pending',
            payment_mode='Cash on Delivery',
            shipping_address=shipping_address,
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
        logger.exception("Admin order notification failed for order %s", order.order_id)

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

    next_url = request.GET.get('next') or request.POST.get('next') or reverse('cart')
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = reverse('cart')

    if request.method == 'POST':
        form = GuestShippingAddressForm(request.POST)
        if form.is_valid():
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
                    guest_name=f'{form.cleaned_data["first_name"]} {form.cleaned_data["last_name"]}'.strip(),
                    guest_email=form.cleaned_data['email'],
                )
            except ValueError as error:
                form.add_error(None, str(error))
                return render(request, 'accounts/guest_checkout.html', {'form': form, 'next_url': next_url})
            cart_obj.is_paid = True
            cart_obj.session_key = None
            cart_obj.save(update_fields=['is_paid', 'session_key', 'updated_at'])
            try:
                send_order_confirmation_email(order)
            except Exception as error:
                logger.exception("Guest order email failed for order %s", order.order_id)
            try:
                send_admin_new_order_email(order)
            except Exception:
                logger.exception("Admin order notification failed for order %s", order.order_id)
            request.session.flush()
            return redirect(
                f'{reverse("success")}?order_id={order.order_id}'
                f'&guest_token={order.guest_access_token}'
            )
    else:
        form = GuestShippingAddressForm()

    return render(request, 'accounts/guest_checkout.html', {
        'form': form,
        'next_url': next_url,
    })


@require_POST
def update_cart_item(request):
    try:
        data = json.loads(request.body)
        cart_item_id = data.get("cart_item_id")
        quantity = int(data.get("quantity"))

        cart = get_active_cart(request)
        cart_item = CartItem.objects.get(
            uid=cart_item_id, cart=cart, cart__is_paid=False)
        if not cart_item.product or cart_item.product.stock_quantity < 1:
            return JsonResponse({"success": False, "error": "This product is out of stock."}, status=400)
        cart_item.quantity = max(1, min(quantity, cart_item.product.stock_quantity))
        cart_item.save()

        return JsonResponse({"success": True})
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
        messages.warning(request, 'Error removing item from cart.')

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
    return HttpResponseRedirect(request.META.get('HTTP_REFERER'))


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
            messages.warning(request, 'Please correct the error below.')
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


# Create an order view
def create_order(
    cart, order_id=None, payment_status='Paid', payment_mode='Razorpay',
    shipping_address=None, delivery_pincode='', guest_access_token=None,
    guest_name='', guest_email='',
):
    with transaction.atomic():
        cart_items = list(CartItem.objects.select_related(
            'product', 'size_variant', 'color_variant').filter(cart=cart))
        bundle_items = list(BundleCartItem.objects.select_related(
            'bundle_offer').prefetch_related('selected_products__product').filter(cart=cart))
        products = {
            product.uid: product
            for product in Product.objects.select_for_update().filter(
                uid__in={item.product_id for item in cart_items if item.product_id}
            )
        }
        for cart_item in cart_items:
            product = products.get(cart_item.product_id)
            if not product or product.stock_quantity < cart_item.quantity:
                raise ValueError('One or more products are no longer available in the requested quantity.')
        bundle_products = {}
        for bundle_item in bundle_items:
            for selected in bundle_item.selected_products.all():
                product = Product.objects.select_for_update().get(uid=selected.product_id)
                bundle_products[selected.product_id] = product
                if product.stock_quantity < bundle_item.quantity:
                    raise ValueError('One or more products are no longer available in the requested quantity.')

        selected_address = shipping_address or (
            getattr(cart.user.profile, 'shipping_address', None) if cart.user else None
        )
        delivery_pincode = (
            delivery_pincode or getattr(selected_address, 'zip_code', '')
        ).strip()
        is_serviceable = ServiceablePincode.objects.filter(
            pincode=delivery_pincode, is_active=True).exists()
        order, created = Order.objects.get_or_create(
            user=cart.user,
            order_id=order_id or generate_order_id(),
            guest_access_token=guest_access_token or uuid.uuid4().hex,
            guest_name=guest_name,
            guest_email=guest_email,
            status=(
                Order.Status.ACCEPTED
                if is_serviceable else Order.Status.PENDING_REVIEW
            ),
            delivery_pincode=delivery_pincode,
            outside_service_area=not is_serviceable,
            payment_status=payment_status,
            shipping_address=str(selected_address) if selected_address else '',
            payment_mode=payment_mode,
            order_total_price=cart.get_cart_total(),
            coupon=cart.coupon,
            grand_total=cart.get_cart_total_price_after_coupon(),
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
                product.stock_quantity -= cart_item.quantity
                product.save(update_fields=['stock_quantity', 'updated_at'])
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
                for selected in bundle_item.selected_products.all():
                    product = bundle_products[selected.product_id]
                    product.stock_quantity -= bundle_item.quantity
                    product.save(update_fields=['stock_quantity', 'updated_at'])

        return order


# Order Details view
@login_required
def order_details(request, order_id):
    order = get_object_or_404(Order, order_id=order_id, user=request.user)
    if not order.guest_access_token:
        order.guest_access_token = uuid.uuid4().hex
        order.save(update_fields=['guest_access_token', 'updated_at'])
    order_items = OrderItem.objects.filter(order=order)
    bundle_items = order.bundle_items.prefetch_related('selected_products__product')
    context = {
        'order': order,
        'order_items': order_items,
        'bundle_items': bundle_items,
        'order_total_price': sum(item.get_total_price() for item in order_items),
        'coupon_discount': order.coupon.discount_amount if order.coupon else 0,
        'grand_total': order.get_order_total_price()
    }
    return render(request, 'accounts/order_details.html', context)


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
