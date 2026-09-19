from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from home.models import HomeBanner
from products.models import Category, Product


class HomeBannerTests(TestCase):
    def test_active_banners_are_shown_on_home_page(self):
        banner = HomeBanner.objects.create(
            title='Diwali Sale',
            subtitle='Flat 30% off on festive essentials',
            button_text='Shop Now',
            link_url='https://example.com/diwali',
            image=SimpleUploadedFile(
                'banner.png',
                b'fake-image-data',
                content_type='image/png',
            ),
            is_active=True,
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=7),
        )

        response = self.client.get(reverse('index'))

        self.assertEqual(list(response.context['banners']), [banner])
        self.assertContains(response, 'Diwali Sale')


class InfiniteProductLoadingTests(TestCase):
    def test_home_page_returns_next_product_batch_for_ajax_requests(self):
        category = Category.objects.create(category_name='Toys')
        for product_number in range(21):
            Product.objects.create(
                product_name=f'Toy {product_number:02d}',
                category=category,
                price=100,
                product_desription='A toy',
            )

        response = self.client.get(
            reverse('index') + '?page=2',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['html'])
        self.assertFalse(response.json()['has_next'])
        self.assertIn('Toy 20', response.json()['html'])
