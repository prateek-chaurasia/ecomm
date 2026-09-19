import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Cart, CartItem, Order
from accounts.views import create_order
from home.models import ShippingAddress
from products.models import Category, Product


class UserShippingAddressTests(TestCase):
    def setUp(self):
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

    def test_user_can_save_multiple_addresses_and_set_default(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('shipping-address'), {
            'first_name': 'Alice',
            'last_name': 'Smith',
            'street': 'Main Street',
            'street_number': '12',
            'zip_code': '110001',
            'city': 'New Delhi',
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
            'city': 'Mumbai',
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
            city='New Delhi',
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
            city='Mumbai',
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
            city='New Delhi',
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
            city='Mumbai',
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
