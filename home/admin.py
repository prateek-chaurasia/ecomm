from django.contrib import admin
from .models import ShippingAddress, HomeBanner

# Register your models here.

admin.site.register(ShippingAddress)


@admin.register(HomeBanner)
class HomeBannerAdmin(admin.ModelAdmin):
    list_display = ('title', 'is_active', 'starts_at', 'ends_at')
    list_filter = ('is_active',)
    search_fields = ('title', 'subtitle')
    ordering = ('-starts_at',)