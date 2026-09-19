import random
import logging
from .forms import ReviewForm
from django.db.models import Q, Count
from django.urls import reverse
from django.contrib import messages
from accounts.models import Cart, CartItem, BundleCartItem, BundleCartProduct
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from products.models import (
    Product, SizeVariant, ProductReview, Wishlist, Category, Tag,
    BundleOffer, BundleConfiguration, BundlePackagingOption,
)

logger = logging.getLogger(__name__)

# Create your views here.

def categories(request):
    categories = Category.objects.all().order_by('category_name')
    return render(request, 'product/categories.html', {'categories': categories})


def bundle_offers(request):
    offers = BundleOffer.objects.filter(is_active=True).prefetch_related('products__product_images')
    return render(request, 'product/bundles.html', {'offers': offers})


def _custom_bundle_products(product_ids):
    return list(Product.objects.filter(
        uid__in=product_ids,
        bundle_eligible=True,
        stock_quantity__gt=0,
    ).prefetch_related('product_images').order_by('product_name'))


def create_custom_bundle(request):
    available_products = Product.objects.filter(
        bundle_eligible=True,
        stock_quantity__gt=0,
    ).prefetch_related('product_images').order_by('product_name')
    max_products = BundleConfiguration.get_solo().max_products
    error = None
    quantity = 1

    if request.method == 'POST':
        selected_ids = list(dict.fromkeys(request.POST.getlist('products')))
        try:
            quantity = max(1, int(request.POST.get('quantity', 1)))
        except (TypeError, ValueError):
            quantity = 1
        selected_products = _custom_bundle_products(selected_ids)
        if len(selected_products) != len(selected_ids):
            error = 'One or more selected products are no longer available.'
        elif not 1 <= len(selected_products) <= max_products:
            error = f'Choose between 1 and {max_products} products.'
        elif any(product.stock_quantity < quantity for product in selected_products):
            error = 'The selected quantity is not available for every product.'
        if not error:
            request.session['custom_bundle_selection'] = {
                'product_ids': selected_ids,
                'quantity': quantity,
            }
            return redirect('custom_bundle_packaging')

    return render(request, 'product/custom_bundle_builder.html', {
        'available_products': available_products,
        'max_products': max_products,
        'quantity': quantity,
        'error': error,
    })


def custom_bundle_packaging(request):
    selection = request.session.get('custom_bundle_selection')
    if not selection:
        messages.info(request, 'Choose products to create your bundle.')
        return redirect('create_custom_bundle')

    selected_products = _custom_bundle_products(selection.get('product_ids', []))
    if len(selected_products) != len(selection.get('product_ids', [])):
        messages.warning(request, 'One or more selected products are no longer available.')
        request.session.pop('custom_bundle_selection', None)
        return redirect('create_custom_bundle')

    packaging_options = BundlePackagingOption.objects.filter(is_active=True)
    if request.method == 'POST':
        packaging_id = request.POST.get('packaging_option')
        packaging = packaging_options.filter(uid=packaging_id).first()
        if not packaging:
            return render(request, 'product/custom_bundle_packaging.html', {
                'selected_products': selected_products,
                'packaging_options': packaging_options,
                'error': 'Choose a packaging option before continuing.',
            })
        request.session['custom_bundle_selection']['packaging_id'] = str(packaging.uid)
        request.session.modified = True
        return redirect('custom_bundle_review')

    return render(request, 'product/custom_bundle_packaging.html', {
        'selected_products': selected_products,
        'packaging_options': packaging_options,
    })


