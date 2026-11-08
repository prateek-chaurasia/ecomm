from datetime import timedelta

from django.contrib import admin
from django import forms
from django.utils import timezone
from base.admin import FilterCollapsibleModelAdmin
from .models import (
    City, State, ShippingAddress, HomeBanner, MarqueeAnnouncement,
    DeliveryGuideline, PageVisit, ReturnRefundPolicy, ReplacePolicy, SiteTheme,
    NewsletterSubscriber,
)

# Register your models here.

admin.site.register(ShippingAddress)


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(FilterCollapsibleModelAdmin):
    list_display = (
        'email', 'is_subscribed', 'consented_at', 'confirmed_at', 'created_at',
    )
    list_filter = ('is_subscribed', 'confirmed_at', 'created_at')
    search_fields = ('email',)
    readonly_fields = (
        'email', 'consented_at', 'confirmed_at', 'unsubscribed_at',
        'confirmation_sent_at', 'confirmation_token', 'unsubscribe_token',
        'coupon', 'created_at',
    )
    fields = readonly_fields + ('is_subscribed',)
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False


@admin.register(SiteTheme)
class SiteThemeAdmin(FilterCollapsibleModelAdmin):
    list_display = ('name', 'is_active', 'starts_at', 'ends_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    ordering = ('name',)
    fields = (
        'name',
        'primary_color',
        'primary_text_color',
        'secondary_color',
        'accent_color',
        'page_background_color',
        'surface_color',
        'text_color',
        'footer_background_color',
        'footer_text_color',
        'is_active',
        'starts_at',
        'ends_at',
    )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name.endswith('_color'):
            kwargs['widget'] = forms.TextInput(attrs={'type': 'color'})
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(PageVisit)
class PageVisitAdmin(admin.ModelAdmin):
    list_display = ('visited_at', 'ip_address', 'country', 'region', 'city', 'path')
    list_filter = ('country', 'visited_at')
    search_fields = ('ip_address', 'country', 'region', 'city', 'path')
    date_hierarchy = 'visited_at'
    ordering = ('-visited_at',)

    def get_queryset(self, request):
        retention_cutoff = timezone.now() - timedelta(days=90)
        return super().get_queryset(request).filter(visited_at__gte=retention_cutoff)

    def has_view_permission(self, request, obj=None):
        return (
            request.user.is_active
            and request.user.is_staff
            and request.user.has_perm('home.view_pagevisit')
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
        'local_delivery_cutoff_time',
        'priority_delivery_fee',
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