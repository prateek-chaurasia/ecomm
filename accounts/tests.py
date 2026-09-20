import json

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Cart, CartItem, Order, ServiceablePincode
from accounts.forms import GuestShippingAddressForm
from accounts.views import create_order
from base.emails import send_admin_new_order_email
from home.models import ShippingAddress
from products.models import Category, Product


class RegistrationTests(TestCase):
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

    def test_login_throttles_repeated_invalid_credentials(self):
        self.user.profile.is_email_verified = True
        self.user.profile.save(update_fields=['is_email_verified', 'updated_at'])

        for _ in range(5):
            response = self.client.post(reverse('login'), {
                'username': 'alice',
                'password': 'wrong-password',
            })
            self.assertEqual(response.status_code, 302)

        response = self.client.post(reverse('login'), {
            'username': 'alice',
            'password': 'secret123',
        }, follow=True)
        self.assertContains(
            response,
            'Too many unsuccessful attempts',
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
        first_address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Street',
            street_number='12',
            zip_code='110001',
            city='Dehradun',
            country='IN',
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
            'phone': '9999999999',
            'email': 'alice@example.com',
        })

        self.assertTrue(form.is_valid())

    def test_unlisted_pincode_starts_order_in_pending_review(self):
        outside_address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Street',
            street_number='12',
            zip_code='110001',
            city='New Delhi',
            country='IN',
            phone='9999999999',
        )

        order = create_order(
            self.cart,
            order_id='ORDER-OUTSIDE-CITY',
            payment_status='Pending',
            payment_mode='Cash on Delivery',
            shipping_address=outside_address,
        )

        self.assertEqual(order.status, Order.Status.PENDING_REVIEW)
        self.assertTrue(order.outside_service_area)
        self.assertEqual(order.delivery_pincode, '110001')

    def test_listed_pincode_starts_order_as_accepted(self):
        ServiceablePincode.objects.create(pincode='248001')
        address = ShippingAddress.objects.create(
            user=self.user,
            first_name='Alice',
            last_name='Smith',
            street='Main Street',
            street_number='12',
            zip_code='248001',
            city='Dehradun',
            country='IN',
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
