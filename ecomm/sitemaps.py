from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from products.models import BundleOffer, Category, Product


class ProductSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        return Product.objects.filter(show_in_catalog=True).order_by('uid')

    def location(self, item):
        return reverse('get_product', kwargs={'slug': item.slug})


class CategorySitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.7

    def items(self):
        return Category.objects.filter(slug__isnull=False).exclude(slug='').order_by('uid')

    def location(self, item):
        return reverse('category_products', kwargs={'slug': item.slug})


class BundleOfferSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.7

    def items(self):
        return BundleOffer.objects.filter(is_active=True).order_by('uid')

    def location(self, item):
        return reverse('bundle_builder', kwargs={'slug': item.slug})


class StaticSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.5

    def items(self):
        return (
            'index',
            'categories',
            'bundle_offers',
            'about',
            'contact',
            'privacy-policy',
            'terms-and-conditions',
        )

    def location(self, item):
        return reverse(item)