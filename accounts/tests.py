import json
from io import BytesIO
from datetime import timedelta
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from accounts.models import (
    BundleOrderItem, BundleOrderProduct, Cart, CartItem, Order, OrderItem,
    ServiceablePincode, ReturnRequest, ReplacementReason,
)
from accounts.forms import (
    GuestShippingAddressForm, RegistrationForm, ShippingAddressForm,
    UserProfileForm, UserUpdateForm,
)
from accounts.views import create_order
from base.emails import (
    send_account_activation_email, send_admin_new_order_email,
    send_order_confirmation_email,
)
from home.models import City, DeliveryGuideline, ReturnRefundPolicy, ShippingAddress, State
from products.models import (
    Category, Coupon, Product, ProductImage, ProductReview, SizeVariant,
)
from PIL import Image


def city_for_state(state, name):
    city, _ = City.objects.get_or_create(
        state=state,
        normalized_name=City.normalize_name(name),
        defaults={'name': name},
    )
    return city


class ShippingAddressFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='address-customer', password='secret123')
        self.client.force_login(self.user)

    def test_shipping_address_page_defaults_to_india_and_shows_indian_states(self):
        response = self.client.get(reverse('shipping-address'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form']['country'].value(), 'IN')
        self.assertTrue(response.context['form'].fields['state'].required)
        self.assertEqual(response.context['form'].fields['state'].queryset.count(), 36)
        self.assertTrue(
            response.context['form'].fields['state'].queryset.filter(
                country__code='IN').exists())
        self.assertContains(response, 'name="state"')
        self.assertContains(response, 'select2-state')
        self.assertContains(response, 'select2-city')
        self.assertContains(response, '/static/js/vendor/jquery-3.5.1.min.js')
        self.assertContains(response, '/static/js/vendor/select2-4.0.13.min.js')
        self.assertContains(response, 'Use as my preferred shipping address')
        self.assertContains(response, 'Andhra Pradesh')
        response_html = response.content.decode()
        self.assertLess(
            response_html.index('name="state"'),
            response_html.index('name="city"'),
        )
        self.assertLess(
            response_html.index('name="state"'),
            response_html.index('name="zip_code"'),
        )

    def test_bound_form_filters_state_queryset_to_selected_country(self):
        form = ShippingAddressForm(data={'country': 'IN'})

        self.assertEqual(form.fields['state'].queryset.count(), 36)
        self.assertEqual(
            set(form.fields['state'].queryset.values_list('country__code', flat=True)),
            {'IN'},
        )

    def test_city_queryset_is_limited_to_selected_state(self):
        uttarakhand = State.objects.get(country__code='IN', name='Uttarakhand')
        delhi = State.objects.get(country__code='IN', name='Delhi')
        delhi_city = city_for_state(delhi, 'New Delhi')
        form = ShippingAddressForm(data={
            'country': 'IN',
            'state': uttarakhand.pk,
            'city': delhi_city.pk,
        })

        self.assertIn('city', form.errors)

    def test_city_endpoint_returns_only_cities_for_selected_state(self):
        uttarakhand = State.objects.get(country__code='IN', name='Uttarakhand')
        delhi = State.objects.get(country__code='IN', name='Delhi')
        city_for_state(uttarakhand, 'Dehradun')
        city_for_state(delhi, 'New Delhi')

        response = self.client.get(reverse('cities_for_state'), {'state': uttarakhand.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cities'], [
            {'id': City.objects.get(state=uttarakhand).pk, 'name': 'Dehradun'},
        ])

    def test_city_endpoint_rejects_invalid_state_ids(self):
        response = self.client.get(reverse('cities_for_state'), {'state': 'invalid'})

        self.assertEqual(response.status_code, 400)

    def test_shipping_address_names_reject_digits(self):
        for field_name in ('first_name', 'last_name'):
            form = ShippingAddressForm(data={
                'first_name': 'Ash1a' if field_name == 'first_name' else 'Asha',
                'last_name': 'Sharm2' if field_name == 'last_name' else 'Sharma',
                'country': 'IN',
            })

            self.assertIn(field_name, form.errors)
            self.assertIn(
                'Numbers are not allowed in names.',
                str(form.errors[field_name]),
            )

    def test_shipping_address_page_displays_name_validation_error(self):
        state = State.objects.get(country__code='IN', name='Delhi')
        city = city_for_state(state, 'New Delhi')
        for field_name in ('first_name', 'last_name'):
            response = self.client.post(reverse('shipping-address'), {
                'first_name': 'Ash1a' if field_name == 'first_name' else 'Asha',
                'last_name': 'Sharm2' if field_name == 'last_name' else 'Sharma',
                'street': 'Main Road',
                'street_number': '10',
                'zip_code': '110001',
                'city': city.pk,
                'country': 'IN',
                'state': state.pk,
                'phone': '9876543210',
            })

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Numbers are not allowed in names.')


class RegistrationPasswordTests(TestCase):
    def test_password_must_meet_all_registration_requirements(self):
        invalid_passwords = [
            ('abcdefg!', 'at least one number'),
            ('abcdefg1', 'at least one special character'),
            ('abc1!', '8 to 10 characters'),
            ('abcdefgh1!x', '8 to 10 characters'),
        ]

        for index, (password, error) in enumerate(invalid_passwords):
            with self.subTest(password=password):
                response = self.client.post(reverse('register'), {
                    'username': f'weak-password-{index}',
                    'first_name': 'Ada',
                    'last_name': 'Lovelace',
                    'email': f'weak-password-{index}@example.com',
                    'password': password,
                })

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, error)
                self.assertFalse(
                    User.objects.filter(username=f'weak-password-{index}').exists()
                )

    def test_compliant_password_is_valid(self):
        form = RegistrationForm(data={
            'first_name': 'Ada',
            'last_name': 'Lovelace',
            'email': 'ada@example.com',
            'password': 'S0lid!Pw',
        })

        self.assertTrue(form.is_valid(), form.errors)

    def test_profile_names_reject_digits(self):
        for field_name in ('first_name', 'last_name'):
            form = UserUpdateForm(data={
                'first_name': 'Ash1a' if field_name == 'first_name' else 'Asha',
                'last_name': 'Sharm2' if field_name == 'last_name' else 'Sharma',
                'email': 'asha@example.com',
            })

            self.assertIn(field_name, form.errors)

    def test_registration_page_displays_name_validation_error(self):
        for field_name in ('first_name', 'last_name'):
            first_name = 'Ash1a' if field_name == 'first_name' else 'Asha'
            last_name = 'Sharm2' if field_name == 'last_name' else 'Sharma'
            response = self.client.post(reverse('register'), {
                'username': f'name-test-{field_name}',
                'first_name': first_name,
                'last_name': last_name,
                'email': f'name-test-{field_name}@example.com',
                'password': 'secret123',
            })

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Numbers are not allowed in names.')
            self.assertContains(response, f'value="{first_name}"')


class OrderCancellationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='cancellation-customer',
            email='cancel@example.com',
            password='secret123',
        )
        self.other_user = User.objects.create_user(
            username='other-customer',
            email='other@example.com',
            password='secret123',
        )
        category = Category.objects.create(category_name='Gifts')
        self.product = Product.objects.create(
            product_name='Gift box',
            category=category,
            price=1000,
            product_desription='A gift box',
            stock_quantity=5,
        )
        self.order = Order.objects.create(
            user=self.user,
            order_id='ORDER-CANCEL-1',
            guest_access_token='cancel-token-123',
            payment_status='Paid',
            payment_mode='Razorpay',
            order_total_price=1000,
            grand_total=1000,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            quantity=2,
            product_price=1000,
        )

    def test_customer_can_cancel_within_24_hours_and_stock_is_restored_once(self):
        self.client.force_login(self.user)
        details_response = self.client.get(reverse(
            'order_details', args=[self.order.order_id]))
        self.assertContains(details_response, 'Cancel Order')
        self.assertContains(details_response, 'cancel-order-btn')
        cancel_url = reverse('cancel_order', args=[
            self.order.order_id, self.order.guest_access_token,
        ])

        response = self.client.post(cancel_url)

        self.assertRedirects(
            response, reverse('order_details', args=[self.order.order_id]))
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertEqual(self.order.payment_status, 'Refund pending')
        self.assertEqual(self.product.stock_quantity, 7)

        self.client.post(cancel_url)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 7)

    @override_settings(ADMIN_EMAIL='ops@example.com, warehouse@example.com')
    def test_cancellation_emails_customer_and_admin_team(self):
        self.client.force_login(self.user)

        self.client.post(reverse('cancel_order', args=[
            self.order.order_id, self.order.guest_access_token,
        ]))

        self.assertEqual(len(mail.outbox), 2)
        customer_email, admin_email = mail.outbox
        self.assertEqual(customer_email.to, ['cancel@example.com'])
        self.assertIn('Order cancelled', customer_email.subject)
        self.assertIn(self.order.order_id, customer_email.body)
        self.assertIn('refund', customer_email.body.lower())
        self.assertEqual(customer_email.alternatives[0][1], 'text/html')
        self.assertIn('cid:bundleofgifts-brand-icon', customer_email.alternatives[0][0])
        self.assertEqual(
            admin_email.to,
            ['ops@example.com', 'warehouse@example.com'],
        )
        self.assertIn('Order cancelled', admin_email.subject)
        self.assertIn(self.order.order_id, admin_email.body)

    def test_customer_can_cancel_before_dispatch_after_24_hours(self):
        self.order.order_date = timezone.now() - timedelta(hours=48)
        self.order.save(update_fields=['order_date'])
        self.client.force_login(self.user)

        self.client.post(reverse('cancel_order', args=[
            self.order.order_id, self.order.guest_access_token,
        ]))

        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertEqual(self.product.stock_quantity, 7)

    def test_cancellation_is_available_after_configured_window(self):
        policy = ReturnRefundPolicy.get_solo()
        policy.cancellation_window_hours = 6
        policy.save()
        self.order.order_date = timezone.now() - timedelta(hours=7)
        self.order.save(update_fields=['order_date'])

        self.assertTrue(self.order.can_be_cancelled)

    def test_configured_cancellation_window_allows_cancellation_after_default_window(self):
        policy = ReturnRefundPolicy.get_solo()
        policy.cancellation_window_hours = 48
        policy.save()
        self.order.order_date = timezone.now() - timedelta(hours=36)
        self.order.save(update_fields=['order_date'])

        self.assertTrue(self.order.can_be_cancelled)

    def test_only_order_owner_can_cancel(self):
        self.client.force_login(self.other_user)

        response = self.client.post(reverse('cancel_order', args=[
            self.order.order_id, self.order.guest_access_token,
        ]))

        self.assertEqual(response.status_code, 404)
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.ACCEPTED)
        self.assertEqual(self.product.stock_quantity, 5)

    def test_dispatched_order_cannot_be_cancelled(self):
        self.order.status = Order.Status.DISPATCHED
        self.order.save(update_fields=['status', 'updated_at'])
        self.client.force_login(self.user)

        details_response = self.client.get(reverse(
            'order_details', args=[self.order.order_id]))
        tracking_response = self.client.get(reverse(
            'track_order',
            args=[self.order.order_id, self.order.guest_access_token],
        ))

        self.assertNotContains(details_response, 'Cancel Order')
        self.assertNotContains(tracking_response, 'Cancel Order')
        self.client.post(reverse('cancel_order', args=[
            self.order.order_id, self.order.guest_access_token,
        ]))

        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.DISPATCHED)
        self.assertEqual(self.product.stock_quantity, 5)

    def test_guest_can_cancel_with_tracking_token(self):
        self.order.user = None
        self.order.save(update_fields=['user', 'updated_at'])
        tracking_url = reverse('track_order', args=[
            self.order.order_id, self.order.guest_access_token,
        ])
        self.assertContains(self.client.get(tracking_url), 'Cancel Order')

        response = self.client.post(reverse('cancel_order', args=[
            self.order.order_id, self.order.guest_access_token,
        ]))

        self.assertRedirects(response, reverse('track_order', args=[
            self.order.order_id, self.order.guest_access_token,
        ]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)

    def test_cancellation_restores_bundle_component_stock(self):
        bundle_product = Product.objects.create(
            product_name='Bundle component',
            category=self.product.category,
            price=200,
            product_desription='A bundle component',
            stock_quantity=3,
        )
        bundle_item = BundleOrderItem.objects.create(
            order=self.order,
            title='Gift bundle',
            quantity=2,
            original_unit_price=400,
            unit_price=350,
        )
        BundleOrderProduct.objects.create(
            bundle_item=bundle_item,
            product=bundle_product,
        )
        self.client.force_login(self.user)

        self.client.post(reverse('cancel_order', args=[
            self.order.order_id, self.order.guest_access_token,
        ]))

        bundle_product.refresh_from_db()
        self.assertEqual(bundle_product.stock_quantity, 5)


class CartItemQuantityUpdateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='quantity-customer', password='secret123')
        category = Category.objects.create(category_name='Gifts')
        self.product = Product.objects.create(
            product_name='Gift item',
            category=category,
            price=400,
            product_desription='A gift item',
            stock_quantity=5,
        )
        cart = Cart.objects.create(user=self.user)
        self.cart_item = CartItem.objects.create(
            cart=cart, product=self.product, quantity=2)
        self.client.force_login(self.user)

    def test_empty_cart_hides_cart_count_badge(self):
        self.cart_item.delete()

        response = self.client.get(reverse('cart'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'class="badge badge-pill badge-danger notify" hidden',
            html=False,
        )

    def test_mobile_cart_layout_places_coupon_and_actions_after_totals(self):
        Coupon.objects.create(coupon_code='RESPONSIVE')
        response = self.client.get(reverse('cart'))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertLess(html.index('Items subtotal'), html.index('Apply Coupon'))
        self.assertLess(html.index('Apply Coupon'), html.index('cart-page-actions'))
        self.assertIn('<details class="card mb-3 cart-coupon-disclosure"', html)
        sidebar_start = html.index('<aside class="col-md-3">')
        sidebar_end = html.index('</aside>', sidebar_start)
        self.assertLess(sidebar_start, html.index('Apply Coupon'))
        self.assertLess(html.index('Apply Coupon'), sidebar_end)
        self.assertIn('cart-available-coupon', html)
        self.assertIn('.table-shopping-cart .itemside .aside img', html)
        self.assertIn('padding-bottom: calc(3rem + env(safe-area-inset-bottom))', html)

    def test_cart_displays_active_coupons_and_can_apply_one(self):
        coupon = Coupon.objects.create(
            coupon_code='SAVE100',
            discount_amount=100,
            minimum_amount=500,
        )
        Coupon.objects.create(coupon_code='EXPIRED', is_expired=True)

        response = self.client.get(reverse('cart'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SAVE100')
        self.assertContains(response, '₹100 off on orders of ₹500 or more.')
        self.assertContains(
            response,
            '<details class="card mb-3 cart-coupon-disclosure">',
            html=False,
        )
        self.assertNotContains(response, 'EXPIRED')
        self.assertEqual(
            list(response.context['available_coupons']),
            [coupon],
        )

        response = self.client.post(
            reverse('cart'),
            {'coupon': coupon.coupon_code},
        )

        self.assertRedirects(response, reverse('cart'))
        self.cart_item.cart.refresh_from_db()
        self.assertEqual(self.cart_item.cart.coupon, coupon)

        response = self.client.get(reverse('cart'))
        self.assertContains(response, 'class="badge badge-success">Applied</span>')
        self.assertContains(
            response,
            '<details class="card mb-3 cart-coupon-disclosure" open>',
            html=False,
        )

    def test_cart_allows_only_one_coupon_and_remove_enables_another(self):
        first_coupon = Coupon.objects.create(
            coupon_code='FIRST',
            discount_amount=50,
            minimum_amount=500,
        )
        second_coupon = Coupon.objects.create(
            coupon_code='SECOND',
            discount_amount=75,
            minimum_amount=500,
        )
        cart_url = reverse('cart')

        self.client.post(cart_url, {'coupon': first_coupon.coupon_code})
        self.client.post(cart_url, {'coupon': second_coupon.coupon_code})

        self.cart_item.cart.refresh_from_db()
        self.assertEqual(self.cart_item.cart.coupon, first_coupon)

        response = self.client.post(
            reverse('remove_coupon', args=[self.cart_item.cart.uid]),
        )

        self.assertRedirects(response, cart_url)
        self.cart_item.cart.refresh_from_db()
        self.assertIsNone(self.cart_item.cart.coupon)

        response = self.client.get(cart_url)
        self.assertNotContains(response, 'class="badge badge-success">Applied</span>')
        self.assertContains(
            response,
            '<details class="card mb-3 cart-coupon-disclosure">',
            html=False,
        )
        self.assertContains(response, 'value="FIRST"')
        self.assertContains(response, 'value="SECOND"')

        self.client.post(cart_url, {'coupon': second_coupon.coupon_code})
        self.cart_item.cart.refresh_from_db()
        self.assertEqual(self.cart_item.cart.coupon, second_coupon)

    def test_reaching_delivery_threshold_shows_free_delivery_and_updates_subtotal(self):
        guideline = DeliveryGuideline.get_solo()
        guideline.delivery_charge_threshold = 500
        guideline.delivery_fee = 80
        guideline.save()
        self.product.price = 250
        self.product.save(update_fields=['price'])
        self.cart_item.quantity = 1
        self.cart_item.save(update_fields=['quantity'])

        below_threshold = self.client.get(reverse('cart'))
        self.assertEqual(below_threshold.context['cart_subtotal'], 330)
        self.assertContains(below_threshold, 'Delivery fee')

        update_response = self.client.post(
            reverse('update_cart_item'),
            data=json.dumps({
                'cart_item_id': str(self.cart_item.uid),
                'quantity': 2,
            }),
            content_type='application/json',
        )
        self.assertEqual(update_response.status_code, 200)

        at_threshold = self.client.get(reverse('cart'))
        self.assertEqual(at_threshold.context['cart_subtotal'], 500)
        self.assertEqual(at_threshold.context['delivery_fee'], 0)
        self.assertContains(at_threshold, 'Delivery fee')
        self.assertNotContains(
            at_threshold,
            'Free delivery because the order total meets',
        )

    def test_zero_quantity_removes_cart_item(self):
        remaining_item = CartItem.objects.create(
            cart=self.cart_item.cart,
            product=self.product,
            quantity=1,
            size_variant=SizeVariant.objects.create(size_name='Large'),
            gift_wrap=True,
        )
        response = self.client.post(
            reverse('update_cart_item'),
            data=json.dumps({
                'cart_item_id': str(self.cart_item.uid),
                'quantity': 0,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'success': True,
            'removed': True,
            'quantity': 0,
            'cart_count': 1,
        })
        self.assertFalse(CartItem.objects.filter(pk=self.cart_item.pk).exists())
        self.assertTrue(CartItem.objects.filter(pk=remaining_item.pk).exists())

    def test_update_rejects_quantity_above_inventory(self):
        response = self.client.post(
            reverse('update_cart_item'),
            data=json.dumps({
                'cart_item_id': str(self.cart_item.uid),
                'quantity': 6,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn('Only 5 available', response.json()['error'])
        self.cart_item.refresh_from_db()
        self.assertEqual(self.cart_item.quantity, 2)

    def test_overstocked_cart_item_can_be_reduced_toward_inventory(self):
        self.cart_item.quantity = 7
        self.cart_item.save(update_fields=['quantity'])

        response = self.client.post(
            reverse('update_cart_item'),
            data=json.dumps({
                'cart_item_id': str(self.cart_item.uid),
                'quantity': 6,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.cart_item.refresh_from_db()
        self.assertEqual(self.cart_item.quantity, 6)

    def test_cart_page_caps_existing_quantity_to_available_inventory(self):
        self.cart_item.quantity = 7
        self.cart_item.save(update_fields=['quantity'])

        response = self.client.get(reverse('cart'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'max="5"')
        self.assertContains(response, 'value="5"')
        self.assertEqual(response.context['stock_notices'], [{
            'product_name': 'Gift item',
            'available_quantity': 5,
        }])
        self.assertContains(response, 'aria-label="Dismiss notice"')
        self.cart_item.refresh_from_db()
        self.assertEqual(self.cart_item.quantity, 5)

    def test_cart_page_shows_out_of_stock_without_numeric_quantity(self):
        self.product.stock_quantity = 0
        self.product.save(update_fields=['stock_quantity'])

        response = self.client.get(reverse('cart'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Out of stock')
        self.assertNotContains(response, 'class="form-control cart-item-quantity"')

    def test_add_rejects_quantity_above_remaining_inventory(self):
        response = self.client.post(
            reverse('add_to_cart', args=[self.cart_item.product.uid]),
            {'quantity': 4, 'next': reverse('index')},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(any(
            'Only 3 more items can be added' in message.message
            for message in get_messages(response.wsgi_request)
        ))
        self.cart_item.refresh_from_db()
        self.assertEqual(self.cart_item.quantity, 2)


class RegisteredCheckoutFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='checkout-customer',
            password='secret123',
        )
        category = Category.objects.create(category_name='Checkout gifts')
        product = Product.objects.create(
            product_name='Checkout product',
            category=category,
            price=400,
            product_desription='A checkout product',
            stock_quantity=5,
        )
        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=self.cart, product=product, quantity=2)
        self.state = State.objects.get(country_id='IN', name='Uttarakhand')
        self.city = city_for_state(self.state, 'Dehradun')
        self.address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Checkout',
            last_name='Customer',
            street='Main Road',
            street_number='12',
            zip_code='248001',
            city=self.city,
            country='IN',
            state=self.state,
            phone='9999999999',
            is_default=True,
        )
        guideline = DeliveryGuideline.get_solo()
        guideline.delivery_charge_threshold = 1000
        guideline.delivery_fee = 80
        guideline.save()
        self.client.force_login(self.user)

    def test_cart_is_items_and_charges_only_before_checkout(self):
        response = self.client.get(reverse('cart'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<footer', html=False)
        self.assertContains(response, 'Checkout product')
        self.assertContains(response, 'Items subtotal')
        self.assertContains(response, 'Delivery fee')
        self.assertContains(response, '₹880')
        self.assertContains(response, 'Continue to checkout')
        self.assertNotContains(response, 'Choose delivery address')
        self.assertNotContains(response, 'Cash on Delivery')
        self.assertNotContains(response, 'Pay Online')

    def test_checkout_reviews_selected_address_items_and_charges(self):
        response = self.client.get(reverse('checkout'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Review delivery address')
        self.assertContains(response, 'Checkout Customer')
        self.assertContains(response, 'Shipping Address')
        self.assertNotContains(response, 'badge badge-success">Default</span>')
        self.assertContains(
            response,
            f'href="{reverse("shipping-address")}?address_id={self.address.uid}&amp;next={reverse("checkout")}"',
        )
        self.assertContains(response, 'aria-label="Edit Checkout Customer address"')
        self.assertContains(response, 'class="fas fa-pen"')
        self.assertContains(response, 'connector-complete')
        self.assertContains(response, 'is-current')
        self.assertContains(response, 'Checkout product')
        self.assertContains(response, 'Items subtotal')
        self.assertContains(response, 'order total is below')
        self.assertContains(response, 'Continue to payment')
        self.assertNotContains(response, 'Cash on Delivery')
        self.assertNotContains(response, 'Pay Online')

    def test_free_delivery_notice_is_hidden_on_cart_checkout_and_payment(self):
        guideline = DeliveryGuideline.get_solo()
        guideline.delivery_charge_threshold = 500
        guideline.save()

        responses = (
            self.client.get(reverse('cart')),
            self.client.get(reverse('checkout')),
            self.client.get(
                reverse('payment'),
                {'address': str(self.address.uid)},
            ),
        )

        for response in responses:
            with self.subTest(path=response.request['PATH_INFO']):
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Delivery fee')
                self.assertContains(response, '>Free</span>')
                self.assertNotContains(
                    response,
                    'Free delivery because the order total meets',
                )

    def test_checkout_advances_selected_address_to_payment(self):
        response = self.client.post(
            reverse('checkout'),
            {'selected_address_id': str(self.address.uid)},
        )

        payment_url = f'{reverse("payment")}?address={self.address.uid}'
        self.assertRedirects(
            response,
            payment_url,
            fetch_redirect_response=False,
        )

    def test_checkout_requires_an_address_before_advancing(self):
        ShippingAddress.objects.filter(user=self.user).delete()

        response = self.client.get(reverse('checkout'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Add a delivery address')
        self.assertContains(response, 'Add address')
        self.assertNotContains(response, 'Continue to payment')

        response = self.client.get(reverse('payment'))
        self.assertRedirects(response, reverse('checkout'))

    @override_settings(ONLINE_PAYMENT_ENABLED=False)
    def test_payment_page_shows_existing_payment_options_and_order_summary(self):
        response = self.client.get(
            reverse('payment'),
            {'address': str(self.address.uid)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cash on Delivery')
        self.assertContains(response, 'id="payment-processing-overlay"')
        self.assertContains(response, 'role="progressbar"')
        self.assertContains(response, 'backdrop-filter: blur(7px)')
        self.assertContains(response, 'Pay Online - Coming Soon')
        self.assertContains(response, 'Order summary')
        self.assertContains(response, '₹880')
        self.assertContains(response, 'Delivering to')
        self.assertContains(response, 'connector-complete')
        self.assertContains(response, 'is-current')
        self.assertContains(response, 'images/misc/payments.png')
        self.assertContains(response, 'Visa, Mastercard, RuPay')
        self.assertContains(response, 'class="payment-method-actions"')
        self.assertContains(response, 'Supported payment methods')
        self.assertContains(response, 'class="col-lg-8 payment-options-column"')
        self.assertContains(response, 'class="col-lg-4 order-summary-column"')
        html = response.content.decode()
        self.assertLess(
            html.index('id="payment-options-heading"'),
            html.index('Order summary'),
        )
        self.assertLess(
            html.index('Order summary'),
            html.index('payment-back-link'),
        )
        self.assertIn('.payment-options-column {\n      order: 2;', html)
        self.assertIn('.order-summary-column {\n      order: 1;', html)
        self.assertIn('@media (min-width: 992px)', html)
        self.assertIn('.payment-method-actions {\n      flex-direction: row;', html)

    def test_checkout_edit_link_opens_selected_address_form_and_returns_to_checkout(self):
        response = self.client.get(
            reverse('shipping-address'),
            {
                'address_id': str(self.address.uid),
                'next': reverse('checkout'),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_address'], self.address)
        self.assertEqual(response.context['next_url'], reverse('checkout'))
        self.assertEqual(
            response.context['form']['first_name'].value(),
            self.address.first_name,
        )


@override_settings(SERVICE_CITY='India')
class GuestCheckoutDeliveryTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(category_name='Guest gifts')
        self.product = Product.objects.create(
            product_name='Guest gift',
            category=self.category,
            price=600,
            product_desription='A guest gift',
            stock_quantity=5,
        )
        self.client.post(
            reverse('add_to_cart', args=[self.product.uid]),
            {'quantity': 1},
        )
        self.cart = Cart.objects.get(session_key=self.client.session.session_key)
        self.state = State.objects.get(country_id='IN', name='Delhi')
        self.city = city_for_state(self.state, 'New Delhi')
        ServiceablePincode.objects.create(pincode='110001')
        DeliveryGuideline.get_solo()

    def checkout_data(self, delivery_option=None):
        data = {
            'first_name': 'Guest',
            'last_name': 'Customer',
            'street': 'Main Road',
            'street_number': '12',
            'zip_code': '110001',
            'city': self.city.pk,
            'country': 'IN',
            'state': self.state.pk,
            'phone': '9999999999',
            'email': 'guest@example.com',
        }
        if delivery_option:
            data['delivery_option'] = delivery_option
        return data

    def test_guest_delivery_options_do_not_offer_express_without_radius_check(self):
        response = self.client.post(
            reverse('delivery-options'),
            data=json.dumps({'city': 'New Delhi'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [option['value'] for option in response.json()['options']],
            ['next_day'],
        )

    @override_settings(SERVICE_CITY='Dehradun')
    def test_guest_checkout_rejects_addresses_outside_service_city(self):
        response = self.client.post(
            reverse('guest-checkout'), self.checkout_data(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'We are currently operational in Dehradun city only.')
        self.assertContains(response, 'Please choose a delivery address in Dehradun.')
        self.assertNotContains(response, 'Total to pay')
        self.assertFalse(Order.objects.filter(guest_email='guest@example.com').exists())

    def test_guest_checkout_shows_total_before_confirming_order(self):
        initial_response = self.client.get(reverse('guest-checkout'))
        self.assertEqual(initial_response.status_code, 200)
        self.assertContains(initial_response, 'We are currently operational across India.')
        self.assertNotContains(initial_response, 'Delivery charges')
        self.assertContains(initial_response, 'data-validate-on-blur')
        self.assertContains(initial_response, 'select2-city')
        self.assertContains(initial_response, 'select2-state')
        initial_response_html = initial_response.content.decode()
        self.assertLess(
            initial_response_html.index('name="state"'),
            initial_response_html.index('name="city"'),
        )

        with patch('accounts.views.send_order_confirmation_email'), patch(
            'accounts.views.send_admin_new_order_email'
        ):
            quote_response = self.client.post(
                reverse('guest-checkout'),
                self.checkout_data(),
            )
            self.assertEqual(quote_response.status_code, 200)
            self.assertContains(quote_response, 'Delivery charges')
            self.assertContains(quote_response, 'Total to pay')
            for address_value in (
                'Guest', 'Customer', 'Main Road', '12', 'New Delhi',
                'Delhi', '110001', 'India', '9999999999',
                'guest@example.com',
            ):
                self.assertContains(quote_response, address_value)
            self.assertEqual(
                quote_response.content.count(b'class="guest-delivery-address-line'),
                3,
            )
            self.assertContains(quote_response, 'class="card guest-delivery-address')
            self.assertNotContains(quote_response, 'First name:')
            self.assertNotContains(quote_response, 'Street:')
            self.assertFalse(Order.objects.filter(guest_email='guest@example.com').exists())
            response = self.client.post(
                reverse('guest-checkout'),
                {'confirm_order': '1'},
            )

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(guest_email='guest@example.com')
        self.assertEqual(order.delivery_option, Order.DeliveryOption.NEXT_DAY)
        self.assertEqual(order.status, Order.Status.ACCEPTED)
        self.assertEqual(order.grand_total, 600)

    def test_confirmation_requotes_when_cart_total_changes(self):
        self.client.post(reverse('guest-checkout'), self.checkout_data())
        self.product.price = 700
        self.product.save(update_fields=['price', 'updated_at'])

        updated_quote = self.client.post(
            reverse('guest-checkout'), {'confirm_order': '1'})

        self.assertEqual(updated_quote.status_code, 200)
        self.assertContains(updated_quote, 'Your cart or delivery charge changed.')
        self.assertContains(updated_quote, '₹700.00')
        self.assertFalse(Order.objects.filter(guest_email='guest@example.com').exists())

        with patch('accounts.views.send_order_confirmation_email'), patch(
            'accounts.views.send_admin_new_order_email'
        ):
            response = self.client.post(
                reverse('guest-checkout'), {'confirm_order': '1'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Order.objects.get(guest_email='guest@example.com').grand_total,
            700,
        )

    def test_guest_checkout_automatically_uses_express_within_configured_radius(self):
        guideline = DeliveryGuideline.get_solo()
        guideline.operational_office_city = 'New Delhi'
        guideline.operational_office_latitude = '28.613900'
        guideline.operational_office_longitude = '77.209000'
        guideline.free_delivery_radius_km = '5.00'
        guideline.save()
        data = self.checkout_data()
        data.update({
            'delivery_latitude': '28.6139',
            'delivery_longitude': '77.2090',
            'delivery_accuracy_meters': '10',
        })

        with patch('accounts.views.send_order_confirmation_email'), patch(
            'accounts.views.send_admin_new_order_email'
        ):
            quote_response = self.client.post(reverse('guest-checkout'), data)
            self.assertEqual(quote_response.status_code, 200)
            self.assertContains(quote_response, 'Total to pay')
            response = self.client.post(
                reverse('guest-checkout'), {'confirm_order': '1'})

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(guest_email='guest@example.com')
        self.assertEqual(order.delivery_option, Order.DeliveryOption.EXPRESS)

    def test_guest_checkout_accepts_nonserviceable_pincode_without_review(self):
        data = self.checkout_data()
        data['zip_code'] = '110002'
        with patch('accounts.views.send_order_confirmation_email'), patch(
            'accounts.views.send_admin_new_order_email'
        ):
            quote_response = self.client.post(reverse('guest-checkout'), data)
            self.assertEqual(quote_response.status_code, 200)
            response = self.client.post(
                reverse('guest-checkout'), {'confirm_order': '1'})

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(guest_email='guest@example.com')
        self.assertEqual(order.status, Order.Status.ACCEPTED)
        self.assertTrue(order.outside_service_area)
        self.assertEqual(order.delivery_option, '')

    def test_cart_displays_delivery_estimates_without_delivery_modal(self):
        response = self.client.get(reverse('cart'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Continue to checkout')
        self.assertNotContains(response, 'Cash on Delivery')
        self.assertNotContains(response, 'Pay Online')
        self.assertNotContains(response, 'Choose delivery address')
        self.assertNotContains(response, 'delivery-option-modal')
        self.assertNotContains(response, 'Delivery estimates')

    def test_guest_order_includes_configured_delivery_fee_below_threshold(self):
        guideline = DeliveryGuideline.get_solo()
        guideline.delivery_charge_threshold = 700
        guideline.delivery_fee = 95
        guideline.save()

        with patch('accounts.views.send_order_confirmation_email'), patch(
            'accounts.views.send_admin_new_order_email'
        ):
            quote_response = self.client.post(
                reverse('guest-checkout'),
                self.checkout_data(),
            )
            self.assertEqual(quote_response.status_code, 200)
            self.assertContains(quote_response, '₹95.00')
            self.assertContains(quote_response, '₹695.00')
            self.assertFalse(Order.objects.filter(guest_email='guest@example.com').exists())
            response = self.client.post(
                reverse('guest-checkout'), {'confirm_order': '1'})

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(guest_email='guest@example.com')
        self.assertEqual(order.additional_delivery_fee, 95)
        self.assertEqual(order.grand_total, 695)

class OrderAdminFilterTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username='orders-admin',
            email='orders-admin@example.com',
            password='secret123',
        )
        self.client.force_login(self.admin_user)
        self.today = timezone.localdate()
        self.placed_order = self._create_order(
            'FILTER-PLACED', Order.Status.PENDING_REVIEW, 'Pending', 'Razorpay')
        self.online_paid_order = self._create_order(
            'FILTER-ONLINE', Order.Status.ACCEPTED, 'Paid', 'Razorpay')
        self.cod_order = self._create_order(
            'FILTER-COD', Order.Status.PACKING, 'Pending', 'Cash on Delivery')
        self.refund_order = self._create_order(
            'FILTER-REFUND', Order.Status.CANCELLED, 'Refund initiated', 'Razorpay')
        self.old_order = self._create_order(
            'FILTER-OLD', Order.Status.DELIVERED, 'Paid', 'Razorpay', days_ago=40)

    def _create_order(self, order_id, status, payment_status, payment_mode, days_ago=0):
        order = Order.objects.create(
            order_id=order_id,
            status=status,
            payment_status=payment_status,
            payment_mode=payment_mode,
            order_total_price=500,
            grand_total=500,
        )
        Order.objects.filter(pk=order.pk).update(
            order_date=timezone.now() - timedelta(days=days_ago))
        return order

    def _filtered_order_ids(self, **query):
        response = self.client.get(
            reverse('admin:accounts_order_changelist'), query)
        self.assertEqual(response.status_code, 200)
        return set(response.context['cl'].queryset.values_list('order_id', flat=True))

    def test_order_changelist_supports_page_sizes_and_preserves_filters(self):
        for index in range(26):
            self._create_order(
                f'PAGE-SIZE-{index}',
                Order.Status.ACCEPTED,
                'Paid',
                'Razorpay',
            )

        url = reverse('admin:accounts_order_changelist')
        response = self.client.get(url, {
            'per_page': '25',
            'period': 'this_month',
            'order_status': Order.Status.ACCEPTED,
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['cl'].list_per_page, 25)
        self.assertEqual(len(response.context['cl'].result_list), 25)
        self.assertContains(response, 'Records per page:')
        self.assertContains(
            response,
            '<details id="changelist-filter" open>',
            html=False,
        )
        self.assertContains(response, 'name="period" value="this_month"')
        self.assertContains(
            response,
            f'name="order_status" value="{Order.Status.ACCEPTED}"',
        )

        response = self.client.get(url, {'per_page': '50'})
        self.assertEqual(response.context['cl'].list_per_page, 50)
        self.assertEqual(len(response.context['cl'].result_list), 31)

        response = self.client.get(url, {'per_page': '100'})
        self.assertEqual(response.context['cl'].list_per_page, 100)

    def test_order_filter_sidebar_is_collapsed_until_filters_are_active(self):
        url = reverse('admin:accounts_order_changelist')

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<details id="changelist-filter">',
            html=False,
        )
        self.assertContains(response, 'id="changelist-filter-header">Filter')
        self.assertContains(response, '@media (max-width: 767px)')
        self.assertContains(response, '#changelist {')
        self.assertContains(response, 'display: block;')
        self.assertContains(response, '.changelist-form-container')
        self.assertContains(response, '#changelist #changelist-filter {')
        self.assertContains(response, 'position: absolute;')
        self.assertContains(response, '#changelist #changelist-filter[open]')
        self.assertContains(response, 'position: fixed;')
        self.assertContains(response, 'height: 100dvh;')
        self.assertNotContains(response, '\n        width: 0;')

        response = self.client.get(url, {'order_status': Order.Status.ACCEPTED})

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<details id="changelist-filter" open>',
            html=False,
        )
        self.assertContains(response, 'right: 0;')

    def test_invalid_order_page_size_uses_default(self):
        response = self.client.get(
            reverse('admin:accounts_order_changelist'),
            {'per_page': '500'},
        )

        self.assertEqual(response.context['cl'].list_per_page, 25)

    def test_custom_date_range_is_inclusive_and_shows_range_widget(self):
        response = self.client.get(
            reverse('admin:accounts_order_changelist'),
            {'period': 'custom', 'date_from': self.today.isoformat(), 'date_to': self.today.isoformat()},
        )

        self.assertEqual(response.status_code, 200, response.get('Location'))
        self.assertContains(response, 'Apply range')
        self.assertContains(response, 'id="order-date-from"')
        self.assertEqual(
            set(response.context['cl'].queryset.values_list('order_id', flat=True)),
            {
                'FILTER-PLACED', 'FILTER-ONLINE', 'FILTER-COD', 'FILTER-REFUND',
            },
        )
        self.assertEqual(
            self._filtered_order_ids(
                order_date__year=self.today.year,
                order_date__month=self.today.month,
                order_date__day=self.today.day,
            ),
            {'FILTER-PLACED', 'FILTER-ONLINE', 'FILTER-COD', 'FILTER-REFUND'},
        )

    def test_weekly_and_monthly_period_filters(self):
        self.assertEqual(
            self._filtered_order_ids(period='this_week'),
            {'FILTER-PLACED', 'FILTER-ONLINE', 'FILTER-COD', 'FILTER-REFUND'},
        )
        self.assertEqual(
            self._filtered_order_ids(period='this_month'),
            {'FILTER-PLACED', 'FILTER-ONLINE', 'FILTER-COD', 'FILTER-REFUND'},
        )

    def test_order_workflow_and_payment_type_filters(self):
        self.assertEqual(
            self._filtered_order_ids(order_status='placed'),
            {'FILTER-PLACED'},
        )
        self.assertEqual(
            self._filtered_order_ids(order_status=Order.Status.CANCELLED),
            {'FILTER-REFUND'},
        )
        self.assertEqual(
            self._filtered_order_ids(payment_type='online'),
            {'FILTER-ONLINE', 'FILTER-OLD'},
        )
        self.assertEqual(
            self._filtered_order_ids(payment_type='cod'),
            {'FILTER-COD'},
        )

    def test_payment_and_return_status_filters(self):
        payment_statuses = {
            'refund_requested': 'Refund requested',
            'refund_initiated': 'Refund initiated',
            'refund_processed': 'Refund processed',
            'return_requested': 'Return requested',
        }
        for filter_value, payment_status in payment_statuses.items():
            with self.subTest(financial_status=filter_value):
                self.refund_order.payment_status = payment_status
                self.refund_order.save(
                    update_fields=['payment_status', 'updated_at'])
                self.assertEqual(
                    self._filtered_order_ids(financial_status=filter_value),
                    {'FILTER-REFUND'},
                )


class RegistrationTests(TestCase):
    def test_register_form_loads_blur_validation(self):
        response = self.client.get(reverse('register'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-validate-on-blur')
        self.assertContains(response, 'type="email"')
        self.assertContains(response, '/static/js/form-validation.js')
        self.assertContains(response, '/static/css/register.min.css')
        self.assertContains(response, 'class="fab fa-facebook-f facebook-icon"')
        self.assertNotContains(response, '/static/css/contact.min.css')

    def test_login_page_loads_styles_for_social_sign_in_images(self):
        response = self.client.get(reverse('login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/static/css/register.min.css')
        self.assertContains(response, 'class="google-img"')
        self.assertContains(response, 'class="fab fa-facebook-f facebook-icon"')

    def test_invalid_email_stays_in_registration_form_with_inline_error(self):
        response = self.client.post(reverse('register'), {
            'username': 'ada-lovelace',
            'first_name': 'Ada',
            'last_name': 'Lovelace',
            'email': 'not-an-email',
            'password': 'S0lid!Pw',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enter a valid email address.')
        self.assertContains(response, 'value="not-an-email"')
        self.assertFalse(User.objects.filter(username='ada-lovelace').exists())

    def register(self, username, email):
        return self.client.post(
            reverse('register'),
            {
                'username': username,
                'first_name': 'Test',
                'last_name': 'User',
                'email': email,
                'password': 'secret123',
            },
            follow=True,
        )

    def test_existing_username_does_not_send_verification_email(self):
        User.objects.create_user(
            username='existing-user',
            email='existing@example.com',
            password='secret123',
        )

        response = self.register('existing-user', 'new@example.com')

        self.assertEqual(mail.outbox, [])
        self.assertContains(response, 'Username or email already exists!')
        self.assertContains(response, 'alert-danger')
        self.assertFalse(User.objects.filter(email='new@example.com').exists())

    def test_existing_email_does_not_send_verification_email(self):
        User.objects.create_user(
            username='existing-user',
            email='existing@example.com',
            password='secret123',
        )

        response = self.register('new-user', 'existing@example.com')

        self.assertEqual(mail.outbox, [])
        self.assertContains(response, 'Username or email already exists!')
        self.assertContains(response, 'alert-danger')
        self.assertFalse(User.objects.filter(username='new-user').exists())

    def test_password_reset_email_uses_shared_branded_html_template(self):
        User.objects.create_user(
            username='reset-customer',
            email='reset@example.com',
            password='secret123',
        )

        response = self.client.post(
            reverse('password_reset'),
            {'email': 'reset@example.com'},
        )

        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        reset_email = mail.outbox[0]
        reset_html = reset_email.alternatives[0][0]
        self.assertEqual(reset_email.alternatives[0][1], 'text/html')
        self.assertIn('src="cid:bundleofgifts-brand-icon"', reset_html)
        self.assertNotIn('bundleofgifts-icon.', reset_html)
        self.assertIn('Reset Password', reset_html)
        self.assertIn('/accounts/reset/', reset_html)
        self.assertIn('<table role="presentation"', reset_html)
        message_parts = list(reset_email.message().walk())
        related_parts = [
            part for part in message_parts
            if part.get_content_type() == 'multipart/related'
        ]
        self.assertEqual(len(related_parts), 1)
        brand_image = next(
            part for part in related_parts[0].walk()
            if part.get('Content-ID') == '<bundleofgifts-brand-icon>'
        )
        self.assertEqual(brand_image.get_content_type(), 'image/png')
        self.assertTrue(brand_image.get_payload(decode=True).startswith(b'\x89PNG'))


class CustomerEmailBrandingTests(TestCase):
    def setUp(self):
        self.media_dir = TemporaryDirectory()
        media_settings = override_settings(MEDIA_ROOT=self.media_dir.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.addCleanup(self.media_dir.cleanup)
        self.user = User.objects.create_user(
            username='branded-customer',
            first_name='Asha',
            email='asha@example.com',
        )
        category = Category.objects.create(category_name='Email gifts')
        self.product = Product.objects.create(
            product_name='Gift Box',
            category=category,
            price=500,
            product_desription='A gift box.',
        )
        self.order = Order.objects.create(
            user=self.user,
            order_id='ORDER-BRANDED-EMAIL',
            guest_access_token='branded-email-token',
            payment_status='Paid',
            payment_mode='Razorpay',
            order_total_price=500,
            grand_total=500,
            shipping_address='10 Main Street\nDehradun',
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            quantity=1,
            product_price=500,
        )

    def assert_branded_html_email(self, email):
        self.assertEqual(len(email.alternatives), 1)
        html, mimetype = email.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertIn('BundleofGifts', html)
        self.assertEqual(html.count('src="cid:bundleofgifts-brand-icon"'), 2)
        self.assertNotIn('bundleofgifts-icon.', html)
        self.assertIn('<table role="presentation"', html)
        related_parts = [
            part for part in email.message().walk()
            if part.get_content_type() == 'multipart/related'
        ]
        self.assertEqual(len(related_parts), 1)
        brand_image = next(
            part for part in related_parts[0].walk()
            if part.get('Content-ID') == '<bundleofgifts-brand-icon>'
        )
        self.assertEqual(brand_image.get_content_type(), 'image/png')

    @override_settings(PUBLIC_BASE_URL='https://shop.example')
    def test_account_activation_uses_shared_branded_html_template(self):
        send_account_activation_email('asha@example.com', 'activation-token')

        self.assertEqual(len(mail.outbox), 1)
        activation_email = mail.outbox[0]
        self.assert_branded_html_email(activation_email)
        self.assertIn('activation-token', activation_email.alternatives[0][0])
        self.assertIn('https://shop.example', activation_email.alternatives[0][0])

    @override_settings(PUBLIC_BASE_URL='https://shop.example')
    def test_order_confirmation_uses_shared_branded_html_and_keeps_invoice(self):
        send_order_confirmation_email(self.order)

        self.assertEqual(len(mail.outbox), 1)
        confirmation_email = mail.outbox[0]
        self.assert_branded_html_email(confirmation_email)
        confirmation_html = confirmation_email.alternatives[0][0]
        self.assertIn(self.product.product_name, confirmation_html)
        self.assertIn('Track your order', confirmation_html)
        self.assertIn('Shipping address', confirmation_html)
        self.assertIn('<th align="left" colspan="2"', confirmation_html)
        self.assertNotIn('src="cid:product-', confirmation_html)
        self.assertTrue(any(
            attachment[0] == f'invoice_{self.order.order_id}.pdf'
            for attachment in confirmation_email.attachments
        ))

    @override_settings(PUBLIC_BASE_URL='https://shop.example')
    def test_order_confirmation_embeds_small_product_thumbnail_and_keeps_invoice(self):
        image_bytes = BytesIO()
        Image.new('RGB', (320, 160), 'red').save(image_bytes, format='PNG')
        ProductImage.objects.create(
            product=self.product,
            image_url=SimpleUploadedFile(
                'gift-box.png',
                image_bytes.getvalue(),
                content_type='image/png',
            ),
        )

        send_order_confirmation_email(self.order)

        confirmation_email = mail.outbox[0]
        confirmation_html = confirmation_email.alternatives[0][0]
        self.assertIn('src="cid:product-', confirmation_html)
        self.assertTrue(any(
            isinstance(attachment, tuple)
            and attachment[0] == f'invoice_{self.order.order_id}.pdf'
            for attachment in confirmation_email.attachments
        ))
        image_attachment = next(
            attachment for attachment in confirmation_email.message().walk()
            if attachment.get('Content-ID')
            and attachment.get('Content-ID').startswith('<product-')
        )
        content_id = (
            'product-'
            + confirmation_html.split('src="cid:product-', 1)[1].split('"', 1)[0]
        )
        self.assertEqual(image_attachment.get('Content-ID'), f'<{content_id}>')
        self.assertEqual(image_attachment.get_content_type(), 'image/jpeg')
        thumbnail = Image.open(BytesIO(image_attachment.get_payload(decode=True)))
        self.assertLessEqual(max(thumbnail.size), 96)


class UserShippingAddressTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='alice',
            email='alice@example.com',
            password='secret123',
        )
        self.category = Category.objects.create(category_name='Toys')
        self.product = Product.objects.create(
            product_name='Puzzle Set',
            category=self.category,
            price=800,
            product_desription='A fun puzzle set',
            stock_quantity=10,
        )
        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        self.state = State.objects.get(country_id='IN', name='Uttarakhand')
        self.city = city_for_state(self.state, 'Dehradun')

    def test_saved_addresses_are_individual_cards_with_shipping_address_badge(self):
        ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Road',
            street_number='10',
            zip_code='110001',
            city=self.city,
            country='IN',
            state=self.state,
            phone='9876543210',
            is_default=True,
            current_address=True,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('shipping-address'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="card saved-address-card"')
        self.assertContains(response, 'Shipping Address')
        self.assertNotContains(response, '>Default</span>')

    def test_profile_picture_can_be_uploaded_and_is_displayed(self):
        image_buffer = BytesIO()
        Image.new('RGB', (20, 30), color='green').save(
            image_buffer, format='JPEG')
        image = SimpleUploadedFile(
            'cropped-profile.jpg',
            image_buffer.getvalue(),
            content_type='image/jpeg',
        )
        with TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root,
        ):
            self.client.force_login(self.user)
            response = self.client.post(
                reverse('profile', kwargs={'username': self.user.username}),
                {
                    'first_name': 'Alice',
                    'last_name': 'Smith',
                    'email': 'alice@example.com',
                    'bio': 'Hello there',
                    'profile_image_file': image,
                },
            )

            self.assertEqual(response.status_code, 302)
            self.user.profile.refresh_from_db()
            self.assertTrue(self.user.profile.profile_image_file)
            self.assertContains(
                self.client.get(
                    reverse('profile', kwargs={'username': self.user.username}),
                ),
                self.user.profile.profile_image_file.url,
            )

    def test_profile_page_has_image_upload_and_crop_controls(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse('profile', kwargs={'username': self.user.username}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-profile-image-input')
        self.assertContains(response, 'profile-crop-stage')
        self.assertContains(response, 'profile-image-zoom')

    def test_profile_picture_upload_rejects_images_over_five_megabytes(self):
        image_buffer = BytesIO()
        Image.new('RGB', (20, 30), color='green').save(
            image_buffer, format='JPEG')
        oversized_image = SimpleUploadedFile(
            'oversized-profile.jpg',
            image_buffer.getvalue() + b'x' * (5 * 1024 * 1024),
            content_type='image/jpeg',
        )
        form = UserProfileForm(
            data={'bio': ''},
            files={'profile_image_file': oversized_image},
            instance=self.user.profile,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('no larger than 5 MB', str(form.errors))

    def test_cart_shows_delivery_fee_below_products_not_in_sidebar(self):
        guideline = DeliveryGuideline.get_solo()
        guideline.delivery_charge_threshold = 1000
        guideline.delivery_fee = 80
        guideline.save()
        ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Street',
            street_number='12',
            zip_code='248001',
            city=self.city,
            country='IN',
            state=self.state,
            phone='9999999999',
            is_default=True,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('cart'))

        self.assertEqual(response.status_code, 200)
        response_html = response.content.decode()
        self.assertNotContains(response, 'Processing order...')
        self.assertNotContains(response, 'Choose delivery address')
        self.assertNotContains(response, 'Pay Online')
        self.assertNotContains(response, 'Cash on Delivery')
        fee_position = response_html.index('Delivery fee')
        total_position = response_html.index('Total')
        self.assertLess(fee_position, total_position)
        self.assertContains(response, '₹80')
        self.assertContains(response, '₹880')
        self.assertContains(response, 'order total is below')

    def test_profile_name_fields_load_blur_validation(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('profile', args=[self.user.username]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-validate-on-blur')
        self.assertContains(response, '/static/js/form-validation.js')

    def test_login_throttles_repeated_invalid_credentials(self):
        self.user.profile.is_email_verified = True
        self.user.profile.save(update_fields=['is_email_verified', 'updated_at'])

        for attempt in range(3):
            response = self.client.post(reverse('login'), {
                'username': 'alice',
                'password': 'wrong-password',
            })
            expected_status = 429 if attempt == 2 else 302
            self.assertEqual(response.status_code, expected_status)

        response = self.client.post(reverse('login'), {
            'username': 'alice',
            'password': 'secret123',
        })
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'alert-danger', status_code=429)
        self.assertContains(
            response,
            'Too many unsuccessful attempts',
            status_code=429,
        )

    def test_user_can_save_multiple_addresses_and_set_default(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('shipping-address'), {
            'first_name': 'Alice',
            'last_name': 'Smith',
            'street': 'Main Street',
            'street_number': '12',
            'zip_code': '110001',
            'city': self.city.pk,
            'country': 'IN',
            'state': self.state.pk,
            'phone': '9999999999',
            'is_default': 'on',
        })
        self.assertEqual(response.status_code, 302)

        first_address = ShippingAddress.objects.get(user=self.user, first_name='Alice')
        self.assertTrue(first_address.is_default)
        self.assertTrue(first_address.current_address)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.shipping_address, first_address)

        response = self.client.post(reverse('shipping-address'), {
            'first_name': 'Bob',
            'last_name': 'Jones',
            'street': 'Market Road',
            'street_number': '43',
            'zip_code': '110002',
            'city': self.city.pk,
            'country': 'IN',
            'state': self.state.pk,
            'phone': '8888888888',
            'is_default': 'on',
        })
        self.assertEqual(response.status_code, 302)

        self.assertEqual(ShippingAddress.objects.filter(user=self.user).count(), 2)
        second_address = ShippingAddress.objects.get(user=self.user, first_name='Bob')
        self.assertTrue(second_address.is_default)
        self.assertTrue(second_address.current_address)
        first_address.refresh_from_db()
        self.assertFalse(first_address.is_default)
        self.assertFalse(first_address.current_address)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.shipping_address, second_address)

    def test_checkout_requires_selected_address_when_multiple_addresses_exist(self):
        self.client.force_login(self.user)
        state = State.objects.get(country_id='IN', name='Uttarakhand')
        first_address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Street',
            street_number='12',
            zip_code='110001',
            city=self.city,
            country='IN',
            state=state,
            phone='9999999999',
            is_default=True,
            current_address=True,
        )
        second_address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Bob',
            last_name='Jones',
            street='Park Lane',
            street_number='7',
            zip_code='110002',
            city=self.city,
            country='IN',
            state=state,
            phone='8888888888',
            is_default=False,
            current_address=False,
        )
        self.user.profile.shipping_address = first_address
        self.user.profile.save(update_fields=['shipping_address', 'updated_at'])

        response = self.client.post(
            reverse('place_cod_order'),
            data=json.dumps({}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'Please choose a delivery address.')

        response = self.client.post(
            reverse('place_cod_order'),
            data=json.dumps({'selected_address_id': str(second_address.uid)}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('redirect_url', response.json())
        order = Order.objects.get(user=self.user)
        self.assertTrue(order.outside_service_area)
        self.assertEqual(order.delivery_option, '')

    def test_user_can_delete_only_non_default_addresses(self):
        self.client.force_login(self.user)

        first_address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Street',
            street_number='12',
            zip_code='110001',
            city=self.city,
            country='IN',
            state=self.state,
            phone='9999999999',
            is_default=True,
            current_address=True,
        )
        second_address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Bob',
            last_name='Jones',
            street='Park Lane',
            street_number='7',
            zip_code='110002',
            city=self.city,
            country='IN',
            state=self.state,
            phone='8888888888',
            is_default=False,
            current_address=False,
        )

        response = self.client.post(reverse('delete_shipping_address', args=[str(second_address.uid)]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ShippingAddress.objects.filter(uid=second_address.uid).exists())

        response = self.client.post(reverse('delete_shipping_address', args=[str(first_address.uid)]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ShippingAddress.objects.filter(uid=first_address.uid).exists())
        first_address.refresh_from_db()
        self.assertTrue(first_address.is_default)

    def test_invoice_download_requires_owner(self):
        order = Order.objects.create(
            user=self.user,
            order_id='ORDER-OWNER-1',
            payment_status='Pending',
            payment_mode='Cash on Delivery',
            order_total_price=800,
            grand_total=800,
        )
        response = self.client.get(reverse('download_invoice', args=[order.order_id]))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(User.objects.create_user(username='other'))
        response = self.client.get(reverse('download_invoice', args=[order.order_id]))
        self.assertEqual(response.status_code, 404)

    def test_order_details_and_confirmation_show_delivery_fee(self):
        order = Order.objects.create(
            user=self.user,
            order_id='ORDER-DELIVERY-FEE',
            payment_status='Pending',
            payment_mode='Cash on Delivery',
            outside_service_area=True,
            order_total_price=800,
            additional_delivery_fee=80,
            grand_total=880,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('order_details', args=[order.order_id]))
        self.assertContains(response, 'Delivery charges')
        self.assertContains(response, '₹80.00')
        self.assertContains(response, '₹880.00')
        self.assertContains(response, 'Outside Standard Service Area')

        confirmation = render_to_string('emails/guest_order_confirmation.html', {
            'order': order,
            'order_items': [],
            'customer_name': 'Alice',
            'tracking_link': '/track/',
        })
        self.assertIn('INR 80', confirmation)
        self.assertIn('Outside Standard Service Area', confirmation)
        self.assertNotIn('reviewing delivery availability', confirmation)

        with override_settings(ADMIN_EMAIL='orders@example.com'):
            send_admin_new_order_email(order)
        self.assertIn('Outside Standard Service Area', mail.outbox[-1].body)
        self.assertIn('Total: INR 880', mail.outbox[-1].body)

    def test_order_details_highlights_current_stage_and_deduplicates_return_eligibility(self):
        order = Order.objects.create(
            user=self.user,
            order_id='ORDER-DETAILS-CURRENT-STAGE',
            status=Order.Status.DELIVERED,
            payment_status='Paid',
            payment_mode='Razorpay',
            order_total_price=800,
            grand_total=800,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('order_details', args=[order.order_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Delivery progress')
        self.assertContains(response, '.progress-step.current { color: #856404;')
        self.assertContains(
            response,
            '.progress-step.current .progress-dot { border-color: #ffc107;',
        )
        self.assertContains(response, 'class="current-status-callout mt-3"')
        self.assertContains(
            response,
            'class="current-status-value">Delivered</strong>',
        )
        self.assertEqual(
            response.content.decode().count(
                'This order does not contain any eligible products.'),
            1,
        )

    def test_order_details_shows_return_cycle_instead_of_delivery_progress(self):
        order = Order.objects.create(
            user=self.user,
            order_id='ORDER-DETAILS-RETURN-CYCLE',
            status=Order.Status.DELIVERED,
            payment_status='Paid',
            payment_mode='Razorpay',
            order_total_price=800,
            grand_total=800,
        )
        return_request = ReturnRequest.objects.create(
            order=order,
            reason='Changed my mind.',
            status=ReturnRequest.Status.SHIPPED,
            shipped_at=timezone.now(),
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('order_details', args=[order.order_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Return status')
        self.assertContains(response, 'Request status cycle')
        self.assertContains(response, 'Return shipped')
        self.assertContains(response, return_request.get_status_display())
        self.assertEqual(
            [step['state'] for step in response.context['return_timeline']],
            ['complete', 'complete', 'current', 'upcoming', 'upcoming', 'upcoming'],
        )
        self.assertNotContains(response, 'Delivery progress')

    @override_settings(
        ONLINE_PAYMENT_ENABLED=True,
        RAZORPAY_KEY_ID='test-key',
        RAZORPAY_KEY_SECRET='test-secret',
    )
    def test_online_payment_amount_includes_delivery_fee(self):
        guideline = DeliveryGuideline.get_solo()
        guideline.delivery_charge_threshold = 1000
        guideline.delivery_fee = 80
        guideline.save()
        self.client.force_login(self.user)

        with patch('accounts.views.razorpay.Client') as razorpay_client:
            razorpay_client.return_value.order.create.return_value = {
                'id': 'payment-order-1',
                'amount': 88000,
                'currency': 'INR',
            }
            response = self.client.post(reverse('create_payment_order'))

        self.assertEqual(response.status_code, 200)
        amount = razorpay_client.return_value.order.create.call_args.args[0]['amount']
        self.assertEqual(amount, 88000)

    @override_settings(
        ONLINE_PAYMENT_ENABLED=True,
        RAZORPAY_KEY_ID='test-key',
        RAZORPAY_KEY_SECRET='test-secret',
    )
    def test_online_payment_rejects_address_outside_service_city(self):
        state = State.objects.get(country_id='IN', name='Delhi')
        address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Street',
            street_number='12',
            zip_code='110001',
            city=city_for_state(state, 'New Delhi'),
            country='IN',
            state=state,
            phone='9999999999',
        )
        self.client.force_login(self.user)

        with patch('accounts.views.razorpay.Client') as razorpay_client:
            response = self.client.post(
                reverse('create_payment_order'),
                data=json.dumps({'selected_address_id': str(address.uid)}),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn('Dehradun city only', response.json()['error'])
        razorpay_client.assert_not_called()

    def test_order_creation_consumes_stock_atomically(self):
        self.product.stock_quantity = 2
        self.product.save(update_fields=['stock_quantity'])
        CartItem.objects.filter(cart=self.cart).update(quantity=2)

        order = create_order(
            self.cart,
            order_id='ORDER-STOCK-1',
            payment_status='Pending',
            payment_mode='Cash on Delivery',
        )

        self.assertIsNotNone(order)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 0)

        insufficient_cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=insufficient_cart, product=self.product, quantity=1)
        with self.assertRaisesMessage(ValueError, 'no longer available'):
            create_order(
                insufficient_cart,
                order_id='ORDER-STOCK-2',
                payment_status='Pending',
                payment_mode='Cash on Delivery',
            )
        self.assertFalse(Order.objects.filter(order_id='ORDER-STOCK-2').exists())

    def test_order_creation_rejects_address_outside_service_city(self):
        outside_address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Street',
            street_number='12',
            zip_code='110001',
            city=city_for_state(
                State.objects.get(country_id='IN', name='Delhi'), 'New Delhi'
            ),
            country='IN',
            state=State.objects.get(country_id='IN', name='Delhi'),
            phone='9999999999',
        )

        with self.assertRaisesMessage(
            ValueError, 'We are currently operational in Dehradun city only.',
        ):
            create_order(
                self.cart,
                order_id='ORDER-OUTSIDE-CITY',
                payment_status='Pending',
                payment_mode='Cash on Delivery',
                shipping_address=outside_address,
            )

        self.assertFalse(Order.objects.filter(order_id='ORDER-OUTSIDE-CITY').exists())

    def test_unlisted_pincode_in_service_city_is_accepted(self):
        address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Street',
            street_number='12',
            zip_code='248099',
            city=self.city,
            country='IN',
            state=self.state,
            phone='9999999999',
        )

        order = create_order(
            self.cart,
            order_id='ORDER-UNLISTED-PINCODE',
            payment_status='Pending',
            payment_mode='Cash on Delivery',
            shipping_address=address,
        )

        self.assertEqual(order.status, Order.Status.ACCEPTED)
        self.assertTrue(order.outside_service_area)
        self.assertEqual(order.delivery_pincode, '248099')

    def test_listed_pincode_starts_order_as_accepted(self):
        ServiceablePincode.objects.create(pincode='248001')
        guideline = DeliveryGuideline.get_solo()
        guideline.delivery_charge_threshold = 801
        guideline.delivery_fee = 80
        guideline.save()
        address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Street',
            street_number='12',
            zip_code='248001',
            city=self.city,
            country='IN',
            state=self.state,
            phone='9999999999',
        )

        order = create_order(
            self.cart,
            order_id='ORDER-SERVICEABLE',
            payment_status='Pending',
            payment_mode='Cash on Delivery',
            shipping_address=address,
        )

        self.assertEqual(order.status, Order.Status.ACCEPTED)
        self.assertFalse(order.outside_service_area)
        self.assertEqual(order.additional_delivery_fee, 80)
        self.assertEqual(order.grand_total, 880)

    def test_base_city_order_above_threshold_is_free_outside_local_radius(self):
        guideline = DeliveryGuideline.get_solo()
        guideline.operational_office_city = 'Dehradun'
        guideline.free_delivery_radius_km = 1
        guideline.delivery_charge_threshold = 799
        guideline.delivery_fee = 80
        guideline.save()
        address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Street',
            street_number='12',
            zip_code='110001',
            city=self.city,
            country='IN',
            state=self.state,
            phone='9999999999',
        )

        order = create_order(
            self.cart,
            order_id='ORDER-BASE-CITY-FREE',
            payment_status='Pending',
            payment_mode='Cash on Delivery',
            shipping_address=address,
        )

        self.assertEqual(order.additional_delivery_fee, None)
        self.assertEqual(order.grand_total, 800)

    def test_order_tracking_requires_matching_access_token(self):
        order = Order.objects.create(
            user=self.user,
            order_id='ORDER-TRACK-1',
            guest_access_token='tracking-token-123',
            payment_status='Pending',
            payment_mode='Cash on Delivery',
            order_total_price=800,
            grand_total=800,
        )

        response = self.client.get(reverse('track_order', args=[
            order.order_id, order.guest_access_token,
        ]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Order accepted')
        self.assertContains(response, 'class="current-status-callout"')
        self.assertContains(response, 'class="current-status-value">Order accepted</strong>')
        tracking_html = response.content.decode()
        self.assertIn('class="order-delivery-card mt-4"', tracking_html)
        self.assertLess(
            tracking_html.index('class="order-progress'),
            tracking_html.index('Delivery details'),
        )
        self.assertIn('class="order-delivery-grid"', tracking_html)
        self.assertIn('@media (max-width: 767.98px)', tracking_html)
        self.assertIn('@media (max-width: 575px)', tracking_html)
        self.assertIn('class="order-tracking-actions"', tracking_html)
        self.assertLess(
            tracking_html.index('</section>', tracking_html.index('order-delivery-card')),
            tracking_html.index('order-tracking-actions'),
        )
        self.assertIn('View full order details', tracking_html)
        self.assertIn('.order-tracking-actions form, .order-tracking-actions .btn { width: 100%; }', tracking_html)

        response = self.client.get(reverse('track_order', args=[
            order.order_id, 'wrong-token',
        ]))
        self.assertEqual(response.status_code, 404)

    def test_order_status_change_emails_customer_once(self):
        order = Order.objects.create(
            user=self.user,
            order_id='ORDER-STATUS-1',
            guest_access_token='status-token-123',
            payment_status='Paid',
            payment_mode='Razorpay',
            order_total_price=800,
            grand_total=800,
        )
        self.assertEqual(len(mail.outbox), 0)

        order.status = Order.Status.DISPATCHED
        order.save(update_fields=['status', 'updated_at'])

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Dispatched', mail.outbox[0].body)
        self.assertIn('ORDER-STATUS-1', mail.outbox[0].subject)
        self.assertIn('status-token-123', mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].alternatives[0][1], 'text/html')
        self.assertIn('cid:bundleofgifts-brand-icon', mail.outbox[0].alternatives[0][0])

        order.save(update_fields=['status', 'updated_at'])
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(ADMIN_EMAIL='ops@example.com, warehouse@example.com')
    def test_new_order_notification_reaches_admin_team(self):
        order = Order.objects.create(
            user=self.user,
            order_id='BOG-20260919-ABC123',
            payment_status='Paid',
            payment_mode='Razorpay',
            order_total_price=800,
            grand_total=800,
        )

        send_admin_new_order_email(order)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].to,
            ['ops@example.com', 'warehouse@example.com'],
        )
        self.assertIn('BOG-20260919-ABC123', mail.outbox[0].subject)
        self.assertIn('Open this order in the seller portal', mail.outbox[0].body)
        self.assertIn('Open order details', mail.outbox[0].alternatives[0][0])
        self.assertIn('seller portal', mail.outbox[0].body)
        self.assertNotIn('in admin', mail.outbox[0].body.lower())


class OrderProductReviewTests(TestCase):
    def setUp(self):
        self.media_dir = TemporaryDirectory()
        media_settings = override_settings(MEDIA_ROOT=self.media_dir.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.addCleanup(self.media_dir.cleanup)

        self.user = User.objects.create_user(
            username='order-review-customer', password='secret123')
        category = Category.objects.create(category_name='Reviewable gifts')
        self.products = [
            Product.objects.create(
                product_name=f'Review gift {index}',
                category=category,
                price=500,
                product_desription='A reviewable gift.',
            )
            for index in range(1, 4)
        ]
        self.order = Order.objects.create(
            user=self.user,
            order_id='ORDER-PRODUCT-REVIEW',
            status=Order.Status.DELIVERED,
            payment_status='Paid',
            payment_mode='Razorpay',
            order_total_price=1500,
            grand_total=1500,
        )
        for product in self.products[:2]:
            OrderItem.objects.create(
                order=self.order,
                product=product,
                quantity=1,
                product_price=product.price,
            )
        ProductImage.objects.create(
            product=self.products[0],
            image_url='products/review-product.jpg',
        )
        bundle = BundleOrderItem.objects.create(
            order=self.order,
            title='Review gift bundle',
            quantity=1,
            original_unit_price=500,
            unit_price=500,
        )
        BundleOrderProduct.objects.create(
            bundle_item=bundle,
            product=self.products[2],
        )
        self.client.force_login(self.user)

    @staticmethod
    def create_test_image():
        image_data = BytesIO()
        Image.new('RGB', (1, 1), color='blue').save(image_data, format='PNG')
        return SimpleUploadedFile(
            'order-review.png', image_data.getvalue(), content_type='image/png')

    def test_my_reviews_cards_frame_product_image_and_display_star_rating(self):
        ProductReview.objects.create(
            product=self.products[0],
            user=self.user,
            stars=4,
            content='A lovely gift.',
        )

        response = self.client.get(reverse('product_reviews'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="review-image-frame"')
        self.assertContains(response, 'aria-label="4 out of 5 stars"')
        self.assertEqual(response.content.decode().count(
            '<i class="fas fa-star" aria-hidden="true"></i>'), 4)
        self.assertEqual(response.content.decode().count(
            '<i class="far fa-star" aria-hidden="true"></i>'), 1)
        self.assertNotContains(response, '4/5')

    def test_delivered_order_shows_a_review_form_for_each_unique_product(self):
        response = self.client.get(
            reverse('order_details', args=[self.order.order_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Review your delivered products')
        self.assertContains(response, 'Photos are optional.')
        for product in self.products:
            self.assertContains(response, product.product_name)
            self.assertContains(
                response,
                reverse(
                    'review_order_product',
                    args=[self.order.order_id, product.uid],
            ))
        self.assertEqual(response.content.decode().count('<form class="order-review-form'), 3)
        self.assertEqual(response.content.decode().count('name="photos"'), 3)
        self.assertEqual(response.content.decode().count('class="order-review-item"'), 3)
        self.assertEqual(response.content.decode().count('class="order-review-star'), 15)
        self.assertEqual(response.content.decode().count('class="order-review-fields" hidden'), 3)
        self.assertContains(
            response,
            'src="/media/products/review-product.jpg" alt="Review gift 1"',
        )
        self.assertContains(response, 'object-fit: contain;')
        self.assertContains(response, '<i class="fas fa-gift"></i>', html=False)

    def test_customer_can_review_each_delivered_order_product_and_upload_optional_photo(self):
        first_product, second_product, bundle_product = self.products
        first_response = self.client.post(
            reverse(
                'review_order_product',
                args=[self.order.order_id, first_product.uid],
            ),
            {
                'stars': 5,
                'content': 'Arrived in great condition.',
                'photos': self.create_test_image(),
            },
        )
        second_response = self.client.post(
            reverse(
                'review_order_product',
                args=[self.order.order_id, second_product.uid],
            ),
            {'stars': 4, 'content': 'Good quality.'},
        )
        bundle_response = self.client.post(
            reverse(
                'review_order_product',
                args=[self.order.order_id, bundle_product.uid],
            ),
            {'stars': 3, 'content': 'Nice bundle item.'},
        )

        for response in (first_response, second_response, bundle_response):
            self.assertRedirects(
                response, reverse('order_details', args=[self.order.order_id]))
        self.assertEqual(
            ProductReview.objects.filter(user=self.user).count(), 3)
        first_review = ProductReview.objects.get(
            user=self.user, product=first_product)
        self.assertEqual(first_review.photos.count(), 1)
        self.assertEqual(
            ProductReview.objects.get(
                user=self.user, product=second_product).photos.count(),
            0,
        )
        self.client.post(
            reverse(
                'review_order_product',
                args=[self.order.order_id, first_product.uid],
            ),
            {'stars': 4, 'content': 'Updated review.'},
        )
        self.assertEqual(
            ProductReview.objects.filter(
                user=self.user, product=first_product).count(),
            1,
        )
        self.assertEqual(first_review.photos.count(), 1)
        response = self.client.get(
            reverse('order_details', args=[self.order.order_id]))
        self.assertEqual(response.content.decode().count('class="order-review-summary"'), 3)
        self.assertContains(response, 'order-review-edit')
        self.assertContains(response, 'Update')
        self.assertEqual(
            response.content.decode().count('order-review-form-existing"'),
            3,
        )
        product_page = self.client.get(
            reverse('get_product', args=[first_product.slug]))
        self.assertContains(product_page, 'Updated review.')
        reviews_page = self.client.get(
            reverse('product_reviews_for_product', args=[first_product.slug]))
        self.assertContains(reviews_page, 'Updated review.')
        my_reviews_page = self.client.get(reverse('product_reviews'))
        self.assertContains(my_reviews_page, 'Updated review.')

    def test_reviews_are_hidden_and_rejected_before_delivery(self):
        self.order.status = Order.Status.ACCEPTED
        self.order.save(update_fields=['status', 'updated_at'])
        response = self.client.get(
            reverse('order_details', args=[self.order.order_id]))
        self.assertNotContains(response, 'Review your delivered products')

        response = self.client.post(
            reverse(
                'review_order_product',
                args=[self.order.order_id, self.products[0].uid],
            ),
            {'stars': 5, 'content': 'Too early.'},
        )

        self.assertRedirects(
            response, reverse('order_details', args=[self.order.order_id]))
        self.assertFalse(
            ProductReview.objects.filter(user=self.user).exists())

    def test_review_endpoint_rejects_products_not_in_the_order(self):
        other_product = Product.objects.create(
            product_name='Not ordered gift',
            category=self.products[0].category,
            price=400,
            product_desription='Not part of this order.',
        )

        response = self.client.post(
            reverse(
                'review_order_product',
                args=[self.order.order_id, other_product.uid],
            ),
            {'stars': 5, 'content': 'Not purchased.'},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            ProductReview.objects.filter(user=self.user).exists())

        other_user = User.objects.create_user(username='other-review-customer')
        self.client.force_login(other_user)
        response = self.client.post(
            reverse(
                'review_order_product',
                args=[self.order.order_id, self.products[0].uid],
            ),
            {'stars': 5, 'content': 'Not my order.'},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(ProductReview.objects.exists())


class ReturnWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='return-customer',
            email='return@example.com',
            password='secret123',
        )
        category = Category.objects.create(category_name='Returnable gifts')
        self.product = Product.objects.create(
            product_name='Returnable gift',
            category=category,
            price=1000,
            product_desription='A gift eligible for return.',
            stock_quantity=3,
        )
        self.order = Order.objects.create(
            user=self.user,
            order_id='ORDER-RETURN-1',
            guest_access_token='return-guest-token',
            status=Order.Status.DELIVERED,
            payment_status='Paid',
            payment_mode='Razorpay',
            razorpay_payment_id='pay-original-1',
            order_total_price=1000,
            grand_total=1000,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            quantity=1,
            product_price=1000,
        )
        self.client.force_login(self.user)

    def create_request_photo(self, name='product.png'):
        image_buffer = BytesIO()
        Image.new('RGB', (2, 2), color='red').save(image_buffer, format='PNG')
        return SimpleUploadedFile(
            name, image_buffer.getvalue(), content_type='image/png')

    def test_customer_request_is_saved_and_admin_is_emailed(self):
        with TemporaryDirectory() as media_root, override_settings(
            ADMIN_EMAIL='returns@example.com',
            MEDIA_ROOT=media_root,
            PUBLIC_BASE_URL='https://shop.example',
        ):
            response = self.client.post(
                reverse('return_request', args=[self.order.order_id]),
                {
                    'action': 'request',
                    'request_type': ReturnRequest.RequestType.RETURN,
                    'return_reason': 'defective_or_doesnt_work',
                    'request_images': [self.create_request_photo()],
                },
            )
            return_request = ReturnRequest.objects.get(order=self.order)
            detail_response = self.client.get(
                reverse('return_request', args=[self.order.order_id]))
            self.assertContains(detail_response, 'Photos submitted with your request')
            self.assertContains(detail_response, 'Item condition evidence')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(return_request.status, ReturnRequest.Status.REQUESTED)
        self.assertEqual(return_request.request_images.count(), 1)
        self.assertEqual(return_request.reason, "Defective or doesn't work")
        self.assertEqual(mail.outbox[-1].to, ['returns@example.com'])
        self.assertIn(self.order.order_id, mail.outbox[-1].body)
        self.assertIn("Defective or doesn't work", mail.outbox[-1].body)
        customer_email = next(
            email for email in mail.outbox
            if self.user.email in email.to
        )
        self.assertEqual(customer_email.alternatives[0][1], 'text/html')
        self.assertIn('cid:bundleofgifts-brand-icon', customer_email.alternatives[0][0])
        self.assertIn(
            f'https://shop.example{reverse("return_request", args=[self.order.order_id])}',
            customer_email.body,
        )
        self.assertIn('awaiting seller review', customer_email.body)
        self.assertNotIn('admin review', customer_email.body.lower())

    def test_return_requires_details_when_reason_is_not_defined(self):
        response = self.client.post(
            reverse('return_request', args=[self.order.order_id]),
            {
                'action': 'request',
                'request_type': ReturnRequest.RequestType.RETURN,
                'return_reason': 'not_defined',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please tell us why you are returning this order.')
        self.assertFalse(ReturnRequest.objects.filter(order=self.order).exists())

    def test_custom_return_reason_is_saved_with_comment(self):
        with TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root,
        ):
            response = self.client.post(
                reverse('return_request', args=[self.order.order_id]),
                {
                    'action': 'request',
                    'request_type': ReturnRequest.RequestType.RETURN,
                    'return_reason': 'not_defined',
                    'reason': 'The product did not meet my needs.',
                    'request_images': [self.create_request_photo()],
                },
            )

        self.assertEqual(response.status_code, 302)
        return_request = ReturnRequest.objects.get(order=self.order)
        self.assertEqual(
            return_request.reason,
            'Reason not defined: The product did not meet my needs.',
        )

    def test_return_form_shows_predefined_reasons_and_custom_comment_control(self):
        response = self.client.get(
            reverse('return_request', args=[self.order.order_id]))

        self.assertEqual(
            [label for _value, label in response.context['form'].fields[
                'return_reason'
            ].choices][1:],
            [
                'Bought by mistake',
                'Better price available',
                'No longer needed',
                'Item arrived too late',
                "Defective or doesn't work",
                'Shipping box is damaged, but the product is ok',
                'Product is damaged, but shipping box is ok',
                'Incorrect item was sent (Wrong item/color/size)',
                'Missing parts or accessories',
                'Performance or quality not adequate',
                'Inaccurate website description',
                'Reason not defined',
            ],
        )
        self.assertContains(response, 'id="reason-details-field"')
        self.assertContains(response, 'id="return-reason-required"')
        self.assertContains(response, 'id="replacement-reason-required"')
        self.assertContains(response, 'class="required-indicator"')
        self.assertContains(response, '(optional for returns)')
        self.assertContains(response, '(required for replacements)')
        self.assertNotContains(response, 'id="cod-refund-details"')

    @override_settings(ADMIN_EMAIL='returns@example.com')
    def test_cod_return_request_collects_upi_id_privately(self):
        self.order.payment_mode = 'Cash on Delivery'
        self.order.save(update_fields=['payment_mode', 'updated_at'])
        response = self.client.get(
            reverse('return_request', args=[self.order.order_id]))

        self.assertContains(response, 'id="cod-refund-details"')
        self.assertContains(response, 'UPI ID')
        self.assertContains(response, 'Bank account details')
        self.assertContains(response, 'id="refund-method-required"')
        self.assertContains(response, 'id="refund-upi-required"')
        self.assertContains(response, 'id="refund-account-holder-required"')
        self.assertContains(response, 'id="refund-account-number-required"')
        self.assertContains(response, 'id="refund-ifsc-required"')

        with TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root,
        ):
            response = self.client.post(
                reverse('return_request', args=[self.order.order_id]),
                {
                    'action': 'request',
                    'request_type': ReturnRequest.RequestType.RETURN,
                    'return_reason': 'no_longer_needed',
                    'refund_method': ReturnRequest.RefundMethod.UPI,
                    'refund_upi_id': 'customer@okaxis',
                    'request_images': [self.create_request_photo()],
                },
            )

        self.assertEqual(response.status_code, 302)
        return_request = ReturnRequest.objects.get(order=self.order)
        self.assertEqual(
            return_request.refund_method, ReturnRequest.RefundMethod.UPI)
        self.assertEqual(return_request.refund_upi_id, 'customer@okaxis')
        self.assertEqual(return_request.refund_account_number, '')
        self.assertNotIn('customer@okaxis', mail.outbox[-1].body)

        admin_user = User.objects.create_superuser(
            username='refund-admin',
            email='refund-admin@example.com',
            password='secret123',
        )
        self.client.force_login(admin_user)
        admin_response = self.client.get(reverse(
            'admin:accounts_returnrequest_change',
            args=[return_request.pk],
        ))
        self.assertEqual(admin_response.status_code, 200)
        self.assertContains(admin_response, 'customer@okaxis')

    def test_cod_return_request_collects_bank_account_details(self):
        self.order.payment_mode = 'Cash on Delivery'
        self.order.save(update_fields=['payment_mode', 'updated_at'])

        with TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root,
        ):
            response = self.client.post(
                reverse('return_request', args=[self.order.order_id]),
                {
                    'action': 'request',
                    'request_type': ReturnRequest.RequestType.RETURN,
                    'return_reason': 'no_longer_needed',
                    'refund_method': ReturnRequest.RefundMethod.BANK_ACCOUNT,
                    'refund_account_holder_name': 'Return Customer',
                    'refund_account_number': '123456789012',
                    'refund_ifsc_code': 'ABCD0123456',
                    'request_images': [self.create_request_photo()],
                },
            )

        self.assertEqual(response.status_code, 302)
        return_request = ReturnRequest.objects.get(order=self.order)
        self.assertEqual(
            return_request.refund_method,
            ReturnRequest.RefundMethod.BANK_ACCOUNT,
        )
        self.assertEqual(
            return_request.refund_account_holder_name, 'Return Customer')
        self.assertEqual(
            return_request.refund_account_number, '123456789012')
        self.assertEqual(return_request.refund_ifsc_code, 'ABCD0123456')
        self.assertEqual(return_request.refund_upi_id, '')

    def test_cod_return_request_requires_valid_payout_details(self):
        self.order.payment_mode = 'Cash on Delivery'
        self.order.save(update_fields=['payment_mode', 'updated_at'])

        shipment_url = reverse(
            'return_request', args=[self.order.order_id])
        base_data = {
            'action': 'request',
            'request_type': ReturnRequest.RequestType.RETURN,
            'return_reason': 'no_longer_needed',
        }
        invalid_payout_details = (
            (
                {},
                'Choose UPI ID or bank account',
            ),
            (
                {
                    'refund_method': ReturnRequest.RefundMethod.UPI,
                    'refund_upi_id': 'not-a-valid-upi',
                },
                'Enter a valid UPI ID.',
            ),
            (
                {
                    'refund_method': ReturnRequest.RefundMethod.BANK_ACCOUNT,
                    'refund_account_holder_name': 'Return Customer',
                    'refund_account_number': '123',
                    'refund_ifsc_code': 'INVALID',
                },
                'Enter a valid 9 to 18 digit bank account number.',
            ),
        )
        for details, expected_error in invalid_payout_details:
            with self.subTest(expected_error=expected_error):
                response = self.client.post(
                    shipment_url,
                    {
                        **base_data,
                        **details,
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected_error)
                self.assertFalse(
                    ReturnRequest.objects.filter(order=self.order).exists())

    def test_return_request_page_shows_complete_return_status_cycle(self):
        return_request = ReturnRequest.objects.create(
            order=self.order,
            reason='Changed my mind.',
            status=ReturnRequest.Status.SHIPPED,
            shipped_at=timezone.now(),
        )

        response = self.client.get(
            reverse('return_request', args=[self.order.order_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="table return-item-table"')
        self.assertContains(response, 'Returnable gift')
        self.assertContains(response, 'Line total')
        self.assertEqual(
            [step['state'] for step in response.context['return_timeline']],
            ['complete', 'complete', 'current', 'upcoming', 'upcoming', 'upcoming'],
        )
        self.assertContains(response, 'Request status cycle')
        self.assertContains(response, 'Return shipped')
        self.assertContains(response, return_request.get_status_display())

    def test_replacement_request_page_shows_replacement_status_cycle(self):
        return_request = ReturnRequest.objects.create(
            order=self.order,
            request_type=ReturnRequest.RequestType.REPLACEMENT,
            reason='Wrong item received',
            status=ReturnRequest.Status.REPLACEMENT_DISPATCHED,
            replacement_dispatched_at=timezone.now(),
        )

        response = self.client.get(
            reverse('return_request', args=[self.order.order_id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [step['state'] for step in response.context['return_timeline']],
            ['complete', 'complete', 'complete', 'current', 'upcoming'],
        )
        self.assertContains(response, 'Replacement dispatched')
        self.assertContains(response, return_request.get_status_display())

    def test_guest_status_email_links_to_guest_return_tracking_page(self):
        self.order.user = None
        self.order.guest_email = 'guest-return@example.com'
        self.order.save(update_fields=['user', 'guest_email', 'updated_at'])
        with override_settings(PUBLIC_BASE_URL='https://shop.example'):
            ReturnRequest.objects.create(
                order=self.order,
                reason='No longer needed',
            )
        guest_email = next(
            email for email in mail.outbox
            if 'guest-return@example.com' in email.to
        )
        guest_status_url = 'https://shop.example' + reverse(
            'return_request_guest',
            args=[self.order.order_id, self.order.guest_access_token],
        )
        self.assertIn(guest_status_url, guest_email.body)


    def test_return_is_not_available_before_delivery_or_after_window(self):
        self.order.status = Order.Status.DISPATCHED
        self.order.save(update_fields=['status', 'updated_at'])
        response = self.client.get(
            reverse('return_request', args=[self.order.order_id]))
        self.assertContains(response, 'only be requested after the order is delivered')

        self.order.status = Order.Status.DELIVERED
        self.order.delivered_at = timezone.now() - timedelta(days=8)
        self.order.save(update_fields=['status', 'delivered_at', 'updated_at'])
        response = self.client.get(
            reverse('return_request', args=[self.order.order_id]))
        self.assertContains(response, 'return window')

    @override_settings(ADMIN_EMAIL='returns@example.com')
    def test_approved_customer_can_submit_tracking_and_required_photos(self):
        return_request = ReturnRequest.objects.create(
            order=self.order, reason='Changed my mind.',
            status=ReturnRequest.Status.APPROVED,
        )
        from home.models import ReturnRefundPolicy

        policy = ReturnRefundPolicy.get_solo()
        policy.return_shipping_address = '10 Returns Road\nDehradun, India'
        policy.save()

        image_buffer = BytesIO()
        Image.new('RGB', (2, 2), color='red').save(image_buffer, format='PNG')
        image = SimpleUploadedFile(
            'parcel.png', image_buffer.getvalue(), content_type='image/png')
        with TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root,
        ):
            response = self.client.post(
                reverse('return_request', args=[self.order.order_id]),
                {
                    'action': 'shipment',
                    'courier_partner_name': 'Parcel Express',
                    'tracking_number': 'TRACK-123',
                    'shipment_images': [image],
                },
            )

        self.assertEqual(response.status_code, 302)
        return_request.refresh_from_db()
        self.assertEqual(return_request.status, ReturnRequest.Status.SHIPPED)
        self.assertEqual(
            return_request.courier_partner_name, 'Parcel Express')
        self.assertEqual(return_request.tracking_number, 'TRACK-123')
        self.assertIsNotNone(return_request.shipped_at)
        self.assertEqual(return_request.shipment_images.count(), 1)
        self.assertIn('Parcel Express', mail.outbox[-1].body)
        self.assertIn('TRACK-123', mail.outbox[-1].body)
        status_response = self.client.get(
            reverse('return_request', args=[self.order.order_id]))
        self.assertContains(status_response, 'Parcel Express')
        self.assertContains(status_response, 'TRACK-123')

    def test_return_shipment_requires_courier_partner_name(self):
        return_request = ReturnRequest.objects.create(
            order=self.order, reason='Changed my mind.',
            status=ReturnRequest.Status.APPROVED,
        )

        response = self.client.post(
            reverse('return_request', args=[self.order.order_id]),
            {
                'action': 'shipment',
                'tracking_number': 'TRACK-123',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enter the courier partner name.')
        return_request.refresh_from_db()
        self.assertEqual(return_request.status, ReturnRequest.Status.APPROVED)
        self.assertEqual(return_request.tracking_number, '')

    def test_guest_order_can_raise_return_with_its_tracking_token(self):
        self.order.user = None
        self.order.guest_email = 'guest-return@example.com'
        self.order.guest_name = 'Guest Buyer'
        self.order.save(update_fields=['user', 'guest_email', 'guest_name', 'updated_at'])
        self.client.logout()

        with TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root,
        ):
            response = self.client.post(
                reverse('return_request_guest', args=[
                    self.order.order_id, self.order.guest_access_token,
                ]),
                {
                    'action': 'request',
                    'request_type': ReturnRequest.RequestType.RETURN,
                    'return_reason': 'no_longer_needed',
                    'reason': 'Guest return reason.',
                    'request_images': [self.create_request_photo()],
                },
            )

        self.assertEqual(response.status_code, 302)
        return_request = ReturnRequest.objects.get(order=self.order)
        self.assertEqual(return_request.reason, 'No longer needed')
        self.assertEqual(return_request.request_images.count(), 1)

    def test_return_request_can_be_submitted_without_product_photos(self):
        page_response = self.client.get(
            reverse('return_request', args=[self.order.order_id]))

        self.assertContains(page_response, '(optional for returns)')
        self.assertContains(page_response, '(required for replacements)')
        self.assertNotContains(
            page_response,
            'id="request_images" name="request_images" type="file" accept="image/*" multiple required',
            html=False,
        )

        response = self.client.post(
            reverse('return_request', args=[self.order.order_id]),
            {
                'action': 'request',
                'request_type': ReturnRequest.RequestType.RETURN,
                'return_reason': 'no_longer_needed',
            },
        )

        self.assertEqual(response.status_code, 302)
        return_request = ReturnRequest.objects.get(order=self.order)
        self.assertEqual(return_request.status, ReturnRequest.Status.REQUESTED)
        self.assertEqual(return_request.reason, 'No longer needed')
        self.assertEqual(return_request.request_images.count(), 0)

    @override_settings(ADMIN_EMAIL='returns@example.com')
    def test_customer_can_request_replacement_with_configurable_reason_and_photos(self):
        replacement_reason = ReplacementReason.objects.get(
            name='Wrong item received')
        image_buffer = BytesIO()
        Image.new('RGB', (2, 2), color='blue').save(image_buffer, format='PNG')
        image = SimpleUploadedFile(
            'wrong-item.png', image_buffer.getvalue(), content_type='image/png')
        with TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root,
        ):
            response = self.client.post(
                reverse('return_request', args=[self.order.order_id]),
                {
                    'action': 'request',
                    'request_type': ReturnRequest.RequestType.REPLACEMENT,
                    'replacement_reason': replacement_reason.pk,
                    'reason': 'The delivered item does not match the order.',
                    'request_images': [image],
                },
            )

        self.assertEqual(response.status_code, 302)
        return_request = ReturnRequest.objects.get(order=self.order)
        self.assertEqual(
            return_request.request_type,
            ReturnRequest.RequestType.REPLACEMENT,
        )
        self.assertEqual(return_request.replacement_reason, replacement_reason)
        self.assertEqual(
            return_request.reason,
            'Wrong item received: The delivered item does not match the order.',
        )
        self.assertEqual(return_request.request_images.count(), 1)
        self.assertEqual(mail.outbox[-1].to, ['returns@example.com'])
        self.assertIn('replacement', mail.outbox[-1].subject.lower())
        self.assertIn('Wrong item received', mail.outbox[-1].body)
        self.assertIn('Request photos: 1', mail.outbox[-1].body)

    def test_replacement_request_requires_product_photo(self):
        from products.models import ReturnDetails

        ReturnDetails.objects.create(
            product=self.product,
            is_returnable=True,
            return_policy='replaceable',
            return_window_days=4,
        )
        response = self.client.get(
            reverse('return_request', args=[self.order.order_id]))

        self.assertContains(response, '(required for replacements)')
        self.assertRegex(
            response.content.decode(),
            r'id="request_images" name="request_images" type="file" '
            r'accept="image/\*" multiple\s+required',
        )

        replacement_reason = ReplacementReason.objects.get(
            name='Wrong item received')
        response = self.client.post(
            reverse('return_request', args=[self.order.order_id]),
            {
                'action': 'request',
                'request_type': ReturnRequest.RequestType.REPLACEMENT,
                'replacement_reason': replacement_reason.pk,
                'reason': 'The delivered item does not match the order.',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Upload at least 1 clear product photo for a replacement request.',
        )
        self.assertFalse(ReturnRequest.objects.filter(order=self.order).exists())

    def test_replacement_can_be_requested_for_products_marked_replaceable(self):
        from products.models import ReturnDetails

        ReturnDetails.objects.create(
            product=self.product,
            is_returnable=True,
            return_policy='replaceable',
            return_window_days=4,
        )
        response = self.client.get(
            reverse('return_request', args=[self.order.order_id]))

        self.assertEqual(
            list(response.context['form'].fields['request_type'].choices),
            [(ReturnRequest.RequestType.REPLACEMENT, 'Replacement')],
        )
        self.assertEqual(
            response.context['request_form_heading'],
            'Start a replacement',
        )
        self.assertEqual(
            response.context['default_request_type'],
            ReturnRequest.RequestType.REPLACEMENT,
        )
        self.assertContains(response, '<h2 id="request-form-heading">Start a replacement</h2>')
        self.assertContains(
            response,
            f'name="request_type" value="{ReturnRequest.RequestType.REPLACEMENT}" '
            'id="id_request_type_0" required checked',
            html=False,
        )
        response_html = response.content.decode()
        self.assertRegex(
            response_html,
            r'<div[^>]*id="return-reason-field"[^>]*\bhidden\b',
        )
        self.assertRegex(
            response_html,
            r'<div[^>]*id="return-shipping-notice"[^>]*\bhidden\b',
        )
        self.assertContains(response, 'Replacement reason')
        self.assertContains(response, 'Wrong item received')
        self.assertNotContains(response, 'Send an eligible item back for a refund')
        self.assertNotContains(response, 'is not eligible for a replacement')

    def test_returnable_product_shows_return_and_replacement_options(self):
        response = self.client.get(
            reverse('return_request', args=[self.order.order_id]))

        self.assertEqual(
            list(response.context['form'].fields['request_type'].choices),
            list(ReturnRequest.RequestType.choices),
        )
        self.assertEqual(
            response.context['request_form_heading'],
            'Start a return or replacement',
        )
        self.assertContains(response, 'Send an eligible item back for a refund')
        self.assertContains(response, 'Request the same item again')

    def test_replaceable_only_product_rejects_return_submission(self):
        from products.models import ReturnDetails

        ReturnDetails.objects.create(
            product=self.product,
            is_returnable=True,
            return_policy='replaceable',
            return_window_days=4,
        )
        response = self.client.post(
            reverse('return_request', args=[self.order.order_id]),
            {
                'action': 'request',
                'request_type': ReturnRequest.RequestType.RETURN,
                'return_reason': 'no_longer_needed',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Returnable gift is not eligible for a return.')
        self.assertFalse(ReturnRequest.objects.filter(order=self.order).exists())

    def test_replacement_admin_lifecycle_updates_customer(self):
        replacement_reason = ReplacementReason.objects.get(
            name='Damaged item received')
        return_request = ReturnRequest.objects.create(
            order=self.order,
            request_type=ReturnRequest.RequestType.REPLACEMENT,
            replacement_reason=replacement_reason,
            reason='Damaged item received: Broken on arrival',
        )
        admin_user = User.objects.create_superuser(
            username='replacement-admin',
            email='admin@example.com',
            password='secret123',
        )
        self.client.force_login(admin_user)
        changelist = reverse('admin:accounts_returnrequest_changelist')

        def run_action(action):
            return self.client.post(changelist, {
                'action': action,
                '_selected_action': [str(return_request.pk)],
                'select_across': '0',
                'index': '0',
            })

        with override_settings(
            ADMIN_EMAIL='',
            PUBLIC_BASE_URL='https://shop.example',
        ):
            response = run_action('approve_returns')
            self.assertEqual(response.status_code, 302)
            return_request.refresh_from_db()
            self.assertEqual(return_request.status, ReturnRequest.Status.APPROVED)
            self.assertIn('replacement request has been approved', mail.outbox[-1].body)
            self.assertIn(
                f'https://shop.example{reverse("return_request", args=[self.order.order_id])}',
                mail.outbox[-1].body,
            )

            response = run_action('start_replacement')
            self.assertEqual(response.status_code, 302)
            return_request.refresh_from_db()
            self.assertEqual(
                return_request.status,
                ReturnRequest.Status.REPLACEMENT_PREPARING,
            )

            return_request.replacement_tracking_number = 'OUTBOUND-123'
            return_request.save(update_fields=[
                'replacement_tracking_number', 'updated_at',
            ])
            response = run_action('dispatch_replacement')
            self.assertEqual(response.status_code, 302)
            return_request.refresh_from_db()
            self.assertEqual(
                return_request.status,
                ReturnRequest.Status.REPLACEMENT_DISPATCHED,
            )
            self.assertIn('OUTBOUND-123', mail.outbox[-1].body)

            response = run_action('complete_replacement')
            self.assertEqual(response.status_code, 302)
            return_request.refresh_from_db()
            self.assertEqual(
                return_request.status,
                ReturnRequest.Status.REPLACEMENT_COMPLETED,
            )

    @override_settings(
        RAZORPAY_KEY_ID='test-key',
        RAZORPAY_KEY_SECRET='test-secret',
        PUBLIC_BASE_URL='https://shop.example',
    )
    def test_admin_initiates_partial_razorpay_refund_after_receipt(self):
        return_request = ReturnRequest.objects.create(
            order=self.order,
            reason='Item condition review.',
            status=ReturnRequest.Status.RECEIVED,
            item_condition=ReturnRequest.ItemCondition.MINOR_DAMAGE,
            deduction_amount=125,
            deduction_reason='Minor damage on inspection.',
            received_at=timezone.now(),
        )
        admin_user = User.objects.create_superuser(
            username='return-admin',
            email='admin@example.com',
            password='secret123',
        )
        self.client.force_login(admin_user)
        razorpay_client_patcher = patch('accounts.admin.razorpay.Client')
        mock_client = razorpay_client_patcher.start()
        self.addCleanup(razorpay_client_patcher.stop)
        mock_client.return_value.payment.refund.return_value = {
            'id': 'rfnd-return-1',
            'status': 'pending',
        }

        response = self.client.post(
            reverse('admin:accounts_returnrequest_changelist'),
            {
                'action': 'initiate_online_refunds',
                '_selected_action': [str(return_request.pk)],
                'select_across': '0',
                'index': '0',
            },
        )

        self.assertEqual(response.status_code, 302)
        mock_client.return_value.payment.refund.assert_called_once_with(
            'pay-original-1',
            {
                'amount': 87500,
                'notes': {
                    'return_request_id': str(return_request.pk),
                    'order_id': self.order.order_id,
                },
            },
        )
        return_request.refresh_from_db()
        self.assertEqual(return_request.status, ReturnRequest.Status.REFUND_PENDING)
        self.assertEqual(return_request.refund_amount, 875)
        self.assertEqual(return_request.razorpay_refund_id, 'rfnd-return-1')
        refund_email = next(
            email for email in reversed(mail.outbox)
            if self.user.email in email.to
            and 'refund has been initiated' in email.body
        )
        self.assertIn(
            f'https://shop.example{reverse("return_request", args=[self.order.order_id])}',
            refund_email.body,
        )

    def test_admin_manual_cod_refund_requires_and_uses_payout_details(self):
        self.order.payment_mode = 'Cash on Delivery'
        self.order.save(update_fields=['payment_mode', 'updated_at'])
        return_request = ReturnRequest.objects.create(
            order=self.order,
            reason='Item condition review.',
            status=ReturnRequest.Status.RECEIVED,
            item_condition=ReturnRequest.ItemCondition.GOOD,
            refund_reference='UPI-transfer-123',
            received_at=timezone.now(),
        )
        admin_user = User.objects.create_superuser(
            username='cod-refund-admin',
            email='admin@example.com',
            password='secret123',
        )
        self.client.force_login(admin_user)
        action_url = reverse('admin:accounts_returnrequest_changelist')
        action_data = {
            'action': 'mark_manual_refunds_processed',
            '_selected_action': [str(return_request.pk)],
            'select_across': '0',
            'index': '0',
        }

        response = self.client.post(action_url, action_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any(
            'Add valid UPI or bank account details' in message.message
            for message in get_messages(response.wsgi_request)
        ))
        return_request.refresh_from_db()
        self.assertEqual(return_request.status, ReturnRequest.Status.RECEIVED)

        return_request.refund_method = ReturnRequest.RefundMethod.UPI
        return_request.refund_upi_id = 'customer@okaxis'
        return_request.save(update_fields=[
            'refund_method', 'refund_upi_id', 'updated_at',
        ])
        response = self.client.post(action_url, action_data)

        self.assertEqual(response.status_code, 302)
        return_request.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(return_request.status, ReturnRequest.Status.REFUNDED)
        self.assertEqual(return_request.refund_amount, self.order.grand_total)
        self.assertEqual(self.order.payment_status, 'Refund processed')

    def test_admin_can_update_return_status_and_email_calculated_refund(self):
        return_request = ReturnRequest.objects.create(
            order=self.order,
            reason='Item condition review.',
            status=ReturnRequest.Status.RECEIVED,
            item_condition=ReturnRequest.ItemCondition.MINOR_DAMAGE,
            received_at=timezone.now(),
        )
        admin_user = User.objects.create_superuser(
            username='return-status-admin',
            email='admin@example.com',
            password='secret123',
        )
        self.client.force_login(admin_user)
        change_url = reverse(
            'admin:accounts_returnrequest_change',
            args=[return_request.pk],
        )
        mail.outbox.clear()

        edit_response = self.client.get(change_url)
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(edit_response, 'INR 1,000.00')
        self.assertContains(edit_response, 'name="status"')

        admin_fields = {
            'status': ReturnRequest.Status.REFUND_PENDING,
            'admin_note': '',
            'item_condition': ReturnRequest.ItemCondition.MINOR_DAMAGE,
            'deduction_amount': '125.00',
            'deduction_reason': 'Minor damage on inspection.',
            'replacement_tracking_number': '',
            'refund_reference': '',
            'shipment_images-TOTAL_FORMS': '0',
            'shipment_images-INITIAL_FORMS': '0',
            'shipment_images-MIN_NUM_FORMS': '0',
            'shipment_images-MAX_NUM_FORMS': '1000',
            'request_images-TOTAL_FORMS': '0',
            'request_images-INITIAL_FORMS': '0',
            'request_images-MIN_NUM_FORMS': '0',
            'request_images-MAX_NUM_FORMS': '1000',
            '_save': 'Save',
        }
        response = self.client.post(change_url, admin_fields)
        self.assertEqual(response.status_code, 302)
        return_request.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(
            return_request.status,
            ReturnRequest.Status.REFUND_PENDING,
        )
        self.assertEqual(return_request.refund_amount, 875)
        self.assertEqual(self.order.payment_status, 'Refund pending')
        initiated_email = next(
            email for email in mail.outbox
            if self.user.email in email.to
            and 'refund has been initiated' in email.body
        )
        self.assertIn('Refund amount: INR 875.00', initiated_email.body)
        self.assertIn('Condition deduction: INR 125.00', initiated_email.body)
        self.assertIn(
            'Deduction reason: Minor damage on inspection.',
            initiated_email.body,
        )

        admin_fields['status'] = ReturnRequest.Status.REFUNDED
        response = self.client.post(change_url, admin_fields)
        self.assertEqual(response.status_code, 302)
        return_request.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(return_request.status, ReturnRequest.Status.REFUNDED)
        self.assertEqual(return_request.refund_amount, 875)
        self.assertIsNotNone(return_request.refunded_at)
        self.assertEqual(self.order.payment_status, 'Refund processed')
        processed_email = next(
            email for email in reversed(mail.outbox)
            if self.user.email in email.to
            and 'refund has been processed' in email.body
        )
        self.assertIn('Refund amount: INR 875.00', processed_email.body)
        self.assertIn(
            'Deduction reason: Minor damage on inspection.',
            processed_email.body,
        )
