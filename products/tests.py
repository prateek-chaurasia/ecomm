from io import BytesIO
import tempfile

from django.contrib import admin
from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from PIL import Image

from accounts.models import Cart, CartItem
from home.models import ReturnRefundPolicy
from products.admin import ProductAdmin, ProductAdminForm, ReturnDetailsInlineForm, restock_selected
from products.models import (
    Category, Product, ProductImage, AgeGroup, Tag, SizeVariant, ColorVariant,
    ReturnDetails, ProductReview,
    BundleConfiguration, BundleOffer, BundlePackagingOption, Wishlist,
)


class ProductSKUTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(category_name='Decorative Items')

    def create_product(self, **overrides):
        values = {
            'product_name': 'Buddha Statue',
            'category': self.category,
            'price': 500,
            'product_desription': 'Decorative statue',
        }
        values.update(overrides)
        return Product.objects.create(**values)

    def test_product_generates_readable_sku(self):
        product = self.create_product()

        self.assertEqual(product.sku, 'DEC-BUD-0001')
        self.assertEqual(len(product.sku), 12)

    def test_generated_skus_are_unique_for_matching_product_names(self):
        first = self.create_product()
        second = self.create_product(product_name='Buddha Lamp')

        self.assertEqual(first.sku, 'DEC-BUD-0001')
        self.assertEqual(second.sku, 'DEC-BUD-0002')

    def test_custom_sku_is_preserved_and_normalized(self):
        product = self.create_product(sku='custom-123')

        self.assertEqual(product.sku, 'CUSTOM-123')

    def test_product_admin_rejects_duplicate_sku_case_insensitively(self):
        existing = self.create_product(sku='CUSTOM-123')
        form = ProductAdminForm(data={
            'product_name': 'Another Statue',
            'category': self.category.pk,
            'price': 600,
            'product_desription': 'Another decorative statue',
            'sku': 'custom-123',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('sku', form.errors)

    def test_database_rejects_duplicate_sku(self):
        existing = self.create_product(sku='CUSTOM-123')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_product(product_name='Different Statue', sku=existing.sku)


class ProductInventoryAdminTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(category_name='Shoes')
        self.product = Product.objects.create(
            product_name='Running Sneaker',
            category=self.category,
            price=1500,
            product_desription='Comfortable running shoe',
            stock_quantity=4,
            low_stock_threshold=5,
        )

    def test_product_tracks_inventory_status(self):
        self.assertEqual(self.product.stock_status, 'Low stock')
        self.assertFalse(self.product.in_stock is False)

    def test_restock_selected_admin_action_updates_stock(self):
        queryset = Product.objects.filter(pk=self.product.pk)
        restock_selected(admin.site._registry[Product], None, queryset)

        self.product.refresh_from_db()
        self.assertGreaterEqual(self.product.stock_quantity, self.product.low_stock_threshold)

    def test_admin_restock_action_emails_wishlist_subscriber(self):
        self.product.stock_quantity = 0
        self.product.save(update_fields=['stock_quantity', 'updated_at'])
        user = User.objects.create_user(
            username='restock-customer', email='restock@example.com')
        Wishlist.objects.create(
            user=user, product=self.product, notify_on_restock=True)

        restock_selected(
            admin.site._registry[Product],
            None,
            Product.objects.filter(pk=self.product.pk),
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['restock@example.com'])


class ProductAdminPaginationTests(TestCase):
    def setUp(self):
        admin_user = User.objects.create_superuser(
            username='product-pagination-admin',
            email='product-pagination-admin@example.com',
            password='secret123',
        )
        self.client.force_login(admin_user)
        category = Category.objects.create(category_name='Pagination Products')
        for index in range(26):
            Product.objects.create(
                product_name=f'Page size product {index}',
                category=category,
                price=100,
                product_desription='Pagination test product',
            )

    def test_product_changelist_supports_allowed_page_sizes_and_keeps_filters(self):
        url = reverse('admin:products_product_changelist')
        response = self.client.get(url, {
            'per_page': '25',
            'q': 'Page size product',
            'newest_product__exact': '0',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['cl'].list_per_page, 25)
        self.assertEqual(len(response.context['cl'].result_list), 25)
        self.assertTrue(response.context['cl'].has_active_filters)
        self.assertContains(response, 'Records per page:')
        self.assertContains(
            response,
            '<details id="changelist-filter" open>',
            html=False,
        )
        self.assertContains(response, 'name="q" value="Page size product"')
        self.assertContains(response, 'name="newest_product__exact"')

        response = self.client.get(url, {'per_page': '50'})
        self.assertEqual(response.context['cl'].list_per_page, 50)
        self.assertEqual(len(response.context['cl'].result_list), 26)

        response = self.client.get(url, {'per_page': '100'})
        self.assertEqual(response.context['cl'].list_per_page, 100)

    def test_invalid_product_page_size_uses_default(self):
        response = self.client.get(
            reverse('admin:products_product_changelist'),
            {'per_page': '500'},
        )

        self.assertEqual(response.context['cl'].list_per_page, 25)


class BundleBuilderTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(category_name='Gifts')
        self.products = [
            Product.objects.create(
                product_name=f'Gift item {index}',
                category=self.category,
                price=100 * index,
                product_desription='Gift item',
                stock_quantity=10,
                bundle_eligible=True,
            )
            for index in range(1, 4)
        ]
        self.offer = BundleOffer.objects.create(
            title='Birthday Box',
            discount_percentage=10,
            is_active=True,
        )
        self.offer.products.set(self.products)
        self.packaging = BundlePackagingOption.objects.create(
            name='Corporate kraft sleeve',
            description='A clean paper sleeve for workplace gifting.',
            sample_image='bundles/packaging-samples/kraft.jpg',
            price=40,
        )

    def test_customer_can_create_bundle_with_configured_limit(self):
        BundleConfiguration.objects.create(max_products=2)
        response = self.client.post(
            reverse('bundle_builder', args=[self.offer.slug]),
            {'products': [str(product.uid) for product in self.products], 'quantity': 2},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Choose between 1 and 2 products.')

        response = self.client.post(
            reverse('bundle_builder', args=[self.offer.slug]),
            {'products': [str(self.products[0].uid), str(self.products[1].uid)], 'quantity': 2},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('cart'))
        cart = Cart.objects.get(session_key=self.client.session.session_key)
        bundle_item = cart.bundle_items.get()
        self.assertEqual(bundle_item.quantity, 2)
        self.assertEqual(bundle_item.unit_price, 270)
        self.assertEqual(bundle_item.products.count(), 2)

    def test_customer_reviews_custom_bundle_before_adding_it(self):
        BundleConfiguration.objects.create(max_products=2)
        self.products[2].show_in_catalog = False
        self.products[2].save(update_fields=['show_in_catalog'])
        response = self.client.get(reverse('create_custom_bundle'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gift item 1')
        self.assertContains(response, 'Gift item 2')
        self.assertContains(response, 'Gift item 3')

        response = self.client.post(
            reverse('create_custom_bundle'),
            {'products': [str(self.products[0].uid), str(self.products[1].uid)], 'quantity': 3},
        )
        self.assertRedirects(response, reverse('custom_bundle_packaging'))
        self.assertFalse(Cart.objects.exists())

        response = self.client.get(reverse('custom_bundle_packaging'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Corporate kraft sleeve')
        self.assertContains(response, '₹40 per bundle')

        response = self.client.post(
            reverse('custom_bundle_packaging'),
            {'packaging_option': str(self.packaging.uid)},
        )
        self.assertRedirects(response, reverse('custom_bundle_review'))

        response = self.client.get(reverse('custom_bundle_review'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gift item 1')
        self.assertContains(response, '₹340')
        self.assertContains(response, '₹1020')

        response = self.client.post(reverse('custom_bundle_review'))
        self.assertRedirects(response, reverse('cart'))
        cart = Cart.objects.get(session_key=self.client.session.session_key)
        bundle_item = cart.bundle_items.get()
        self.assertIsNone(bundle_item.bundle_offer)
        self.assertEqual(bundle_item.quantity, 3)
        self.assertEqual(bundle_item.packaging_option, self.packaging)
        self.assertEqual(bundle_item.packaging_price, 40)
        self.assertEqual(bundle_item.unit_price, 340)
        self.assertEqual(bundle_item.products.count(), 2)


class CategoryPagesTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(category_name='Shoes', category_image='categories/shoes.jpg')
        self.product = Product.objects.create(
            product_name='Trail Runner',
            category=self.category,
            price=2200,
            product_desription='Comfortable running shoe',
            stock_quantity=8,
            low_stock_threshold=5,
        )

    def test_categories_page_lists_all_categories(self):
        response = self.client.get(reverse('categories'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Shoes')
        self.assertContains(
            response,
            f'href="{reverse("category_products", args=[self.category.slug])}"',
        )

    def test_category_page_shows_products_for_that_category(self):
        response = self.client.get(reverse('category_products', args=[self.category.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Trail Runner')

    def test_category_page_hides_products_disabled_from_catalog(self):
        hidden_product = Product.objects.create(
            product_name='Bundle-only Gift',
            category=self.category,
            price=500,
            product_desription='Only for bundles',
            stock_quantity=8,
            bundle_eligible=True,
            show_in_catalog=False,
        )
        response = self.client.get(reverse('category_products', args=[self.category.slug]))
        self.assertContains(response, 'Trail Runner')
        self.assertNotContains(response, hidden_product.product_name)

    def test_category_page_has_category_specific_search(self):
        response = self.client.get(reverse('category_products', args=[self.category.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Search in Shoes:')

    def test_home_page_does_not_show_duplicate_search_box(self):
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Search in')

    def test_product_can_belong_to_multiple_age_groups(self):
        age_3_5 = AgeGroup.objects.create(name='3-5 years')
        age_6_8 = AgeGroup.objects.create(name='6-8 years')

        self.product.age_groups.set([age_3_5, age_6_8])

        self.assertEqual(
            set(self.product.age_groups.values_list('name', flat=True)),
            {'3-5 years', '6-8 years'},
        )
        response = self.client.get(reverse('category_products', args=[self.category.slug]))
        self.assertEqual(response.status_code, 200)


class SearchTests(TestCase):
    def setUp(self):
        self.shoes = Category.objects.create(category_name='Shoes', category_image='categories/shoes.jpg')
        self.bags = Category.objects.create(category_name='Bags', category_image='categories/bags.jpg')
        self.runner = Product.objects.create(
            product_name='Trail Runner',
            category=self.shoes,
            price=2200,
            product_desription='Comfortable running shoe',
            stock_quantity=8,
            low_stock_threshold=5,
        )
        self.sandal = Product.objects.create(
            product_name='Beach Sandal',
            category=self.shoes,
            price=1200,
            product_desription='Casual summer footwear',
            stock_quantity=10,
            low_stock_threshold=5,
        )
        self.backpack = Product.objects.create(
            product_name='Adventure Backpack',
            category=self.bags,
            price=1800,
            product_desription='Daypack for travel and hiking',
            stock_quantity=6,
            low_stock_threshold=5,
        )

    def test_home_search_finds_products_across_all_categories(self):
        response = self.client.get(reverse('index'), {'q': 'trail'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Trail Runner')
        self.assertNotContains(response, 'Adventure Backpack')

    def test_category_search_filters_products_within_that_category(self):
        response = self.client.get(reverse('category_products', args=[self.shoes.slug]), {'q': 'trail'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Trail Runner')
        self.assertNotContains(response, 'Beach Sandal')


class CampaignTagTests(TestCase):
    def test_product_can_be_tagged_for_campaigns(self):
        category = Category.objects.create(category_name='Toys', category_image='categories/toys.jpg')
        product = Product.objects.create(
            product_name='Learning Blocks',
            category=category,
            price=900,
            product_desription='Colorful learning blocks',
            stock_quantity=12,
            low_stock_threshold=5,
        )
        tag = Tag.objects.create(name='Summer Campaign')
        product.tags.add(tag)

        response = self.client.get(reverse('index'), {'tag': 'Summer Campaign'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Learning Blocks')


class ProductPricingTests(TestCase):
    def test_product_slug_supports_full_product_name_length(self):
        category = Category.objects.create(category_name='Learning')
        product_name = 'A detailed description of this exceptionally long product name'
        product = Product.objects.create(
            product_name=product_name,
            category=category,
            price=1200,
            product_desription='Product description',
        )

        self.assertEqual(product.slug, 'a-detailed-description-of-this-exceptionally-long-product-name')

    def test_marked_price_calculates_discount_percentage(self):
        category = Category.objects.create(category_name='Learning', category_image='categories/learning.jpg')
        product = Product.objects.create(
            product_name='Puzzle Set',
            category=category,
            price=1200,
            marked_price=1500,
            product_desription='Creative puzzle set',
            stock_quantity=10,
            low_stock_threshold=5,
        )

        self.assertEqual(product.discount_percentage, 20)
        self.assertEqual(product.discount_label, '20% OFF')


class ProductReturnDetailsTests(TestCase):
    def test_product_admin_shows_return_policy_choices_during_product_entry(self):
        admin_user = User.objects.create_superuser(
            username='product-admin',
            email='admin@example.com',
            password='secret123',
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse('admin:products_product_add'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Return policy')
        self.assertContains(response, 'value="returnable">Returnable')
        self.assertContains(response, 'value="replaceable">Replaceable')
        self.assertContains(response, 'value="non_returnable">Non-Returnable')

    def test_return_details_attach_to_product_and_render(self):
        generic_policy = ReturnRefundPolicy.get_solo()
        generic_policy.return_summary = 'Generic return summary.'
        generic_policy.refund_summary = 'Generic refund summary.'
        generic_policy.save()
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Building Blocks',
            category=category,
            price=900,
            product_desription='Colorful building blocks',
        )
        ReturnDetails.objects.create(
            product=product,
            return_window_days=14,
            policy='Contact support to start a return.',
            refund_policy='Refunds for this product are store credit only.',
            conditions='Item must be unused and in original packaging.',
        )

        response = self.client.get(reverse('get_product', args=[product.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Return Details')
        self.assertContains(response, '14 days')
        self.assertContains(response, 'Refunds for this product are store credit only.')
        self.assertContains(response, 'class="return-policy-cards"')
        self.assertContains(response, 'class="product-return-badge">Returnable</span>')
        self.assertContains(response, 'Guidelines:</strong> Item must be unused')
        self.assertNotContains(response, 'Generic return summary.')
        self.assertNotContains(response, 'Generic refund summary.')
        self.assertContains(response, '<details class="return-policy-section"', html=False)
        self.assertContains(response, 'Buy Now')

    def test_product_page_shows_status_and_window_for_each_return_policy(self):
        category = Category.objects.create(category_name='Accessories')
        product = Product.objects.create(
            product_name='Leather wallet',
            category=category,
            price=1200,
            product_desription='Handmade leather wallet',
        )
        return_details = ReturnDetails.objects.create(
            product=product,
            return_window_days=10,
        )

        for policy, status, guidance in (
            ('returnable', 'Returnable', 'Return requests can be submitted within 10 days'),
            ('replaceable', 'Replaceable', 'Replacement requests can be submitted within 10 days'),
            ('non_returnable', 'Non-returnable', 'This product is not eligible for return or replacement.'),
        ):
            with self.subTest(policy=policy):
                return_details.return_policy = policy
                return_details.save()

                response = self.client.get(reverse('get_product', args=[product.slug]))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'>{status}</span>')
                if policy == 'non_returnable':
                    self.assertNotContains(response, guidance)
                else:
                    self.assertContains(response, guidance)
                page = response.content.decode()
                details_end = page.index(
                    '</details>', page.index('<details class="return-policy-section"'))
                badge_position = page.index(
                    '<span class="product-return-badge', details_end)
                self.assertLess(details_end, badge_position)

    def test_generic_policy_is_used_when_product_has_no_return_details(self):
        generic_policy = ReturnRefundPolicy.get_solo()
        generic_policy.return_summary = 'Generic return summary.'
        generic_policy.refund_summary = 'Generic refund summary.'
        generic_policy.save()
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Toy car',
            category=category,
            price=500,
            product_desription='A toy car',
        )

        response = self.client.get(reverse('get_product', args=[product.slug]))

        self.assertContains(response, 'Generic return summary.')
        self.assertContains(response, 'Generic refund summary.')

    def test_generic_refund_policy_fills_missing_product_refund_policy(self):
        generic_policy = ReturnRefundPolicy.get_solo()
        generic_policy.refund_summary = 'Generic refund summary.'
        generic_policy.save()
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Wooden train',
            category=category,
            price=700,
            product_desription='A wooden train',
        )
        ReturnDetails.objects.create(
            product=product,
            policy='Product-specific return terms.',
        )

        response = self.client.get(reverse('get_product', args=[product.slug]))

        self.assertContains(response, 'Product-specific return terms.')
        self.assertContains(response, 'Generic refund summary.')

    def test_admin_return_policy_form_syncs_legacy_returnable_flag(self):
        category = Category.objects.create(category_name='Gifts')
        product = Product.objects.create(
            product_name='Personalized gift',
            category=category,
            price=700,
            product_desription='A personalized gift',
        )

        for policy, is_returnable in (
            ('returnable', True),
            ('replaceable', True),
            ('non_returnable', False),
        ):
            form = ReturnDetailsInlineForm(
                data={'return_policy': policy},
                instance=ReturnDetails(product=product),
            )
            self.assertTrue(form.is_valid(), form.errors)
            return_details = form.save()
            self.assertEqual(return_details.return_policy, policy)
            self.assertEqual(return_details.is_returnable, is_returnable)
            return_details.delete()


class ProductCartStateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='cart-customer', password='secret123')
        self.category = Category.objects.create(category_name='Toys')
        self.product = Product.objects.create(
            product_name='Toy robot',
            category=self.category,
            price=750,
            product_desription='A toy robot',
            stock_quantity=8,
        )
        self.client.force_login(self.user)

    def test_product_page_shows_existing_cart_quantity_instead_of_add_button(self):
        cart = Cart.objects.create(user=self.user)
        cart_item = CartItem.objects.create(
            cart=cart,
            product=self.product,
            quantity=3,
        )

        response = self.client.get(reverse('get_product', args=[self.product.slug]))

        self.assertEqual(response.context['product_cart_item'], cart_item)
        self.assertContains(response, '3 items added')
        self.assertContains(response, f'<a href="{reverse("cart")}" id="go-to-cart-link"')
        self.assertContains(response, 'id="go-to-cart-link" class="btn btn-outline-primary"')
        self.assertContains(response, 'id="add-to-cart-btn" class="btn btn-primary" hidden')
        self.assertContains(response, 'id="add-to-cart-form" class="d-inline-block d-none"')

    def test_buy_now_redirects_authenticated_customer_directly_to_checkout(self):
        response = self.client.post(
            reverse('add_to_cart', args=[self.product.uid]),
            {'quantity': 1, 'buy_now': '1'},
        )

        self.assertRedirects(response, reverse('checkout'))
        self.assertTrue(
            CartItem.objects.filter(
                cart__user=self.user, product=self.product, quantity=1,
            ).exists())

    def test_product_quantity_is_capped_at_available_inventory(self):
        response = self.client.get(reverse('get_product', args=[self.product.slug]))

        self.assertContains(response, 'max="8"')
        self.assertContains(response, 'In stock')
        self.assertNotContains(response, '8 available')
        self.assertContains(response, 'handleProductQuantityInput(quantityInput)')
        self.assertContains(
            response,
            'Requested quantity exceeds available stock. Choose a lower quantity.',
        )
        self.assertContains(response, 'quantityInput.value = String(maximum)')
        self.assertContains(response, 'if (targetQuantity > maximum)')
        self.assertContains(response, 'id="go-to-cart-link" class="btn btn-outline-primary" hidden')

    def test_product_page_shows_add_button_when_not_in_cart(self):
        response = self.client.get(reverse('get_product', args=[self.product.slug]))

        self.assertIsNone(response.context['product_cart_item'])
        self.assertContains(response, 'Add to cart')
        self.assertNotContains(response, 'added to cart')
        self.assertContains(response, f'<a href="{reverse("cart")}" id="go-to-cart-link"')
        self.assertContains(response, 'id="go-to-cart-link" class="btn btn-outline-primary" hidden')

    def test_default_size_does_not_change_product_or_cart_price(self):
        default_size = SizeVariant.objects.create(
            size_name='Default Size',
            price=450,
        )
        large_size = SizeVariant.objects.create(size_name='Large', price=100)
        self.product.size_variant.add(default_size, large_size)

        response = self.client.get(
            reverse('get_product', args=[self.product.slug]),
            {'size': default_size.size_name},
        )

        self.assertEqual(response.context['updated_price'], self.product.price)
        self.assertContains(response, f'₹{self.product.price}.00')
        self.assertEqual(
            self.product.get_product_price_by_size(default_size.size_name),
            self.product.price,
        )
        self.assertEqual(
            self.product.get_product_price_by_size(large_size.size_name),
            self.product.price + large_size.price,
        )

        cart = Cart.objects.create(user=self.user)
        cart_item = CartItem.objects.create(
            cart=cart,
            product=self.product,
            size_variant=default_size,
            quantity=2,
        )
        self.assertEqual(cart_item.get_product_price(), self.product.price * 2)
        cart_item.size_variant = large_size
        self.assertEqual(
            cart_item.get_product_price(),
            self.product.price * 2 + large_size.price,
        )

    def test_adding_product_shows_go_to_cart_link_after_redirect(self):
        response = self.client.post(
            reverse('add_to_cart', args=[self.product.uid]),
            {
                'quantity': 2,
                'next': reverse('get_product', args=[self.product.slug]),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request['PATH_INFO'], reverse('get_product', args=[self.product.slug]))
        self.assertContains(response, '2 items added')
        self.assertContains(response, 'Go to Cart')

    def test_product_page_hides_suitable_for_when_no_age_group_is_selected(self):
        response = self.client.get(reverse('get_product', args=[self.product.slug]))

        self.assertNotContains(response, 'Suitable for')
        self.assertNotContains(response, 'All ages')

    def test_product_page_shows_selected_age_groups_as_suitable_for(self):
        age_group = AgeGroup.objects.create(name='6-8 years')
        self.product.age_groups.add(age_group)

        response = self.client.get(reverse('get_product', args=[self.product.slug]))

        self.assertContains(response, 'Suitable for')
        self.assertContains(response, '6-8 years')

    def test_product_description_renders_allowed_html_and_newlines(self):
        self.product.product_desription = (
            'Gift highlights:\n'
            '<ul><li><strong>Handmade</strong></li><li>Ready to gift</li></ul>'
        )
        self.product.save(update_fields=['product_desription'])

        response = self.client.get(reverse('get_product', args=[self.product.slug]))

        self.assertContains(response, 'Gift highlights:<br>')
        self.assertContains(response, '<ul><li><strong>Handmade</strong></li>')
        self.assertContains(response, '<li>Ready to gift</li></ul>')

    def test_product_description_strips_unsafe_html_and_attributes(self):
        self.product.product_desription = (
            '<p onclick="alert(1)">Safe text</p>'
            '<script>alert(2)</script>'
        )
        self.product.save(update_fields=['product_desription'])

        response = self.client.get(reverse('get_product', args=[self.product.slug]))

        self.assertContains(response, '<p>Safe text</p>alert(2)')
        self.assertNotContains(response, 'onclick')
        self.assertNotContains(response, '<script>')


class WishlistBackInStockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='wishlist-customer',
            email='wishlist@example.com',
            password='secret123',
        )
        self.category = Category.objects.create(category_name='Gifts')
        self.product = Product.objects.create(
            product_name='Out of stock gift',
            category=self.category,
            price=1200,
            product_desription='A popular gift',
            stock_quantity=0,
        )

    def test_product_page_hides_add_wishlist_action_when_already_wishlisted(self):
        Wishlist.objects.create(user=self.user, product=self.product)
        ProductImage.objects.create(
            product=self.product,
            image_url='products/out-of-stock-gift.jpg',
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('get_product', args=[self.product.slug]))

        self.assertContains(response, 'In wishlist')
        self.assertContains(response, 'class="stock-status-overlay"')
        self.assertContains(response, 'role="status">Out of stock</span>')
        self.assertNotContains(response, 'Add to wishlist')

    def test_registered_user_can_opt_into_restock_email_without_size_variants(self):
        self.client.force_login(self.user)

        product_url = reverse('get_product', args=[self.product.slug])
        detail_response = self.client.get(product_url)
        self.assertContains(
            detail_response,
            f'action="{reverse("add_to_wishlist", args=[self.product.uid])}?size="',
        )
        self.assertNotContains(detail_response, '?size=None')

        response = self.client.post(
            reverse('add_to_wishlist', args=[self.product.uid]),
            {
                'size': 'None',
                'manage_restock_notification': '1',
                'notify_on_restock': 'on',
                'next': product_url,
            },
        )

        self.assertRedirects(response, product_url)
        wishlist_item = Wishlist.objects.get(user=self.user, product=self.product)
        self.assertTrue(wishlist_item.notify_on_restock)
        self.assertContains(self.client.get(reverse('get_product', args=[self.product.slug])), 'Email me when this item is back in stock')

    def test_user_can_opt_out_of_restock_email(self):
        Wishlist.objects.create(
            user=self.user,
            product=self.product,
            notify_on_restock=True,
        )
        self.client.force_login(self.user)

        self.client.post(
            reverse('add_to_wishlist', args=[self.product.uid]),
            {'manage_restock_notification': '1'},
        )

        wishlist_item = Wishlist.objects.get(user=self.user, product=self.product)
        self.assertFalse(wishlist_item.notify_on_restock)

    def test_restock_preference_only_updates_selected_size(self):
        small = SizeVariant.objects.create(size_name='Small')
        large = SizeVariant.objects.create(size_name='Large')
        self.product.size_variant.add(small, large)
        Wishlist.objects.create(user=self.user, product=self.product, size_variant=small)
        Wishlist.objects.create(user=self.user, product=self.product, size_variant=large)
        self.client.force_login(self.user)

        self.client.post(
            reverse('add_to_wishlist', args=[self.product.uid]),
            {'size': 'Large', 'manage_restock_notification': '1', 'notify_on_restock': 'on'},
        )

        self.assertFalse(Wishlist.objects.get(user=self.user, product=self.product, size_variant=small).notify_on_restock)
        self.assertTrue(Wishlist.objects.get(user=self.user, product=self.product, size_variant=large).notify_on_restock)

    def test_move_to_cart_uses_wishlisted_size_variant(self):
        small = SizeVariant.objects.create(size_name='Small')
        large = SizeVariant.objects.create(size_name='Large')
        self.product.size_variant.add(small, large)
        self.product.stock_quantity = 3
        self.product.save(update_fields=['stock_quantity', 'updated_at'])
        Wishlist.objects.create(user=self.user, product=self.product, size_variant=small)
        Wishlist.objects.create(user=self.user, product=self.product, size_variant=large)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('move_to_cart', args=[self.product.uid]), {'size': 'Large'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('cart'))
        self.assertTrue(Wishlist.objects.filter(user=self.user, product=self.product, size_variant=small).exists())
        self.assertFalse(Wishlist.objects.filter(user=self.user, product=self.product, size_variant=large).exists())
        self.assertEqual(
            CartItem.objects.get(cart__user=self.user, product=self.product).size_variant,
            large,
        )

    @override_settings(DEFAULT_FROM_EMAIL='noreply@example.com')
    def test_transition_from_out_of_stock_sends_one_email_to_subscribed_user(self):
        wishlist_item = Wishlist.objects.create(
            user=self.user,
            product=self.product,
            notify_on_restock=True,
        )

        self.product.stock_quantity = 4
        self.product.save(update_fields=['stock_quantity', 'updated_at'])

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['wishlist@example.com'])
        self.assertIn(self.product.product_name, mail.outbox[0].subject)
        self.assertIn(self.product.slug, mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].alternatives[0][1], 'text/html')
        self.assertIn(
            'cid:bundleofgifts-brand-icon',
            mail.outbox[0].alternatives[0][0],
        )
        wishlist_item.refresh_from_db()
        self.assertTrue(wishlist_item.restock_notification_sent)

        self.product.stock_quantity = 6
        self.product.save(update_fields=['stock_quantity', 'updated_at'])
        self.assertEqual(len(mail.outbox), 1)

    def test_back_in_stock_email_embeds_a_product_thumbnail_when_available(self):
        image_bytes = BytesIO()
        Image.new('RGB', (240, 120), 'blue').save(image_bytes, format='PNG')
        Wishlist.objects.create(
            user=self.user,
            product=self.product,
            notify_on_restock=True,
        )

        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=media_dir):
                ProductImage.objects.create(
                    product=self.product,
                    image_url=SimpleUploadedFile(
                        'restock-product.png',
                        image_bytes.getvalue(),
                        content_type='image/png',
                    ),
                )
                self.product.stock_quantity = 3
                self.product.save(update_fields=['stock_quantity', 'updated_at'])

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        html = email.alternatives[0][0]
        self.assertIn('src="cid:product-', html)
        image_attachment = next(
            attachment for attachment in email.message().walk()
            if attachment.get('Content-ID')
            and attachment.get('Content-ID').startswith('<product-')
        )
        content_id = (
            'product-'
            + html.split('src="cid:product-', 1)[1].split('"', 1)[0]
        )
        self.assertEqual(image_attachment.get('Content-ID'), f'<{content_id}>')
        self.assertEqual(image_attachment.get_content_type(), 'image/jpeg')


class ProductReviewPageTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        media_settings = override_settings(MEDIA_ROOT=self.media_dir.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.addCleanup(self.media_dir.cleanup)
        category = Category.objects.create(category_name='Games')
        self.product = Product.objects.create(
            product_name='Strategy Game',
            category=category,
            price=1200,
            product_desription='A strategy game',
        )
        self.user = User.objects.create_user(
            username='reviewer', password='secret123')
        self.client.force_login(self.user)

    @staticmethod
    def create_test_image(name):
        image_data = BytesIO()
        Image.new('RGB', (1, 1), color='red').save(image_data, format='PNG')
        return SimpleUploadedFile(
            name, image_data.getvalue(), content_type='image/png')

    def test_review_can_be_posted_without_photos(self):
        response = self.client.post(
            reverse('get_product', args=[self.product.slug]),
            {'stars': 5, 'content': 'Great game'},
        )

        self.assertRedirects(response, reverse('get_product', args=[self.product.slug]))
        review = ProductReview.objects.get(product=self.product, user=self.user)
        self.assertEqual(review.photos.count(), 0)
        response = self.client.get(
            reverse('get_product', args=[self.product.slug]))
        self.assertNotContains(response, 'Share Your Experience')

    def test_share_experience_is_shown_when_signed_in_user_has_not_reviewed(self):
        response = self.client.get(
            reverse('get_product', args=[self.product.slug]))

        self.assertContains(response, 'Share Your Experience')
        self.assertRegex(
            response.content.decode(),
            r'<details class="review-form-disclosure">\s*<summary',
        )
        self.assertRegex(
            response.content.decode(),
            r'class="review-form-fields"\s+hidden',
        )
        self.assertEqual(response.content.count(b'data-rating="'), 5)
        self.assertContains(response, 'id="reviewPhotos"')
        self.assertNotRegex(
            response.content.decode(),
            r'<input[^>]*id="reviewPhotos"[^>]*required',
        )

    def test_review_can_be_posted_with_multiple_photos(self):
        response = self.client.post(
            reverse('get_product', args=[self.product.slug]),
            {
                'stars': 5,
                'content': 'Great game',
                'photos': [
                    self.create_test_image('game-one.png'),
                    self.create_test_image('game-two.png'),
                ],
            },
        )

        self.assertRedirects(response, reverse('get_product', args=[self.product.slug]))
        review = ProductReview.objects.get(product=self.product, user=self.user)
        self.assertEqual(review.photos.count(), 2)
        response = self.client.get(reverse('get_product', args=[self.product.slug]))
        self.assertContains(response, 'class="product-review-list"')
        self.assertContains(response, 'class="product-review-item"')
        self.assertContains(response, 'class="product-review-rating" aria-label="5 out of 5 stars"')
        self.assertContains(response, 'reviewer')
        self.assertContains(response, 'aria-label="Review photos"')
        self.assertContains(response, 'game-one')
        response = self.client.get(
            reverse('product_reviews_for_product', args=[self.product.slug]))
        self.assertContains(response, 'aria-label="Review photos"')

    def test_review_rejects_more_than_five_photos(self):
        response = self.client.post(
            reverse('get_product', args=[self.product.slug]),
            {
                'stars': 5,
                'content': 'Great game',
                'photos': [
                    self.create_test_image(f'game-{index}.png')
                    for index in range(6)
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'You can upload up to five review photos.')
        self.assertContains(response, '<details class="review-form-disclosure" open>')
        self.assertFalse(
            ProductReview.objects.filter(product=self.product, user=self.user).exists())

    def test_product_reviews_page_is_scoped_and_sortable(self):
        low_reviewer = User.objects.create_user(username='low-reviewer')
        high_reviewer = User.objects.create_user(username='high-reviewer')
        ProductReview.objects.create(product=self.product, user=low_reviewer, stars=2, content='Not great')
        ProductReview.objects.create(product=self.product, user=high_reviewer, stars=5, content='Excellent')

        response = self.client.post(
            reverse('product_reviews_for_product', args=[self.product.slug]),
            {'sort': 'rating_desc'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Highest rating')
        self.assertContains(response, 'Strategy Game reviews')
        self.assertLess(
            response.content.decode().index('Excellent'),
            response.content.decode().index('Not great'),
        )


class ProductQuantityTests(TestCase):
    def test_add_to_cart_merges_matching_product_size_and_color(self):
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Wooden Puzzle',
            category=category,
            price=600,
            product_desription='A wooden puzzle',
            stock_quantity=5,
            gift_wrap_available=True,
            gift_wrap_price=30,
        )
        size = SizeVariant.objects.create(size_name='Medium')
        product.size_variant.add(size)

        self.client.post(
            reverse('add_to_cart', args=[product.uid]),
            {'size': 'Medium', 'quantity': 1, 'gift_wrap': '0'},
        )
        self.client.post(
            reverse('add_to_cart', args=[product.uid]),
            {'size': 'Medium', 'quantity': 1, 'gift_wrap': '1'},
        )

        cart_items = CartItem.objects.filter(
            cart__session_key=self.client.session.session_key,
            product=product,
            size_variant=size,
            color_variant=None,
        )
        self.assertEqual(cart_items.count(), 1)
        self.assertEqual(cart_items.get().quantity, 2)
        self.assertTrue(cart_items.get().gift_wrap)

    def test_same_product_can_have_distinct_size_and_color_combinations(self):
        user = User.objects.create_user(username='variant-customer')
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Wooden Puzzle',
            category=category,
            price=600,
            product_desription='A wooden puzzle',
            stock_quantity=5,
        )
        cart = Cart.objects.create(user=user)
        small = SizeVariant.objects.create(size_name='Small')
        large = SizeVariant.objects.create(size_name='Large')
        red = ColorVariant.objects.create(color_name='Red')
        blue = ColorVariant.objects.create(color_name='Blue')

        CartItem.objects.create(
            cart=cart, product=product, size_variant=small, color_variant=red)
        CartItem.objects.create(
            cart=cart, product=product, size_variant=large, color_variant=red)
        CartItem.objects.create(
            cart=cart, product=product, size_variant=small, color_variant=blue)

        self.assertEqual(cart.cart_items.count(), 3)
        self.client.force_login(user)
        response = self.client.get(reverse('cart'))
        self.assertContains(response, 'Color: Red', count=2)
        self.assertContains(response, 'Color: Blue', count=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CartItem.objects.create(
                    cart=cart,
                    product=product,
                    size_variant=small,
                    color_variant=red,
                    gift_wrap=True,
                )

    def test_buy_now_redirects_to_guest_checkout_without_adding_existing_item_again(self):
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Wooden Puzzle',
            category=category,
            price=600,
            product_desription='A wooden puzzle',
            stock_quantity=5,
        )
        self.client.post(
            reverse('add_to_cart', args=[product.uid]),
            {'quantity': 1},
        )

        response = self.client.post(
            reverse('add_to_cart', args=[product.uid]),
            {'quantity': 1, 'buy_now': '1'},
        )

        self.assertRedirects(response, reverse('guest-checkout'))
        cart_items = CartItem.objects.filter(
            cart__session_key=self.client.session.session_key,
            product=product,
        )
        self.assertEqual(cart_items.count(), 1)
        self.assertEqual(cart_items.get().quantity, 1)

    def test_buy_now_updates_gift_wrap_for_existing_cart_item(self):
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Wooden Puzzle',
            category=category,
            price=600,
            product_desription='A wooden puzzle',
            stock_quantity=5,
            gift_wrap_available=True,
            gift_wrap_price=30,
        )
        self.client.post(
            reverse('add_to_cart', args=[product.uid]),
            {'quantity': 1, 'gift_wrap': '0'},
        )

        response = self.client.post(
            reverse('add_to_cart', args=[product.uid]),
            {'quantity': 1, 'gift_wrap': '1', 'buy_now': '1'},
        )

        self.assertRedirects(response, reverse('guest-checkout'))
        cart_item = CartItem.objects.get(
            cart__session_key=self.client.session.session_key,
            product=product,
        )
        self.assertEqual(cart_item.quantity, 1)
        self.assertTrue(cart_item.gift_wrap)
        self.assertEqual(cart_item.get_product_price(), 630)
        cart_response = self.client.get(reverse('cart'))
        self.assertContains(cart_response, 'Gift wrapped (+₹30 per item)')
        self.assertContains(cart_response, '₹630')

    def test_buy_now_adds_product_when_it_is_not_already_in_cart(self):
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Wooden Puzzle',
            category=category,
            price=600,
            product_desription='A wooden puzzle',
            stock_quantity=5,
        )

        response = self.client.post(
            reverse('add_to_cart', args=[product.uid]),
            {'quantity': 2, 'buy_now': '1'},
        )

        self.assertRedirects(response, reverse('guest-checkout'))
        cart_item = CartItem.objects.get(
            cart__session_key=self.client.session.session_key,
            product=product,
        )
        self.assertEqual(cart_item.quantity, 2)

    def test_add_to_cart_respects_requested_quantity_and_stock_limit(self):
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Limited Blocks',
            category=category,
            price=900,
            product_desription='Limited stock blocks',
            stock_quantity=3,
        )

        response = self.client.post(
            reverse('add_to_cart', args=[product.uid]),
            {'quantity': 2},
        )
        self.assertEqual(response.status_code, 302)
        cart_item = CartItem.objects.get(cart__session_key=self.client.session.session_key)
        self.assertEqual(cart_item.quantity, 2)

        self.client.post(reverse('add_to_cart', args=[product.uid]), {'quantity': 5})
        cart_item.refresh_from_db()
        self.assertEqual(cart_item.quantity, 3)

    def test_gift_wrap_charge_is_applied_per_item(self):
        category = Category.objects.create(category_name='Gifts')
        product = Product.objects.create(
            product_name='Gift Box',
            category=category,
            price=500,
            product_desription='A gift box',
            stock_quantity=5,
            gift_wrap_available=True,
            gift_wrap_price=40,
        )
        cart = Cart.objects.create(session_key='gift-wrap-session')
        cart_item = CartItem.objects.create(
            cart=cart, product=product, quantity=3, gift_wrap=True)

        self.assertEqual(cart_item.get_product_price(), 1620)
        self.assertEqual(cart.get_cart_total(), 1620)


class SearchSuggestionTests(TestCase):
    def test_search_returns_product_name_suggestions(self):
        category = Category.objects.create(category_name='Toys', category_image='categories/toys.jpg')
        Product.objects.create(
            product_name='Learning Blocks',
            category=category,
            price=900,
            product_desription='Colorful learning blocks',
            stock_quantity=12,
            low_stock_threshold=5,
        )

        response = self.client.get(reverse('product_search'), {'q': 'learn', 'format': 'json'})

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content.decode(), {'suggestions': ['Learning Blocks']})


class ProductReviewSortTests(TestCase):
    def test_review_rating_sorting_orders_high_to_low(self):
        category = Category.objects.create(category_name='Games', category_image='categories/games.jpg')
        low_rated = Product.objects.create(
            product_name='Simple Board Game',
            category=category,
            price=800,
            product_desription='Basic board game',
            stock_quantity=10,
            low_stock_threshold=5,
        )
        high_rated = Product.objects.create(
            product_name='Premium Puzzle',
            category=category,
            price=1200,
            product_desription='Premium puzzle',
            stock_quantity=10,
            low_stock_threshold=5,
        )

        from django.contrib.auth import get_user_model
        User = get_user_model()
        user_one = User.objects.create_user(username='user1', password='pass1234')
        user_two = User.objects.create_user(username='user2', password='pass1234')
        user_three = User.objects.create_user(username='user3', password='pass1234')

        low_rated.reviews.create(user=user_one, stars=2, content='Okay')
        high_rated.reviews.create(user=user_two, stars=5, content='Great')
        high_rated.reviews.create(user=user_three, stars=4, content='Loved it')

        response = self.client.get(reverse('index'), {'sort': 'ratingDesc'})

        self.assertEqual(response.status_code, 200)
        products = list(response.context['products'])
        self.assertEqual(products[0].product_name, 'Premium Puzzle')
        self.assertEqual(products[1].product_name, 'Simple Board Game')
