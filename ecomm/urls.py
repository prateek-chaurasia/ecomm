from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.http import HttpResponse
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings
from django.contrib.staticfiles.urls import staticfiles_urlpatterns

from ecomm.sitemaps import (
    BundleOfferSitemap,
    CategorySitemap,
    ProductSitemap,
    StaticSitemap,
)


sitemaps = {
    'static': StaticSitemap,
    'categories': CategorySitemap,
    'products': ProductSitemap,
    'bundles': BundleOfferSitemap,
}


def robots_txt(request):
    sitemap_url = request.build_absolute_uri('/sitemap.xml')
    content = f'User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /accounts/\nDisallow: /product/wishlist/\nDisallow: /product/add-to-cart/\nSitemap: {sitemap_url}\n'
    return HttpResponse(content, content_type='text/plain')


urlpatterns = [
    path('robots.txt', robots_txt, name='robots_txt'),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='sitemap'),
    path('admin/', admin.site.urls),
    path('', include('home.urls')),
    path('product/', include('products.urls')),
    path('accounts/', include('accounts.urls')),
    path("accounts/", include("allauth.urls")),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL,
                          document_root=settings.MEDIA_ROOT)


urlpatterns += staticfiles_urlpatterns()
