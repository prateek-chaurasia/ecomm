from django.contrib import admin
from .models import (
    ShippingAddress, HomeBanner, DeliveryGuideline, ReturnRefundPolicy,
)

# Register your models here.

admin.site.register(ShippingAddress)


@admin.register(DeliveryGuideline)
class DeliveryGuidelineAdmin(admin.ModelAdmin):
    fields = (
        'title',
        'operational_office_city',
        'operational_office_latitude',
        'operational_office_longitude',
        'free_delivery_radius_km',
        'delivery_charge_threshold',
        'delivery_fee',
        'express_delivery_enabled',
        'express_delivery_min_hours',
        'express_delivery_max_hours',
        'next_day_delivery_enabled',
        'summary',
        'details',
        'outside_area_notice',
        'fee_review_notice',
    )

    def has_add_permission(self, request):
        return not DeliveryGuideline.objects.exists()


@admin.register(ReturnRefundPolicy)
class ReturnRefundPolicyAdmin(admin.ModelAdmin):
    fields = (
        'return_title', 'return_summary', 'return_details',
        'refund_title', 'refund_summary', 'refund_details',
        'cancellation_window_hours', 'cancellation_policy',
    )

    def has_add_permission(self, request):
        return not ReturnRefundPolicy.objects.exists()


@admin.register(HomeBanner)
class HomeBannerAdmin(admin.ModelAdmin):
    list_display = ('title', 'is_active', 'starts_at', 'ends_at')
    list_filter = ('is_active',)
    search_fields = ('title', 'subtitle')
    ordering = ('-starts_at',)