def custom_bundle_review(request):
    selection = request.session.get('custom_bundle_selection')
    if not selection:
        messages.info(request, 'Choose products to create your bundle.')
        return redirect('create_custom_bundle')

    selected_products = _custom_bundle_products(selection.get('product_ids', []))
    quantity = max(1, int(selection.get('quantity', 1)))
    if len(selected_products) != len(selection.get('product_ids', [])):
        messages.warning(request, 'One or more selected products are no longer available.')
        request.session.pop('custom_bundle_selection', None)
        return redirect('create_custom_bundle')
    if any(product.stock_quantity < quantity for product in selected_products):
        messages.warning(request, 'The selected quantity is no longer available for every product.')
        request.session.pop('custom_bundle_selection', None)
        return redirect('create_custom_bundle')

    packaging_id = selection.get('packaging_id')
    packaging = BundlePackagingOption.objects.filter(uid=packaging_id, is_active=True).first()
    if not packaging:
        messages.info(request, 'Choose a packaging option for your bundle.')
        return redirect('custom_bundle_packaging')

    original_price = sum(product.price for product in selected_products)
    unit_price = original_price + packaging.price
    if request.method == 'POST':
        if not request.user.is_authenticated and not request.session.session_key:
            request.session.create()
        cart_filters = {
            'user': request.user if request.user.is_authenticated else None,
            'session_key': None if request.user.is_authenticated else request.session.session_key,
            'is_paid': False,
        }
        cart = Cart.objects.filter(**cart_filters).first()
        if not cart:
            cart = Cart.objects.create(**{
                key: value for key, value in cart_filters.items()
                if key != 'is_paid'
            })
        bundle_item = BundleCartItem.objects.create(
            cart=cart,
            quantity=quantity,
            original_unit_price=original_price,
            unit_price=unit_price,
            packaging_option=packaging,
            packaging_price=packaging.price,
        )
        BundleCartProduct.objects.bulk_create([
            BundleCartProduct(bundle_item=bundle_item, product=product)
            for product in selected_products
        ])
        request.session.pop('custom_bundle_selection', None)
        messages.success(request, 'Your bundle was added to the cart.')
        return redirect('cart')

    return render(request, 'product/custom_bundle_review.html', {
        'selected_products': selected_products,
        'quantity': quantity,
        'packaging': packaging,
        'product_unit_price': original_price,
        'unit_price': unit_price,
        'total_price': unit_price * quantity,
    })


def bundle_builder(request, slug):
    offer = get_object_or_404(
        BundleOffer.objects.prefetch_related('products__product_images'),
        slug=slug,
        is_active=True,
    )
    available_products = offer.products.filter(bundle_eligible=True, stock_quantity__gt=0).order_by('product_name')
    max_products = BundleConfiguration.get_solo().max_products
    selected_products = []
    quantity = 1
    error = None

    if request.method == 'POST':
        selected_ids = list(dict.fromkeys(request.POST.getlist('products')))
        try:
            quantity = max(1, int(request.POST.get('quantity', 1)))
        except (TypeError, ValueError):
            quantity = 1
        selected_products = list(available_products.filter(uid__in=selected_ids))
        if len(selected_products) != len(selected_ids):
            error = 'One or more selected products are no longer available.'
        elif not 1 <= len(selected_products) <= max_products:
            error = f'Choose between 1 and {max_products} products.'
        elif any(product.stock_quantity < quantity for product in selected_products):
            error = 'The selected quantity is not available for every product.'
        if not error:
            original_price = sum(product.price for product in selected_products)
            unit_price = offer.price_for(selected_products)
            if not request.user.is_authenticated and not request.session.session_key:
                request.session.create()
            cart = Cart.objects.filter(
                user=request.user if request.user.is_authenticated else None,
                session_key=None if request.user.is_authenticated else request.session.session_key,
                is_paid=False,
            ).first()
            if not cart:
                cart = Cart.objects.create(
                    user=request.user if request.user.is_authenticated else None,
                    session_key=None if request.user.is_authenticated else request.session.session_key,
                )
            bundle_item = BundleCartItem.objects.create(
                cart=cart,
                bundle_offer=offer,
                quantity=quantity,
                original_unit_price=original_price,
                unit_price=unit_price,
            )
            BundleCartProduct.objects.bulk_create([
                BundleCartProduct(bundle_item=bundle_item, product=product)
                for product in selected_products
            ])
            messages.success(request, 'Your bundle was added to the cart.')
            return redirect('cart')

    return render(request, 'product/bundle_builder.html', {
        'offer': offer,
        'available_products': available_products,
        'selected_products': selected_products,
        'max_products': max_products,
        'quantity': quantity,
        'error': error,
    })


