from django.contrib import admin
from django import forms
from django.utils.html import format_html
from base.admin import FilterCollapsibleModelAdmin, PaginatedModelAdmin
from .models import *

# Register your models here.


class CategoryAdminForm(forms.ModelForm):
    # image_file = forms.ImageField(
    #     required=False, help_text="Upload an image file (will be converted to URL)")

    class Meta:
        model = Category
        fields = '__all__'
        # widgets = {
        #     'category_image': forms.URLInput(attrs={
        #         'placeholder': 'Enter image URL or upload a file below',
        #         'style': 'width: 100%;'
        #     })
        # }


class CategoryAdmin(FilterCollapsibleModelAdmin):
    form = CategoryAdminForm
    list_display = ['category_name', 'image_thumbnail']

    @admin.display(description='Thumbnail')
    def image_thumbnail(self, obj):
        if obj.category_image:
            return format_html(
                '<img src="{}" width="60" height="60" style="object-fit: contain;" />',
                obj.category_image.url,
            )
        return "No Image"


class ProductImageAdminForm(forms.ModelForm):
    # image_file = forms.ImageField(
    #     required=False, help_text="Upload an image file (will be converted to URL)")

    class Meta:
        model = ProductImage
        fields = '__all__'
        widgets = {
            'image_url': forms.ClearableFileInput(
                attrs={'accept': '.avif,.jpg,.jpeg,.png,.webp'}
            ),
        }
        # widgets = {
        #     'image_url': forms.URLInput(attrs={
        #         'placeholder': 'Enter image URL or upload a file below',
        #         'style': 'width: 100%;'
        #     })
        # }


class ProductImageAdmin(admin.StackedInline):
    model = ProductImage
    form = ProductImageAdminForm
    extra = 1
    readonly_fields = ['image_preview']

    def image_preview(self, obj):
        if obj.image_url and hasattr(obj.image_url, 'url'):
            return format_html('<img src="{}" width="200" style="object-fit: contain;" />', obj.image_url.url)
        return "No Image"
    image_preview.short_description = 'Preview'


class ProductAdminForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = '__all__'
        help_texts = {
            'product_desription': (
                'Supports basic HTML formatting (paragraphs, bold/italic text, '
                'and ordered or unordered lists). Plain-text newlines are also rendered.'
            ),
        }

    def clean_sku(self):
        return self.cleaned_data['sku'].strip().upper()


class ReturnDetailsInlineForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['return_window_days'].required = False

    class Meta:
        model = ReturnDetails
        fields = ['return_policy', 'return_window_days', 'policy', 'refund_policy', 'conditions']
        help_texts = {
            'return_policy': 'Choose whether this product is returnable, replaceable, or non-returnable.',
            'policy': 'HTML formatting is supported; unsafe tags and attributes are removed when displayed.',
            'refund_policy': 'HTML formatting is supported; unsafe tags and attributes are removed when displayed.',
            'conditions': 'HTML formatting is supported; unsafe tags and attributes are removed when displayed.',
        }

    def clean_return_window_days(self):
        days = self.cleaned_data.get('return_window_days')
        if days is None:
            return ReturnDetails._meta.get_field('return_window_days').get_default()
        return days

    def save(self, commit=True):
        return_details = super().save(commit=False)
        return_details.is_returnable = return_details.return_policy != 'non_returnable'
        if commit:
            return_details.save()
            self.save_m2m()
        return return_details


class ReturnDetailsInline(admin.StackedInline):
    model = ReturnDetails
    form = ReturnDetailsInlineForm
    extra = 1
    max_num = 1


def restock_selected(modeladmin, request, queryset):
    updated_count = 0
    for product in queryset:
        product.stock_quantity = max(product.stock_quantity, product.low_stock_threshold)
        product.save(update_fields=['stock_quantity'])
        updated_count += 1

    if request is not None:
        modeladmin.message_user(request, f'Restocked {updated_count} product(s) to the low-stock threshold.')

    return updated_count


restock_selected.short_description = 'Restock selected products'


