from django.contrib import admin
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Cart, CartItem
from products.admin import ProductAdmin, restock_selected
from products.models import (
    Category, Product, AgeGroup, Tag, ReturnDetails, ProductReview,
    BundleConfiguration, BundleOffer, BundlePackagingOption,
)


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
        self.assertRedirects(response, reverse('cart'))
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
    def test_return_details_attach_to_product_and_render(self):
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
            conditions='Item must be unused and in original packaging.',
        )

        response = self.client.get(reverse('get_product', args=[product.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Return Details')
        self.assertContains(response, '14 days')
        self.assertContains(response, 'Buy Now')


class ProductReviewPageTests(TestCase):
    def test_product_reviews_page_is_scoped_and_sortable(self):
        category = Category.objects.create(category_name='Games')
        product = Product.objects.create(
            product_name='Strategy Game',
            category=category,
            price=1200,
            product_desription='A strategy game',
        )
        low_reviewer = User.objects.create_user(username='low-reviewer')
        high_reviewer = User.objects.create_user(username='high-reviewer')
        ProductReview.objects.create(product=product, user=low_reviewer, stars=2, content='Not great')
        ProductReview.objects.create(product=product, user=high_reviewer, stars=5, content='Excellent')

        response = self.client.post(
            reverse('product_reviews_for_product', args=[product.slug]),
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