def category_products(request, slug):
    category = get_object_or_404(Category, slug=slug)
    search_term = (request.GET.get('q', '') or '').strip()
    products = Product.objects.filter(show_in_catalog=True, category=category).select_related('category').prefetch_related('product_images', 'age_groups', 'tags').order_by('product_name', 'uid')

    selected_tag = request.GET.get('tag')
    if search_term:
        products = products.filter(
            Q(product_name__icontains=search_term) |
            Q(brand_name__icontains=search_term) |
            Q(product_desription__icontains=search_term) |
            Q(age_groups__name__icontains=search_term) |
            Q(tags__name__icontains=search_term)
        ).distinct()

    if selected_tag:
        products = products.filter(tags__name=selected_tag).distinct()

    categories = Category.objects.all()
    context = {
        'products': products,
        'categories': categories,
        'selected_category': category.category_name,
        'selected_sort': '',
        'selected_query': search_term,
        'selected_tag': selected_tag,
        'tags': Tag.objects.order_by('name'),
    }
    return render(request, 'home/index.html', context)


def get_product(request, slug):
    product = get_object_or_404(Product, slug=slug)
    sorted_size_variants = product.size_variant.all().order_by('size_name')
    related_products = list(product.category.products.filter(parent=None).exclude(uid=product.uid))

    # Review product view
    review = None
    if request.user.is_authenticated:
        try:
            review = ProductReview.objects.filter(product=product, user=request.user).first()
        except Exception:
            logger.exception("Product review lookup failed for product %s", product.uid)
            messages.warning(request, "No reviews found for this product")

    rating_percentage = 0
    if product.reviews.exists():
        rating_percentage = (product.get_rating() / 5) * 100

    if request.method == 'POST' and request.user.is_authenticated:
        if review:
            # If review exists, update it
            review_form = ReviewForm(request.POST, instance=review)
        else:
            # Otherwise, create a new review
            review_form = ReviewForm(request.POST)

        if review_form.is_valid():
            review = review_form.save(commit=False)
            review.product = product
            review.user = request.user
            review.save()
            messages.success(request, "Review added successfully!")
            return redirect('get_product', slug=slug)
    else:
        review_form = ReviewForm()

    # Related product view
    if len(related_products) >= 4:
        related_products = random.sample(related_products, 4)

    in_wishlist = False
    if request.user.is_authenticated:
        in_wishlist = Wishlist.objects.filter(user=request.user, product=product).exists()

    context = {
        'product': product,
        'sorted_size_variants': sorted_size_variants,
        'related_products': related_products,
        'review_form': review_form,
        'rating_percentage': rating_percentage,
        'in_wishlist': in_wishlist,
    }

    if request.GET.get('size'):
        size = request.GET.get('size')
        price = product.get_product_price_by_size(size)
        context['selected_size'] = size
        context['updated_price'] = price

    return render(request, 'product/product.html', context=context)


# Product Review view
@login_required
def product_reviews(request):
    reviews = ProductReview.objects.filter(
        user=request.user).select_related('product').order_by('-date_added')
    return render(request, 'product/all_product_reviews.html', {'reviews': reviews})


def product_reviews_for_product(request, slug):
    product = get_object_or_404(Product, slug=slug)
    sort = request.GET.get('sort', 'latest')
    reviews = product.reviews.select_related('user').annotate(
        helpful_count=Count('likes', distinct=True),
        unhelpful_count=Count('dislikes', distinct=True),
    )

    sort_options = {
        'rating_desc': ('-stars', '-date_added'),
        'rating_asc': ('stars', '-date_added'),
        'latest': ('-date_added', '-uid'),
        'top': ('-helpful_count', '-stars', '-date_added'),
        'lowest': ('-unhelpful_count', 'stars', '-date_added'),
    }
    if sort not in sort_options:
        sort = 'latest'
    reviews = reviews.order_by(*sort_options[sort])

    return render(request, 'product/product_reviews.html', {
        'product': product,
        'reviews': reviews,
        'selected_sort': sort,
    })


