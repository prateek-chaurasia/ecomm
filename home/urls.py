from django.urls import path
from home.views import *

urlpatterns = [
    path('', index, name="index"),
    path('search/', product_search, name='product_search'),
    path('contact/', contact, name='contact'),
    path('about/', about, name='about'),
    path(
        'online-shopping-dehradun/',
        dehradun_shopping_guide,
        name='dehradun_shopping_guide',
    ),
    path('terms-and-conditions/', terms_and_conditions, name='terms-and-conditions'),
    path('privacy-policy/', privacy_policy, name='privacy-policy'),
    path(
        'returns-refunds-replacements/',
        return_refund_replace_policy,
        name='return-refund-replace-policy',
    ),
    path('webhook/whatsapp/', whatsapp_webhook, name='whatsapp_webhook'),
]
