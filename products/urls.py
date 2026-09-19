from django.urls import path
from products.views import *

urlpatterns = [
    path('categories/', categories, name='categories'),
    path('categories/<slug:slug>/', category_products, name='category_products'),
    path('bundles/', bundle_offers, name='bundle_offers'),
    path('bundles/create/', create_custom_bundle, name='create_custom_bundle'),
    path('bundles/packaging/', custom_bundle_packaging, name='custom_bundle_packaging'),
    path('bundles/review/', custom_bundle_review, name='custom_bundle_review'),
    path('bundles/<slug:slug>/', bundle_builder, name='bundle_builder'),
    path('wishlist/', wishlist_view, name='wishlist'),
    path('wishlist/add/<uid>/', add_to_wishlist, name='add_to_wishlist'),
    path('wishlist/move_to_cart/<uid>/', move_to_cart, name='move_to_cart'),
    path('wishlist/remove/<uid>/', remove_from_wishlist, name='remove_from_wishlist'),
    path('product-reviews/', product_reviews, name='product_reviews'),
    path('product-reviews/<slug:slug>/', product_reviews_for_product, name='product_reviews_for_product'),
    path('product-reviews/edit/<uuid:review_uid>/', edit_review, name='edit_review'),
    path('like-review/<review_uid>/', like_review, name='like_review'),
    path('dislike-review/<review_uid>/',dislike_review, name='dislike_review'),
    path('<slug>/', get_product, name='get_product'),
    path('<slug>/<review_uid>/delete/', delete_review, name='delete_review'),
]
