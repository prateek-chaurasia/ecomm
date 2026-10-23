from django.contrib import admin
from django import forms
from base.admin import FilterCollapsibleModelAdmin
from .models import (
    City, State, ShippingAddress, HomeBanner, MarqueeAnnouncement,
    DeliveryGuideline, ReturnRefundPolicy, ReplacePolicy,
)

# Register your models here.

admin.site.register(ShippingAddress)


@admin.register(State)
class StateAdmin(FilterCollapsibleModelAdmin):
    list_display = ('name', 'country')
    list_filter = ('country',)
    search_fields = ('name', 'country__name', 'country__code')


@admin.register(City)
class CityAdmin(FilterCollapsibleModelAdmin):
    list_display = ('name', 'state', 'normalized_name')
    list_filter = ('state__country', 'state')
    search_fields = ('name', 'state__name')


@admin.register(DeliveryGuideline)
class DeliveryGuidelineAdmin(FilterCollapsibleModelAdmin):
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


class ReturnRefundPolicyAdminForm(forms.ModelForm):
    class Meta:
        model = ReturnRefundPolicy
        fields = '__all__'
        help_texts = {
            'return_summary': 'HTML formatting is supported; unsafe tags and attributes are removed when displayed.',
            'return_details': 'HTML formatting is supported; unsafe tags and attributes are removed when displayed.',
            'refund_summary': 'HTML formatting is supported; unsafe tags and attributes are removed when displayed.',
            'refund_details': 'HTML formatting is supported; unsafe tags and attributes are removed when displayed.',
        }


@admin.register(ReturnRefundPolicy)
class ReturnRefundPolicyAdmin(FilterCollapsibleModelAdmin):
    form = ReturnRefundPolicyAdminForm
    fields = (
        'return_title', 'return_summary', 'return_details',
        'refund_title', 'refund_summary', 'refund_details',
        'return_shipping_address',
        'cancellation_policy',
    )

    def has_add_permission(self, request):
        return not ReturnRefundPolicy.objects.exists()


class ReplacePolicyAdminForm(forms.ModelForm):
    class Meta:
        model = ReplacePolicy
        fields = '__all__'
        help_texts = {
            'replace_summary': 'HTML formatting is supported; unsafe tags and attributes are removed when displayed.',
            'replace_details': 'HTML formatting is supported; unsafe tags and attributes are removed when displayed.',
        }


@admin.register(ReplacePolicy)
class ReplacePolicyAdmin(FilterCollapsibleModelAdmin):
    form = ReplacePolicyAdminForm
    fields = ('replace_summary', 'replace_details')

    def has_add_permission(self, request):
        return not ReplacePolicy.objects.exists()


class HomeBannerAdminForm(forms.ModelForm):
    class Meta:
        model = HomeBanner
        fields = '__all__'
        widgets = {
            'image': forms.ClearableFileInput(
                attrs={'accept': '.avif,.jpg,.jpeg,.png,.webp'}
            ),
        }


@admin.register(HomeBanner)
class HomeBannerAdmin(FilterCollapsibleModelAdmin):
    form = HomeBannerAdminForm
    list_display = ('title', 'is_active', 'starts_at', 'ends_at')
    list_filter = ('is_active',)
    search_fields = ('title', 'subtitle')
    ordering = ('-starts_at',)


@admin.register(MarqueeAnnouncement)
class MarqueeAnnouncementAdmin(FilterCollapsibleModelAdmin):
    list_display = ('text', 'is_active', 'display_order', 'updated_at')
    list_editable = ('is_active', 'display_order')
    list_filter = ('is_active',)
    search_fields = ('text',)
    ordering = ('display_order', 'created_at')