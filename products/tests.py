from io import BytesIO
from datetime import timedelta
from decimal import Decimal
import json
import tempfile

from django.contrib import admin
from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from PIL import Image

from accounts.models import Cart, CartItem
from home.models import ReplacePolicy, ReturnRefundPolicy
from products.admin import (
    CategoryAdmin, CouponAdminForm, ProductAdmin, ProductAdminForm, ProductImageAdminForm,
    ReturnDetailsInlineForm, restock_selected,
)
from products.models import (
    Category, Coupon, Product, ProductImage, AgeGroup, Tag, SizeVariant,
    ColorVariant, ReturnDetails, ProductReview,
    BundleConfiguration, BundleOffer, BundlePackagingOption, Wishlist, HSNCode,
)
from products.image_utils import product_thumbnail_name


class CouponAdminTests(TestCase):
    def test_coupon_expiry_date_is_required_in_admin(self):
        form = CouponAdminForm()

        self.assertTrue(form.fields['expiry_date'].required)
        self.assertTrue(form.fields['discount_percentage'].required)
        self.assertEqual(form.fields['discount_amount'].label, 'Maximum discount amount')

    def test_coupon_discount_percentage_is_capped_and_respects_minimum_spend(self):
        coupon = Coupon.objects.create(
            coupon_code='PERCENT',
            discount_percentage=15,
            discount_amount=100,
            minimum_amount=500,
            expiry_date=timezone.localdate() + timedelta(days=1),
        )

        self.assertEqual(coupon.calculate_discount(Decimal('400.00')), Decimal('0.00'))
        self.assertEqual(coupon.calculate_discount(Decimal('600.00')), Decimal('90.00'))
        self.assertEqual(coupon.calculate_discount(Decimal('1000.00')), Decimal('100.00'))

    def test_cart_uses_percentage_discount_with_maximum_cap(self):
        category = Category.objects.create(category_name='Coupon test products')
        product = Product.objects.create(
            product_name='Coupon test product',
            category=category,
            price=1000,
            product_desription='Coupon test product',
        )
        cart = Cart.objects.create()
        CartItem.objects.create(cart=cart, product=product, quantity=1)
        coupon = Coupon.objects.create(
            coupon_code='CARTPCT',
            discount_percentage=20,
            discount_amount=100,
            minimum_amount=500,
            expiry_date=timezone.localdate() + timedelta(days=1),
        )
        cart.coupon = coupon
        cart.save(update_fields=['coupon', 'updated_at'])

        self.assertEqual(cart.get_cart_total(), Decimal('1000.00'))
        self.assertEqual(
            cart.get_cart_total_price_after_coupon(),
            Decimal('900.00'),
        )


class CategoryAdminThumbnailTests(TestCase):
    def test_category_admin_thumbnail_uses_image_url(self):
        category = Category(
            category_name='Decorative Items',
            category_image='categories/example.jpg',
        )

        thumbnail = str(CategoryAdmin(Category, admin.site).image_thumbnail(category))

        self.assertIn('src="/media/categories/example.jpg"', thumbnail)
        self.assertIn('width="60" height="60"', thumbnail)


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


class HSNCodeAdminTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(category_name='Decorative Items')

    def test_csv_reference_rows_are_loaded(self):
        self.assertEqual(HSNCode.objects.count(), 13)
        clay_idols = HSNCode.objects.get(product_material='Pure Clay Idols')
        self.assertEqual(clay_idols.hsn_code, '6913')
        self.assertEqual(clay_idols.gst_rate, 0)
        self.assertIn('Terracotta statues', clay_idols.details)

    def test_product_admin_form_copies_selected_reference_into_snapshots(self):
        reference = HSNCode.objects.get(product_material='Wood, Stone & Marble Items')
        form = ProductAdminForm(data={
            'product_name': 'Marble Statue',
            'category': self.category.pk,
            'brand_name': 'Generic',
            'price': 500,
            'marked_price': 0,
            'product_desription': 'Hand carved statue',
            'hsn_reference': reference.pk,
            'sku': 'MARBLE-001',
            'stock_quantity': 0,
            'low_stock_threshold': 5,
            'gift_wrap_price': 0,
        })

        self.assertTrue(form.is_valid(), form.errors)
        product = form.save()

        self.assertEqual(product.hsn_code, 'Chapter 68')
        self.assertEqual(product.gst_rate, reference.gst_rate)
        self.assertEqual(product.hsn_reference, reference)

    def test_reference_changes_copy_snapshots_but_later_reference_edits_do_not(self):
        reference = HSNCode.objects.get(product_material='Stainless Steel Utensils')
        product = Product.objects.create(
            product_name='Steel Bowl',
            category=self.category,
            price=100,
            product_desription='Steel bowl',
            hsn_reference=reference,
        )
        self.assertEqual(product.hsn_code, '7323')
        self.assertEqual(product.gst_rate, 5)

        reference.gst_rate = 12
        reference.save()
        product.stock_quantity = 10
        product.save(update_fields=['stock_quantity'])
        product.refresh_from_db()
        self.assertEqual(product.gst_rate, 5)

        new_reference = HSNCode.objects.get(product_material='Plastic Kitchenware')
        product.hsn_reference = new_reference
        product.save(update_fields=['hsn_reference'])
        product.refresh_from_db()
        self.assertEqual(product.hsn_code, '3924')
        self.assertEqual(product.gst_rate, 18)

    def test_product_admin_uses_searchable_reference_and_option_selectors(self):
        product_admin = ProductAdmin(Product, admin.site)

        self.assertEqual(
            product_admin.autocomplete_fields,
            ['hsn_reference', 'color_variant', 'age_groups', 'tags'],
        )

    def test_product_entry_page_renders_searchable_reference_and_variant_fields(self):
        admin_user = User.objects.create_superuser(
            username='hsn-product-admin',
            email='hsn-admin@example.com',
            password='secret123',
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse('admin:products_product_add'))

        self.assertEqual(response.status_code, 200)
        for field_name in ('hsn_reference', 'color_variant', 'age_groups', 'tags'):
            self.assertContains(response, f'name="{field_name}"')
        self.assertContains(response, 'admin-autocomplete')


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

    def test_category_product_cards_use_generated_webp_thumbnail(self):
        image_data = BytesIO()
        Image.new('RGB', (900, 700), 'blue').save(image_data, format='JPEG')
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                image = ProductImage.objects.create(
                    product=self.product,
                    image_url=SimpleUploadedFile(
                        'trail-runner.jpg',
                        image_data.getvalue(),
                        content_type='image/jpeg',
                    ),
                )

                response = self.client.get(
                    reverse('category_products', args=[self.category.slug]),
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'src="{image.thumbnail_url}"')
                self.assertTrue(image.thumbnail_url.endswith('.webp'))

    def test_category_page_shows_coming_soon_when_category_has_no_products(self):
        empty_category = Category.objects.create(category_name='Watches')

        response = self.client.get(
            reverse('category_products', args=[empty_category.slug]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Coming soon')
        self.assertContains(response, "We're preparing something special for Watches.")
        self.assertNotContains(response, 'Trail Runner')

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
        self.assertContains(response, 'Apply filters')
        self.assertNotContains(response, 'onchange="this.form.submit()"')

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


class ProductImageAdminUploadTests(TestCase):
    def test_saving_product_image_creates_webp_thumbnail_without_changing_original(self):
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                category = Category.objects.create(
                    category_name='Thumbnail products',
                )
                product = Product.objects.create(
                    product_name='Thumbnail product',
                    category=category,
                    price=100,
                    product_desription='Product used to test thumbnails',
                )
                image_data = BytesIO()
                Image.new('RGB', (1200, 900), 'green').save(
                    image_data,
                    format='JPEG',
                )

                product_image = ProductImage.objects.create(
                    product=product,
                    image_url=SimpleUploadedFile(
                        'thumbnail-source.jpg',
                        image_data.getvalue(),
                        content_type='image/jpeg',
                    ),
                )

                self.assertTrue(product_image.thumbnail_url.endswith('.webp'))
                with product_image.image_url.open('rb') as original_file:
                    with Image.open(original_file) as original:
                        self.assertEqual(original.size, (1200, 900))

                thumbnail_name = product_thumbnail_name(
                    product_image.image_url.name,
                )
                with default_storage.open(thumbnail_name, 'rb') as thumbnail_file:
                    with Image.open(thumbnail_file) as thumbnail:
                        self.assertEqual(thumbnail.format, 'WEBP')
                        self.assertEqual(thumbnail.size, (640, 480))

    def test_admin_accepts_webp_and_avif_product_images(self):
        category = Category.objects.create(category_name='AVIF products')
        product = Product.objects.create(
            product_name='AVIF product',
            category=category,
            price=100,
            product_desription='Product with AVIF image',
        )
        for image_format, extension, content_type in (
            ('WEBP', 'webp', 'image/webp'),
            ('AVIF', 'avif', 'image/avif'),
        ):
            with self.subTest(image_format=image_format):
                image_data = BytesIO()
                Image.new('RGB', (2, 2), 'green').save(
                    image_data,
                    format=image_format,
                )
                form = ProductImageAdminForm(
                    data={'product': product.pk},
                    files={
                        'image_url': SimpleUploadedFile(
                            f'product.{extension}',
                            image_data.getvalue(),
                            content_type=content_type,
                        ),
                    },
                )

                self.assertIn(
                    f'.{extension}',
                    form.fields['image_url'].widget.attrs['accept'],
                )
                self.assertTrue(form.is_valid(), form.errors)


class BundleOffersPageTests(TestCase):
    def test_empty_bundles_page_shows_coming_soon_banner(self):
        response = self.client.get(reverse('bundle_offers'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gift bundles are coming soon')
        self.assertContains(
            response,
            "We're putting together thoughtful bundles for every occasion. Check back soon!",
        )
        self.assertNotContains(response, 'Create your own bundle')

    def test_bundles_page_shows_active_offers_without_coming_soon_banner(self):
        offer = BundleOffer.objects.create(
            title='Birthday Favorites',
            description='A bundle for birthdays.',
        )

        response = self.client.get(reverse('bundle_offers'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, offer.title)
        self.assertNotContains(response, 'Gift bundles are coming soon')
        self.assertContains(response, 'Create your own bundle')
        self.assertContains(response, reverse('create_custom_bundle'))


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
        self.assertContains(response, 'aria-expanded="true"')

    def test_category_search_combines_search_brand_age_and_sort_filters(self):
        age_group = AgeGroup.objects.create(name='Adults')
        self.runner.brand_name = 'Stride'
        self.runner.age_groups.add(age_group)
        self.runner.save()

        response = self.client.get(
            reverse('category_products', args=[self.shoes.slug]),
            {
                'q': 'trail',
                'brand': 'Stride',
                'age_group': 'Adults',
                'sort': 'priceDesc',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['products']), [self.runner])
        self.assertEqual(response.context['selected_brand'], 'Stride')
        self.assertEqual(response.context['selected_age_group'], 'Adults')
        self.assertEqual(response.context['selected_sort'], 'priceDesc')

    def test_home_filters_wait_for_apply_button(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'Apply filters')
        self.assertNotContains(response, 'onchange="this.form.submit()"')


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
        self.assertContains(response, 'value="returnable" selected>Returnable')
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
        self.assertContains(response, '<h2 class="h5">\n                <i class="fas fa-undo" aria-hidden="true"></i>\n                Return\n              </h2>')
        self.assertContains(response, '<h2 class="h5">\n                <i class="fas fa-wallet" aria-hidden="true"></i>\n                Refund\n              </h2>')
        self.assertContains(response, '14 days')
        self.assertContains(response, 'Refunds for this product are store credit only.')
        self.assertContains(response, 'class="return-policy-cards"')
        self.assertContains(
            response,
            '<summary class="product-return-badge return-policy-toggle">',
        )
        self.assertNotContains(
            response,
            '>Return and Refund Policy<',
            html=False,
        )
        self.assertContains(
            response,
            '.return-policy-section[open] .return-policy-content {\n'
            '    margin-top: 1rem;',
            html=False,
        )
        self.assertContains(response, 'Returnable')
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
                if policy == 'non_returnable':
                    self.assertContains(
                        response,
                        '<span class="product-return-badge non-returnable">'
                        'Non-Returnable</span>',
                    )
                    self.assertNotContains(
                        response,
                        '<details class="return-policy-section"',
                        html=False,
                    )
                    self.assertNotContains(response, guidance)
                else:
                    badge_class = (
                        'product-return-badge return-policy-toggle'
                        if policy == 'returnable'
                        else 'product-return-badge replaceable return-policy-toggle'
                    )
                    self.assertContains(
                        response,
                        f'<summary class="{badge_class}">',
                    )
                    self.assertNotContains(
                        response,
                        '>Return and Refund Policy<',
                        html=False,
                    )
                    self.assertContains(response, status)
                    self.assertContains(response, guidance)
                    if policy == 'replaceable':
                        self.assertContains(response, 'Replace Policy')
                        self.assertNotContains(response, 'fas fa-wallet')
                    else:
                        self.assertContains(response, 'fas fa-undo')
                        self.assertContains(response, 'fas fa-wallet')

    def test_generic_policy_is_used_when_product_has_no_return_details(self):
        generic_policy = ReturnRefundPolicy.get_solo()
        generic_policy.return_summary = '<p><strong>Generic return summary.</strong></p>'
        generic_policy.return_details = '<ul><li>Generic return details.</li></ul>'
        generic_policy.refund_summary = '<p><em>Generic refund summary.</em></p>'
        generic_policy.refund_details = (
            '<p><a href="https://example.com/refunds">Generic refund details.</a></p>'
        )
        generic_policy.save()
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Toy car',
            category=category,
            price=500,
            product_desription='A toy car',
        )

        response = self.client.get(reverse('get_product', args=[product.slug]))

        self.assertContains(response, '<p><strong>Generic return summary.</strong></p>')
        self.assertContains(response, '<ul><li>Generic return details.</li></ul>')
        self.assertContains(response, '<p><em>Generic refund summary.</em></p>')
        self.assertContains(
            response,
            '<a href="https://example.com/refunds">Generic refund details.</a>',
        )

    def test_policy_html_is_sanitized_before_rendering(self):
        generic_policy = ReturnRefundPolicy.get_solo()
        generic_policy.return_summary = (
            '<p onclick="alert(1)"><strong>Safe return</strong></p>'
            '<script>alert(2)</script>'
            '<a href="javascript:alert(3)">unsafe link</a>'
        )
        generic_policy.save()
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Safe policy toy',
            category=category,
            price=500,
            product_desription='A toy with safe policy rendering',
        )

        response = self.client.get(reverse('get_product', args=[product.slug]))

        self.assertContains(response, '<p><strong>Safe return</strong></p>')
        self.assertContains(response, '<a>unsafe link</a>')
        self.assertNotContains(response, 'onclick')
        self.assertNotContains(response, '<script>alert(2)</script>', html=False)
        self.assertNotContains(response, 'href="javascript:')

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

    def test_replace_product_policy_overrides_central_policy_and_omits_refunds(self):
        central_policy = ReplacePolicy.get_solo()
        central_policy.replace_summary = '<p><strong>Central replacement summary.</strong></p>'
        central_policy.replace_details = '<ul><li>Central replacement details.</li></ul>'
        central_policy.save()
        category = Category.objects.create(category_name='Gifts')
        product = Product.objects.create(
            product_name='Replaceable gift',
            category=category,
            price=600,
            product_desription='A replaceable gift',
        )
        ReturnDetails.objects.create(
            product=product,
            return_policy='replaceable',
            policy='Product replacement terms.',
            conditions='Product replacement conditions.',
        )

        response = self.client.get(reverse('get_product', args=[product.slug]))

        self.assertContains(response, 'Replace Policy')
        self.assertContains(response, 'Product replacement terms.')
        self.assertContains(response, 'Product replacement conditions.')
        self.assertNotContains(response, 'Central replacement summary.')
        self.assertNotContains(response, 'Central replacement details.')
        self.assertNotContains(response, '>Refund<', html=False)

    def test_replace_product_uses_central_policy_when_product_copy_is_blank(self):
        central_policy = ReplacePolicy.get_solo()
        central_policy.replace_summary = '<p><strong>Central replacement summary.</strong></p>'
        central_policy.replace_details = '<ul><li>Central replacement details.</li></ul>'
        central_policy.save()
        category = Category.objects.create(category_name='Gifts')
        product = Product.objects.create(
            product_name='Standard replacement gift',
            category=category,
            price=600,
            product_desription='A replacement gift',
        )
        ReturnDetails.objects.create(product=product, return_policy='replaceable')

        response = self.client.get(reverse('get_product', args=[product.slug]))

        self.assertContains(
            response,
            '<p><strong>Central replacement summary.</strong></p>',
        )
        self.assertContains(response, '<ul><li>Central replacement details.</li></ul>')
        self.assertNotContains(response, '>Refund<', html=False)

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

    def test_buy_now_redirects_authenticated_customer_to_checkout_with_coupon_application(self):
        coupon = Coupon.objects.create(
            coupon_code='BUYNOW',
            discount_amount=50,
            minimum_amount=500,
            expiry_date=timezone.localdate() + timedelta(days=30),
        )

        response = self.client.post(
            reverse('add_to_cart', args=[self.product.uid]),
            {'quantity': 1, 'buy_now': '1'},
        )

        self.assertRedirects(response, reverse('checkout'))
        self.assertTrue(
            CartItem.objects.filter(
                cart__user=self.user, product=self.product, quantity=1,
            ).exists())
        checkout_response = self.client.get(reverse('checkout'))
        self.assertContains(checkout_response, 'Apply Coupon')
        self.assertContains(checkout_response, coupon.coupon_code)
        checkout_html = checkout_response.content.decode()
        self.assertIn(
            '<details class="card mb-4 cart-coupon-disclosure checkout-coupon-section">',
            checkout_html,
        )
        self.assertLess(
            checkout_html.index('Review items and charges'),
            checkout_html.index('Apply Coupon'),
        )
        self.assertIn('cart-coupon-summary-detail', checkout_html)
        self.assertIn('justify-content: flex-end;', checkout_html)
        self.assertIn('max-width: 58%;', checkout_html)
        self.assertIn('checkout-coupon-chevron', checkout_html)
        self.assertNotIn('fa-chevron-down', checkout_html)

        self.client.post(
            reverse('checkout'),
            {'coupon': coupon.coupon_code, 'apply_coupon': '1'},
        )
        cart = Cart.objects.get(user=self.user, is_paid=False)
        self.assertEqual(cart.coupon, coupon)
        applied_response = self.client.get(reverse('checkout'))
        self.assertContains(applied_response, 'Coupon applied')
        self.assertContains(
            applied_response,
            '<details class="card mb-4 cart-coupon-disclosure checkout-coupon-section" open>',
            html=False,
        )
        self.assertContains(
            applied_response,
            'class="checkout-coupon-remove-form"',
        )

    def test_buy_now_after_emptying_cart_does_not_keep_previous_coupon(self):
        coupon = Coupon.objects.create(
            coupon_code='PREVIOUS',
            expiry_date=timezone.localdate() + timedelta(days=30),
        )
        cart = Cart.objects.create(user=self.user)
        item = CartItem.objects.create(cart=cart, product=self.product)
        cart.coupon = coupon
        cart.save(update_fields=['coupon', 'updated_at'])

        removal = self.client.post(
            reverse('update_cart_item'),
            data=json.dumps({
                'cart_item_id': str(item.uid),
                'quantity': 0,
            }),
            content_type='application/json',
        )

        self.assertEqual(removal.status_code, 200)
        cart.refresh_from_db()
        self.assertIsNone(cart.coupon)

        buy_now_response = self.client.post(
            reverse('add_to_cart', args=[self.product.uid]),
            {'quantity': 1, 'buy_now': '1'},
        )

        self.assertRedirects(buy_now_response, reverse('checkout'))
        checkout_response = self.client.get(reverse('checkout'))
        self.assertNotContains(checkout_response, 'Coupon applied')
        self.assertNotContains(
            checkout_response,
            f'Coupon <strong>{coupon.coupon_code}</strong> applied',
        )

    def test_cart_visit_clears_buy_now_coupon_checkout_state(self):
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, product=self.product)
        self.client.post(
            reverse('add_to_cart', args=[self.product.uid]),
            {'quantity': 1, 'buy_now': '1'},
        )

        cart_response = self.client.get(reverse('cart'))
        self.assertEqual(cart_response.status_code, 200)
        response = self.client.get(reverse('checkout'))

        self.assertEqual(response.context['cart'], cart)
        self.assertNotContains(response, 'Apply Coupon')

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

    def test_product_image_opens_in_accessible_full_size_viewer(self):
        ProductImage.objects.create(
            product=self.product,
            image_url='products/toy-robot.jpg',
        )

        response = self.client.get(reverse('get_product', args=[self.product.slug]))

        self.assertContains(response, 'class="product-main-image"')
        self.assertContains(
            response,
            '.product-detail-card .gallery-wrap .img-big-wrap .product-main-image {',
        )
        self.assertContains(response, 'height: min(70vh, 600px);')
        self.assertContains(response, 'object-fit: contain;')
        self.assertContains(response, 'role="button" tabindex="0"')
        self.assertContains(response, 'id="productImageViewer"')
        self.assertContains(response, 'id="productImageViewerImage"')
        self.assertContains(response, '.product-image-viewer[open] {')
        self.assertContains(response, '.product-image-viewer:not([open]) {')
        self.assertContains(response, 'justify-content: center;')
        self.assertContains(response, 'align-items: center;')
        self.assertContains(response, 'imageViewer.showModal()')
        self.assertContains(response, 'imageViewer.close()')
        self.assertNotContains(response, 'zoomed-in')

    def test_product_gallery_supports_multiple_images_and_navigation(self):
        ProductImage.objects.create(
            product=self.product,
            image_url='products/toy-robot-front.jpg',
        )
        ProductImage.objects.create(
            product=self.product,
            image_url='products/toy-robot-back.jpg',
        )

        response = self.client.get(reverse('get_product', args=[self.product.slug]))

        self.assertEqual(len(response.context['product_images']), 2)
        self.assertEqual(response.content.count(b'data-gallery-slide>'), 2)
        self.assertContains(response, 'aria-label="Show previous product image"')
        self.assertContains(response, 'aria-label="Show next product image"')
        self.assertContains(response, 'data-gallery-thumbnail="1"')
        self.assertContains(response, 'imageTrack.addEventListener("touchstart"')
        self.assertContains(response, 'imageTrack.addEventListener("touchend"')
        self.assertContains(response, 'moveProductGallery(deltaX < 0 ? 1 : -1)')

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
        self.assertNotContains(response, '<script>alert(2)</script>', html=False)


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

    def test_buy_now_redirects_guest_to_shipping_with_coupon_application(self):
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Wooden Puzzle',
            category=category,
            price=600,
            product_desription='A wooden puzzle',
            stock_quantity=5,
        )
        coupon = Coupon.objects.create(
            coupon_code='GUESTNOW',
            discount_amount=50,
            minimum_amount=500,
            expiry_date=timezone.localdate() + timedelta(days=30),
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
        buy_now_cart = Cart.objects.get(is_buy_now=True)
        self.assertEqual(
            CartItem.objects.get(cart=buy_now_cart, product=product).quantity,
            1,
        )
        self.assertContains(self.client.get(reverse('guest-checkout')), coupon.coupon_code)
        self.client.post(
            reverse('guest-checkout'),
            {'coupon': coupon.coupon_code, 'apply_coupon': '1'},
        )
        buy_now_cart.refresh_from_db()
        self.assertEqual(buy_now_cart.coupon, coupon)

    def test_buy_now_keeps_existing_cart_item_and_applies_gift_wrap_to_purchase(self):
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
        self.assertFalse(cart_item.gift_wrap)
        buy_now_cart_item = CartItem.objects.get(
            cart__is_buy_now=True,
            product=product,
        )
        self.assertTrue(buy_now_cart_item.gift_wrap)
        self.assertEqual(buy_now_cart_item.get_product_price(), 630)
        checkout_response = self.client.get(reverse('guest-checkout'))
        self.assertContains(checkout_response, '₹630')
        self.assertNotContains(checkout_response, 'Gift wrapped (+₹30 per item)')
        cart_response = self.client.get(reverse('cart'))
        self.assertNotContains(cart_response, 'Gift wrapped (+₹30 per item)')
        self.assertContains(cart_response, '₹600')

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
            cart__is_buy_now=True,
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
        self.assertEqual(cart_item.quantity, 2)

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