# Edit Review view
@login_required
def edit_review(request, review_uid):
    review = ProductReview.objects.filter(uid=review_uid, user=request.user).first()
    if not review:
        return JsonResponse({"detail": "Review not found"}, status=404)
    
    if request.method == "POST":
        stars = request.POST.get("stars")
        content = request.POST.get("content")
        review.stars = stars
        review.content = content
        review.save()
        messages.success(request, "Your review has been updated successfully.")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

    return JsonResponse({"detail": "Invalid request"}, status=400)

# Like and Dislike review view
@require_POST
@login_required
def like_review(request, review_uid):
    review = ProductReview.objects.filter(uid=review_uid).first()

    if request.user in review.likes.all():
        review.likes.remove(request.user)
    else:
        review.likes.add(request.user)
        review.dislikes.remove(request.user)
    return JsonResponse({'likes': review.like_count(), 'dislikes': review.dislike_count()})


@require_POST
@login_required
def dislike_review(request, review_uid):
    review = ProductReview.objects.filter(uid=review_uid).first()

    if request.user in review.dislikes.all():
        review.dislikes.remove(request.user)
    else:
        review.dislikes.add(request.user)
        review.likes.remove(request.user)
    return JsonResponse({'likes': review.like_count(), 'dislikes': review.dislike_count()})


# delete review view
@require_POST
@login_required
def delete_review(request, slug, review_uid):
    review = ProductReview.objects.filter(uid=review_uid, product__slug=slug, user=request.user).first()
    
    if not review:
        messages.error(request, "Review not found.")
        return redirect('get_product', slug=slug)

    review.delete()
    messages.success(request, "Your review has been deleted.")
    return HttpResponseRedirect(request.META.get('HTTP_REFERER'))


# Add a product to Wishlist
@login_required
def add_to_wishlist(request, uid):
    variant = request.GET.get('size')
    if not variant:
        messages.warning(request, 'Please select a size variant before adding to the wishlist!')
        return redirect(request.META.get('HTTP_REFERER'))

    product = get_object_or_404(Product, uid=uid)
    size_variant = get_object_or_404(SizeVariant, size_name=variant)
    wishlist, created = Wishlist.objects.get_or_create(
        user=request.user, product=product, size_variant=size_variant)

    if created:
        messages.success(request, "Product added to Wishlist!")

    return redirect(reverse('wishlist'))


# Remove product from wishlist
@login_required
def remove_from_wishlist(request, uid):
    product = get_object_or_404(Product, uid=uid)
    size_variant_name = request.GET.get('size')

    if size_variant_name:
        size_variant = get_object_or_404(SizeVariant, size_name=size_variant_name)
        Wishlist.objects.filter(
            user=request.user, product=product, size_variant=size_variant).delete()
    else:
        Wishlist.objects.filter(user=request.user, product=product).delete()

    messages.success(request, "Product removed from wishlist!")
    return redirect(reverse('wishlist'))


# Wishlist View
@login_required
def wishlist_view(request):
    wishlist_items = Wishlist.objects.filter(user=request.user)
    return render(request, 'product/wishlist.html', {'wishlist_items': wishlist_items})


# Move to cart functionality on wishlist page.
@require_POST
@login_required
def move_to_cart(request, uid):
    product = get_object_or_404(Product, uid=uid)
    wishlist = Wishlist.objects.filter(user=request.user, product=product).first()

    if not wishlist:
        messages.error(request, "Item not found in wishlist.")
        return redirect('wishlist')

    size_variant = wishlist.size_variant
    wishlist.delete()

    cart, created = Cart.objects.get_or_create(user=request.user, is_paid=False)
    cart_item, created = CartItem.objects.get_or_create(
        cart=cart, product=product, size_variant=size_variant)

    if not created:
        cart_item.quantity += 1
        cart_item.save()

    messages.success(request, "Product moved to cart successfully!")
    return redirect('cart')