class ProductAdmin(PaginatedModelAdmin):
    form = ProductAdminForm
    list_display = ['product_name', 'image_thumbnail', 'sku', 'brand_name', 'display_age_groups', 'display_tags', 'price', 'marked_price', 'discount_percentage', 'stock_quantity', 'low_stock_threshold', 'stock_status', 'bundle_eligible', 'show_in_catalog']
    list_filter = ['category', 'brand_name', 'newest_product', 'tags', 'bundle_eligible', 'show_in_catalog']
    search_fields = ['product_name', 'sku', 'brand_name', 'tags__name']
    inlines = [ProductImageAdmin, ReturnDetailsInline]
    actions = [restock_selected]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('product_images')

    @admin.display(description='Image')
    def image_thumbnail(self, obj):
        image = next(iter(obj.product_images.all()), None)
        if image and image.image_url:
            return format_html(
                '<img src="{}" width="60" height="60" style="object-fit: contain;" />',
                image.image_url.url,
            )
        return 'No image'

    def display_age_groups(self, obj):
        return ', '.join(age.name for age in obj.age_groups.all()) or 'All ages'
    display_age_groups.short_description = 'Age groups'

    def display_tags(self, obj):
        return ', '.join(tag.name for tag in obj.tags.all()) or 'No campaign tag'
    display_tags.short_description = 'Campaign tags'


class BundleOfferAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['products'].queryset = Product.objects.filter(bundle_eligible=True)

    class Meta:
        model = BundleOffer
        fields = '__all__'

    def clean_products(self):
        products = self.cleaned_data['products']
        max_products = BundleConfiguration.get_solo().max_products
        if products.filter(bundle_eligible=True).count() != products.count():
            raise forms.ValidationError('Only products marked as bundle eligible can be included.')
        if products.count() > max_products:
            raise forms.ValidationError(f'Choose no more than {max_products} products.')
        return products


@admin.register(BundleConfiguration)
class BundleConfigurationAdmin(FilterCollapsibleModelAdmin):
    fields = ['max_products']

    def has_add_permission(self, request):
        return not BundleConfiguration.objects.exists()


@admin.register(BundleOffer)
class BundleOfferAdmin(FilterCollapsibleModelAdmin):
    form = BundleOfferAdminForm
    list_display = ['title', 'discount_percentage', 'is_active', 'product_count']
    list_filter = ['is_active']
    search_fields = ['title', 'description']
    filter_horizontal = ['products']

    def product_count(self, obj):
        return obj.products.count()
    product_count.short_description = 'Products'


@admin.register(BundlePackagingOption)
class BundlePackagingOptionAdmin(FilterCollapsibleModelAdmin):
    list_display = ['name', 'price', 'is_active', 'sort_order', 'sample_preview']
    list_filter = ['is_active']
    search_fields = ['name', 'description']
    list_editable = ['price', 'is_active', 'sort_order']

    def sample_preview(self, obj):
        if obj.sample_image:
            return format_html(
                '<img src="{}" width="80" height="60" style="object-fit: cover;" />',
                obj.sample_image.url,
            )
        return 'No sample'
    sample_preview.short_description = 'Sample sheet'


class ProductImageStandaloneAdmin(FilterCollapsibleModelAdmin):
    form = ProductImageAdminForm
    list_display = ['product', 'image_thumbnail']
    readonly_fields = ['img_preview']

    def image_thumbnail(self, obj):
        if obj.image_url and hasattr(obj.image_url, 'url'):
            return format_html('<img src="{}" width="50" height="50" style="object-fit: cover;" />', obj.image_url.url)
        return "No Image"
    image_thumbnail.short_description = 'Thumbnail'


@admin.register(AgeGroup)
class AgeGroupAdmin(FilterCollapsibleModelAdmin):
    list_display = ['name']
    search_fields = ['name']


@admin.register(Tag)
class TagAdmin(FilterCollapsibleModelAdmin):
    list_display = ['name', 'slug']
    search_fields = ['name']


@admin.register(ColorVariant)
class ColorVariantAdmin(FilterCollapsibleModelAdmin):
    list_display = ['color_name', 'price']
    model = ColorVariant


@admin.register(SizeVariant)
class SizeVariantAdmin(FilterCollapsibleModelAdmin):
    list_display = ['size_name', 'price', 'order']
    model = SizeVariant


admin.site.register(Category, CategoryAdmin)
admin.site.register(Coupon)
admin.site.register(Product, ProductAdmin)
admin.site.register(ProductImage, ProductImageStandaloneAdmin)
admin.site.register(ProductReview)
admin.site.register(ReviewPhoto)
