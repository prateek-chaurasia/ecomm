import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from accounts.models import (
    BundleOrderItem, BundleOrderProduct, Cart, CartItem, Order, OrderItem,
    ServiceablePincode,
)
from accounts.forms import (
    GuestShippingAddressForm, RegistrationForm, ShippingAddressForm,
    UserUpdateForm,
)
from accounts.views import create_order
from base.emails import send_admin_new_order_email
from home.models import DeliveryGuideline, ReturnRefundPolicy, ShippingAddress, State
from products.models import Category, Product


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
        self.assertContains(response, 'Andhra Pradesh')

    def test_bound_form_filters_state_queryset_to_selected_country(self):
        form = ShippingAddressForm(data={'country': 'IN'})

        self.assertEqual(form.fields['state'].queryset.count(), 36)
        self.assertEqual(
            set(form.fields['state'].queryset.values_list('country__code', flat=True)),
            {'IN'},
        )

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
        state = State.objects.filter(country__code='IN').first()
        for field_name in ('first_name', 'last_name'):
            response = self.client.post(reverse('shipping-address'), {
                'first_name': 'Ash1a' if field_name == 'first_name' else 'Asha',
                'last_name': 'Sharm2' if field_name == 'last_name' else 'Sharma',
                'street': 'Main Road',
                'street_number': '10',
                'zip_code': '110001',
                'city': 'New Delhi',
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
        self.assertEqual(
            admin_email.to,
            ['ops@example.com', 'warehouse@example.com'],
        )
        self.assertIn('Order cancelled', admin_email.subject)
        self.assertIn(self.order.order_id, admin_email.body)

    def test_customer_cannot_cancel_after_24_hours(self):
        self.order.order_date = timezone.now() - timedelta(hours=24, seconds=1)
        self.order.save(update_fields=['order_date'])
        self.client.force_login(self.user)

        self.client.post(reverse('cancel_order', args=[
            self.order.order_id, self.order.guest_access_token,
        ]))

        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.ACCEPTED)
        self.assertEqual(self.product.stock_quantity, 5)

    def test_cancellation_rejection_message_uses_configured_window(self):
        policy = ReturnRefundPolicy.get_solo()
        policy.cancellation_window_hours = 6
        policy.save()
        self.order.order_date = timezone.now() - timedelta(hours=7)
        self.order.save(update_fields=['order_date'])
        self.client.force_login(self.user)

        response = self.client.post(reverse('cancel_order', args=[
            self.order.order_id, self.order.guest_access_token,
        ]))

        self.assertTrue(any(
            'within 6 hours' in message.message
            for message in get_messages(response.wsgi_request)
        ))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.ACCEPTED)

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

    def test_reaching_delivery_threshold_removes_fee_and_updates_subtotal(self):
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
        self.assertNotContains(at_threshold, 'Delivery fee')

    def test_zero_quantity_removes_cart_item(self):
        remaining_item = CartItem.objects.create(
            cart=self.cart_item.cart,
            product=self.product,
            quantity=1,
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
        ServiceablePincode.objects.create(pincode='110001')
        DeliveryGuideline.get_solo()

    def checkout_data(self, delivery_option=None):
        data = {
            'first_name': 'Guest',
            'last_name': 'Customer',
            'street': 'Main Road',
            'street_number': '12',
            'zip_code': '110001',
            'city': 'New Delhi',
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

    def test_guest_checkout_shows_total_before_confirming_order(self):
        initial_response = self.client.get(reverse('guest-checkout'))
        self.assertEqual(initial_response.status_code, 200)
        self.assertNotContains(initial_response, 'Delivery charges')
        self.assertContains(initial_response, 'data-validate-on-blur')
        self.assertContains(initial_response, 'select2-state')

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
        self.assertContains(response, 'Express delivery in 4–5 hours')
        self.assertContains(response, 'next day')
        self.assertContains(response, '4–6 days')
        self.assertContains(response, 'Outside Standard Service Area')
        self.assertNotContains(response, 'Non-serviceable')
        self.assertNotContains(response, 'delivery-option-modal')
        self.assertContains(response, 'Enter your shipping address at checkout')
        self.assertNotContains(response, 'Delivery charges:')

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
            city='Dehradun',
            country='IN',
            state=self.state,
            phone='9999999999',
            is_default=True,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('cart'))

        self.assertEqual(response.status_code, 200)
        response_html = response.content.decode()
        self.assertContains(response, 'Processing order...')
        self.assertContains(response, 'class="cod-button-spinner"')
        fee_position = response_html.index('Delivery fee')
        sidebar_position = response_html.index('<aside class="col-md-3">')
        self.assertLess(fee_position, sidebar_position)
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
            'city': 'Dehradun',
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
            'city': 'Dehradun',
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
            city='Dehradun',
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
            city='Dehradun',
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
            city='Dehradun',
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
            city='Dehradun',
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

    def test_guest_address_outside_service_city_can_enter_review(self):
        form = GuestShippingAddressForm(data={
            'first_name': 'Alice',
            'last_name': 'Smith',
            'street': 'Main Street',
            'street_number': '12',
            'zip_code': '110001',
            'city': 'New Delhi',
            'country': 'IN',
            'state': State.objects.get(country_id='IN', name='Delhi').pk,
            'phone': '9999999999',
            'email': 'alice@example.com',
        })

        self.assertTrue(form.is_valid())

    def test_unlisted_pincode_order_is_accepted_immediately(self):
        outside_address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Street',
            street_number='12',
            zip_code='110001',
            city='New Delhi',
            country='IN',
            state=State.objects.get(country_id='IN', name='Delhi'),
            phone='9999999999',
        )

        order = create_order(
            self.cart,
            order_id='ORDER-OUTSIDE-CITY',
            payment_status='Pending',
            payment_mode='Cash on Delivery',
            shipping_address=outside_address,
        )

        self.assertEqual(order.status, Order.Status.ACCEPTED)
        self.assertTrue(order.outside_service_area)
        self.assertEqual(order.delivery_pincode, '110001')

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
            city='Dehradun',
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
            city='Dehradun',
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
        self.assertIn('/admin/accounts/order/', mail.outbox[0].body)
