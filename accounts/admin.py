from datetime import datetime, timedelta

from django.contrib import admin
from django import forms
from django.utils import timezone
from django.utils.html import format_html
from .models import Profile, Cart, CartItem, Order, OrderItem, ServiceablePincode

# Register your models here.


class ProfileAdminForm(forms.ModelForm):
    image_file = forms.ImageField(
        required=False, help_text="Upload an image file (will be converted to URL)")

    class Meta:
        model = Profile
        fields = '__all__'
        widgets = {
            'profile_image': forms.URLInput(attrs={
                'placeholder': 'Enter image URL or upload a file below',
                'style': 'width: 100%;'
            })
        }


class ProfileAdmin(admin.ModelAdmin):
    form = ProfileAdminForm
    list_display = ['user', 'is_email_verified', 'image_preview']
    readonly_fields = ['image_display']

    def image_preview(self, obj):
        if obj.profile_image:
            return format_html('<img src="{}" width="50" height="50" style="object-fit: cover; border-radius: 50%;" />', obj.profile_image)
        return "No Image"
    image_preview.short_description = 'Preview'

    def image_display(self, obj):
        if obj.profile_image:
            return format_html('<img src="{}" width="200" height="200" style="object-fit: cover; border-radius: 10px;" />', obj.profile_image)
        return "No Image"
    image_display.short_description = 'Profile Image'


admin.site.register(Profile, ProfileAdmin)
admin.site.register(Cart)
admin.site.register(CartItem)


class OrderDateFilter(admin.SimpleListFilter):
    title = 'order date'
    parameter_name = 'period'
    template = 'admin/accounts/order/date_filter.html'

    def __init__(self, request, params, model, model_admin):
        self.request = request
        super().__init__(request, params, model, model_admin)
        for parameter in ('date_from', 'date_to'):
            if parameter in params:
                self.used_parameters[parameter] = params.pop(parameter)[-1]

    def lookups(self, request, model_admin):
        return (
            ('today', 'Today'),
            ('this_week', 'This week'),
            ('this_month', 'This month'),
        )

    def expected_parameters(self):
        return [self.parameter_name, 'date_from', 'date_to']

    def queryset(self, request, queryset):
        period = self.value()
        today = timezone.localdate()
        if period == 'today':
            return self._filter_date_range(queryset, today, today)
        if period == 'this_week':
            week_start = today - timedelta(days=today.weekday())
            return self._filter_date_range(queryset, week_start, today)
        if period == 'this_month':
            next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
            return queryset.filter(
                order_date__gte=self._local_midnight(today),
                order_date__lt=self._local_midnight(next_month),
            )

        from_date = self._parse_date(self.used_parameters.get('date_from'))
        to_date = self._parse_date(self.used_parameters.get('date_to'))
        if from_date and to_date and from_date > to_date:
            return queryset.none()
        if not from_date and not to_date:
            return queryset
        return self._filter_date_range(queryset, from_date, to_date)

    @staticmethod
    def _local_midnight(value):
        return timezone.make_aware(datetime.combine(value, datetime.min.time()))

    @classmethod
    def _filter_date_range(cls, queryset, from_date, to_date):
        if from_date:
            queryset = queryset.filter(
                order_date__gte=cls._local_midnight(from_date))
        if to_date:
            queryset = queryset.filter(
                order_date__lt=cls._local_midnight(to_date + timedelta(days=1)))
        return queryset

    @staticmethod
    def _parse_date(value):
        if not value:
            return None
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            return None


class OrderStatusFilter(admin.SimpleListFilter):
    title = 'order status'
    parameter_name = 'order_status'

    def lookups(self, request, model_admin):
        return (
            ('placed', 'Order placed / pending review'),
            (Order.Status.CANCELLED, 'Cancelled'),
            (Order.Status.ACCEPTED, 'Order accepted'),
            (Order.Status.PACKING, 'Being packed'),
            (Order.Status.DISPATCHED, 'Dispatched'),
            (Order.Status.OUT_FOR_DELIVERY, 'Out for delivery'),
            (Order.Status.DELIVERED, 'Delivered'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'placed':
            return queryset.filter(status=Order.Status.PENDING_REVIEW)
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


class PaymentTypeFilter(admin.SimpleListFilter):
    title = 'payment type'
    parameter_name = 'payment_type'

    def lookups(self, request, model_admin):
        return (
            ('online', 'Paid online'),
            ('cod', 'Cash on delivery'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'online':
            return queryset.exclude(
                payment_mode='Cash on Delivery',
            ).filter(payment_status='Paid')
        if self.value() == 'cod':
            return queryset.filter(payment_mode='Cash on Delivery')
        return queryset


class PaymentStatusFilter(admin.SimpleListFilter):
    title = 'current payment / return status'
    parameter_name = 'financial_status'

    def lookups(self, request, model_admin):
        return (
            ('pending', 'Payment pending'),
            ('paid', 'Paid'),
            ('refund_pending', 'Refund pending'),
            ('refund_requested', 'Refund requested'),
            ('refund_initiated', 'Refund initiated'),
            ('refund_processed', 'Refund processed'),
            ('return_requested', 'Return requested'),
        )

    def queryset(self, request, queryset):
        status_values = {
            'refund_pending': 'Refund pending',
            'refund_requested': 'Refund requested',
            'refund_initiated': 'Refund initiated',
            'refund_processed': 'Refund processed',
            'return_requested': 'Return requested',
        }
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(
            payment_status=status_values.get(value, value))


@admin.register(ServiceablePincode)
class ServiceablePincodeAdmin(admin.ModelAdmin):
    list_display = ('pincode', 'is_active', 'notes')
    list_filter = ('is_active',)
    list_editable = ('is_active',)
    search_fields = ('pincode', 'notes')
@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_id', 'customer_name', 'delivery_pincode', 'delivery_option',
        'status', 'additional_delivery_fee', 'payment_status',
        'grand_total', 'order_date',
    )
    ordering = ('-order_date',)
    date_hierarchy = 'order_date'
    list_filter = (
        OrderDateFilter,
        OrderStatusFilter,
        PaymentTypeFilter,
        PaymentStatusFilter,
        'outside_service_area',
    )
    list_editable = ('status', 'additional_delivery_fee')
    search_fields = ('order_id', 'guest_email', 'guest_name', 'user__username')
    readonly_fields = ('order_id', 'guest_access_token', 'order_date', 'delivery_pincode')

    @admin.display(description='Customer')
    def customer_name(self, obj):
        return obj.guest_name or (obj.user.get_username() if obj.user else 'Guest')


admin.site.register(OrderItem)
