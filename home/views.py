import logging

from django.db.models import Q, Avg
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import render
from products.models import Product, Category, Tag
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.utils import timezone

from home.models import HomeBanner

logger = logging.getLogger(__name__)


def _product_search_queryset(search_term):
    term = (search_term or '').strip()
    if not term:
        return Product.objects.none()

    term = term.replace('%', '\\%')
    return Product.objects.filter(
        Q(product_name__icontains=term) |
        Q(brand_name__icontains=term) |
        Q(product_desription__icontains=term) |
        Q(category__category_name__icontains=term) |
        Q(age_groups__name__icontains=term) |
        Q(tags__name__icontains=term),
        show_in_catalog=True,
    ).order_by('product_name', 'uid').distinct()


def index(request):
    query = Product.objects.filter(show_in_catalog=True).select_related('category').prefetch_related('product_images', 'age_groups').order_by('product_name', 'uid')
    categories = Category.objects.all()
    selected_sort = request.GET.get('sort')
    selected_category = request.GET.get('category')
    selected_brand = request.GET.get('brand')
    selected_age_group = request.GET.get('age_group')
    selected_tag = request.GET.get('tag')
    selected_query = request.GET.get('q', '').strip()
    now = timezone.now()
    banners = HomeBanner.objects.filter(
        is_active=True,
        starts_at__lte=now,
        ends_at__gte=now,
    ).order_by('-starts_at')

    if selected_query:
        query = _product_search_queryset(selected_query)

    if selected_category:
        query = query.filter(category__category_name=selected_category)

    if selected_brand:
        query = query.filter(brand_name=selected_brand)

    if selected_age_group:
        query = query.filter(age_groups__name=selected_age_group).distinct()

    if selected_tag:
        query = query.filter(tags__name=selected_tag).distinct()

    if selected_sort:
        if selected_sort == 'newest':
            query = query.filter(newest_product=True).order_by('category_id', 'uid')
        elif selected_sort == 'priceAsc':
            query = query.order_by('price', 'uid')
        elif selected_sort == 'priceDesc':
            query = query.order_by('-price', 'uid')
        elif selected_sort == 'ratingAsc':
            query = query.annotate(avg_rating=Coalesce(Avg('reviews__stars'), 0.0)).order_by('avg_rating', 'product_name', 'uid')
        elif selected_sort == 'ratingDesc':
            query = query.annotate(avg_rating=Coalesce(Avg('reviews__stars'), 0.0)).order_by('-avg_rating', 'product_name', 'uid')

    page = request.GET.get('page', 1)
    paginator = Paginator(query, 20)

    try:
        products = paginator.page(page)
    except PageNotAnInteger:
        products = paginator.page(1)
    except EmptyPage:
        products = paginator.page(paginator.num_pages)
    except Exception:
        logger.exception("Product pagination failed")

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        from django.template.loader import render_to_string
        return JsonResponse({
            'html': render_to_string(
                'product_parts/product_cards.html',
                {'products': products},
                request=request,
            ),
            'has_next': products.has_next(),
            'next_page': products.next_page_number() if products.has_next() else None,
        })

    context = {
        'products': products,
        'categories': categories,
        'selected_category': selected_category,
        'selected_sort': selected_sort,
        'selected_brand': selected_brand,
        'selected_age_group': selected_age_group,
        'selected_tag': selected_tag,
        'selected_query': selected_query,
        'brands': Product.objects.filter(show_in_catalog=True).exclude(brand_name='').values_list('brand_name', flat=True).distinct().order_by('brand_name'),
        'age_groups': sorted(set(
            Product.objects.filter(show_in_catalog=True).exclude(age_groups__isnull=True).values_list('age_groups__name', flat=True)
        )),
        'tags': Tag.objects.order_by('name'),
        'banners': banners,
    }
    return render(request, 'home/index.html', context)


def product_search(request):
    query = (request.GET.get('q', '') or '').strip()
    products = _product_search_queryset(query) if query else Product.objects.none()

    if request.GET.get('format') == 'json':
        suggestions = list(
            products.order_by('product_name').values_list('product_name', flat=True).distinct()[:10]
        )
        return JsonResponse({'suggestions': suggestions})

    context = {'query': query, 'products': products.select_related('category').prefetch_related('product_images', 'age_groups', 'tags')}
    return render(request, 'home/search.html', context)


def contact(request):
    context = {"form_id": "xgvvlrvn"}
    return render(request, 'home/contact.html', context)


def about(request):
    return render(request, 'home/about.html')


def terms_and_conditions(request):
    return render(request, 'home/terms_and_conditions.html')


def privacy_policy(request):
    return render(request, 'home/privacy_policy.html')
