from django.contrib import admin
from django import forms
from django.utils.html import format_html
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


class CategoryAdmin(admin.ModelAdmin):
    form = CategoryAdminForm
    list_display = ['category_name', 'image_preview']

    def image_preview(self, obj):
        if obj.category_image:
            return format_html('<img src="{}" width="50" height="50" style="object-fit: cover;" />', obj.category_image)
        return "No Image"
    image_preview.short_description = 'Preview'


class ProductImageAdminForm(forms.ModelForm):
    # image_file = forms.ImageField(
    #     required=False, help_text="Upload an image file (will be converted to URL)")

    class Meta:
        model = ProductImage
        fields = '__all__'
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


class ReturnDetailsInline(admin.StackedInline):
    model = ReturnDetails
    extra = 0
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


class ProductAdmin(admin.ModelAdmin):
    list_display = ['product_name', 'brand_name', 'display_age_groups', 'display_tags', 'price', 'marked_price', 'discount_percentage', 'stock_quantity', 'low_stock_threshold', 'stock_status', 'bundle_eligible', 'show_in_catalog']
    list_filter = ['category', 'brand_name', 'newest_product', 'tags', 'bundle_eligible', 'show_in_catalog']
    search_fields = ['product_name', 'brand_name', 'tags__name']
    inlines = [ProductImageAdmin, ReturnDetailsInline]
    actions = [restock_selected]

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
class BundleConfigurationAdmin(admin.ModelAdmin):
    fields = ['max_products']

    def has_add_permission(self, request):
        return not BundleConfiguration.objects.exists()


@admin.register(BundleOffer)
class BundleOfferAdmin(admin.ModelAdmin):
    form = BundleOfferAdminForm
    list_display = ['title', 'discount_percentage', 'is_active', 'product_count']
    list_filter = ['is_active']
    search_fields = ['title', 'description']
    filter_horizontal = ['products']

    def product_count(self, obj):
        return obj.products.count()
    product_count.short_description = 'Products'


@admin.register(BundlePackagingOption)
class BundlePackagingOptionAdmin(admin.ModelAdmin):
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


class ProductImageStandaloneAdmin(admin.ModelAdmin):
    form = ProductImageAdminForm
    list_display = ['product', 'image_thumbnail']
    readonly_fields = ['img_preview']

    def image_thumbnail(self, obj):
        if obj.image_url and hasattr(obj.image_url, 'url'):
            return format_html('<img src="{}" width="50" height="50" style="object-fit: cover;" />', obj.image_url.url)
        return "No Image"
    image_thumbnail.short_description = 'Thumbnail'


@admin.register(AgeGroup)
class AgeGroupAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    search_fields = ['name']


@admin.register(ColorVariant)
class ColorVariantAdmin(admin.ModelAdmin):
    list_display = ['color_name', 'price']
    model = ColorVariant


@admin.register(SizeVariant)
class SizeVariantAdmin(admin.ModelAdmin):
    list_display = ['size_name', 'price', 'order']
    model = SizeVariant


admin.site.register(Category, CategoryAdmin)
admin.site.register(Coupon)
admin.site.register(Product, ProductAdmin)
admin.site.register(ProductImage, ProductImageStandaloneAdmin)
admin.site.register(ProductReview)
