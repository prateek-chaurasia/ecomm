from datetime import datetime, timedelta

from django.contrib import admin
from django import forms
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.conf import settings
import logging
import razorpay
from base.admin import FilterCollapsibleModelAdmin, PaginatedModelAdmin
from .models import (
    Profile, Cart, CartItem, Order, OrderItem, ServiceablePincode,
    ReturnRequest, ReturnShipmentImage, ReturnRequestImage, ReplacementReason,
)

logger = logging.getLogger(__name__)

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


class ProfileAdmin(FilterCollapsibleModelAdmin):
    form = ProfileAdminForm
    list_display = ['user', 'is_email_verified', 'image_preview']
    readonly_fields = ['image_display']

    def image_preview(self, obj):
        if obj.profile_image_file:
            return format_html(
                '<img src="{}" width="50" height="50" '
                'style="object-fit: cover; border-radius: 50%;" />',
                obj.profile_image_file.url,
            )
        if obj.profile_image:
            return format_html('<img src="{}" width="50" height="50" style="object-fit: cover; border-radius: 50%;" />', obj.profile_image)
        return "No Image"
    image_preview.short_description = 'Preview'

    def image_display(self, obj):
        if obj.profile_image_file:
            return format_html(
                '<img src="{}" width="200" height="200" '
                'style="object-fit: cover; border-radius: 10px;" />',
                obj.profile_image_file.url,
            )
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
            ('refund_failed', 'Refund failed'),
            ('return_requested', 'Return requested'),
        )

    def queryset(self, request, queryset):
        status_values = {
            'refund_pending': 'Refund pending',
            'refund_requested': 'Refund requested',
            'refund_initiated': 'Refund initiated',
            'refund_processed': 'Refund processed',
            'refund_failed': 'Refund failed',
            'return_requested': 'Return requested',
        }
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(
            payment_status=status_values.get(value, value))


@admin.register(ServiceablePincode)
class ServiceablePincodeAdmin(FilterCollapsibleModelAdmin):
    list_display = ('pincode', 'is_active', 'notes')
    list_filter = ('is_active',)
    list_editable = ('is_active',)
    search_fields = ('pincode', 'notes')
@admin.register(Order)
class OrderAdmin(PaginatedModelAdmin):
    list_display = (
        'order_id', 'product_thumbnails', 'customer_name', 'delivery_pincode', 'delivery_option',
        'status', 'additional_delivery_fee', 'is_priority_delivery',
        'priority_delivery_fee', 'payment_status',
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

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related(
            'order_items__product__product_images',
            'bundle_items__selected_products__product__product_images',
        )

    @admin.display(description='Products')
    def product_thumbnails(self, obj):
        products = {}
        for item in obj.order_items.all():
            if item.product_id:
                products.setdefault(item.product_id, item.product)
        for bundle_item in obj.bundle_items.all():
            for selected_product in bundle_item.selected_products.all():
                products.setdefault(selected_product.product_id, selected_product.product)

        thumbnails = []
        for product in products.values():
            image = next(
                (image for image in product.product_images.all() if image.image_url),
                None,
            )
            if image:
                thumbnails.append((image.thumbnail_url, product.product_name))

        if not thumbnails:
            return '—'

        visible_thumbnails = thumbnails[:4]
        images = format_html_join(
            '',
            '<img src="{}" alt="{}" title="{}" width="40" height="40" '
            'style="object-fit: cover; border-radius: 4px;" />',
            ((url, name, name) for url, name in visible_thumbnails),
        )
        extra_count = len(thumbnails) - len(visible_thumbnails)
        if extra_count:
            return format_html(
                '<span style="display: inline-flex; align-items: center; gap: 4px;">'
                '{}<span>+{}</span></span>',
                images,
                extra_count,
            )
        return format_html(
            '<span style="display: inline-flex; align-items: center; gap: 4px;">{}</span>',
            images,
        )

    @admin.display(description='Customer')
    def customer_name(self, obj):
        return obj.guest_name or (obj.user.get_username() if obj.user else 'Guest')


admin.site.register(OrderItem)


@admin.register(ReplacementReason)
class ReplacementReasonAdmin(FilterCollapsibleModelAdmin):
    list_display = ('name', 'is_active', 'sort_order')
    list_editable = ('is_active', 'sort_order')
    list_filter = ('is_active',)
    search_fields = ('name',)
    ordering = ('sort_order', 'name')


class ReturnShipmentImageInline(admin.TabularInline):
    model = ReturnShipmentImage
    extra = 0
    can_delete = False
    readonly_fields = ('image_preview', 'image')

    @admin.display(description='Shipment photo')
    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener">'
                '<img src="{}" width="160" style="object-fit: contain;" /></a>',
                obj.image.url,
                obj.image.url,
            )
        return 'No image'


class ReturnRequestImageInline(ReturnShipmentImageInline):
    model = ReturnRequestImage
    verbose_name = 'Request evidence photo'
    verbose_name_plural = 'Photos uploaded with the return/replacement request'


@admin.register(ReturnRequest)
class ReturnRequestAdmin(FilterCollapsibleModelAdmin):
    list_display = (
        'order', 'request_type', 'replacement_reason', 'status',
        'courier_partner_name', 'tracking_number',
        'replacement_tracking_number', 'item_condition', 'deduction_amount',
        'refund_amount', 'created_at',
    )
    list_filter = ('request_type', 'status', 'item_condition', 'created_at')
    search_fields = (
        'order__order_id', 'order__guest_email', 'order__user__username',
        'courier_partner_name', 'tracking_number',
    )
    readonly_fields = (
        'order', 'request_type', 'replacement_reason', 'reason',
        'courier_partner_name', 'tracking_number', 'refund_method',
        'refund_upi_id', 'refund_account_holder_name', 'refund_account_number',
        'refund_ifsc_code', 'shipped_at', 'received_at',
        'replacement_dispatched_at', 'calculated_refund_amount_display',
        'razorpay_refund_id',
        'refunded_at',
    )
    fields = (
        'order', 'request_type', 'replacement_reason', 'status', 'reason',
        'admin_note', 'courier_partner_name', 'tracking_number', 'shipped_at',
        'refund_method', 'refund_upi_id', 'refund_account_holder_name',
        'refund_account_number', 'refund_ifsc_code', 'received_at',
        'item_condition', 'deduction_amount', 'deduction_reason',
        'replacement_tracking_number', 'replacement_dispatched_at',
        'calculated_refund_amount_display', 'razorpay_refund_id',
        'refund_reference',
        'refunded_at',
    )
    inlines = (ReturnShipmentImageInline, ReturnRequestImageInline)
    actions = (
        'approve_returns', 'reject_returns', 'mark_returns_received',
        'start_replacement', 'dispatch_replacement', 'complete_replacement',
        'initiate_online_refunds', 'refresh_online_refunds',
        'mark_manual_refunds_processed',
    )

    @admin.display(description='Calculated refund amount')
    def calculated_refund_amount_display(self, obj):
        if not obj or obj.request_type != ReturnRequest.RequestType.RETURN:
            return 'Not applicable'
        return f'INR {obj.calculated_refund_amount:,.2f}'

    def save_model(self, request, obj, form, change):
        if obj.request_type == ReturnRequest.RequestType.RETURN:
            obj.refund_amount = obj.calculated_refund_amount
        if (
            obj.status == ReturnRequest.Status.REFUNDED
            and not obj.refunded_at
        ):
            obj.refunded_at = timezone.now()

        super().save_model(request, obj, form, change)

        order_payment_status = {
            ReturnRequest.Status.REFUND_PENDING: 'Refund pending',
            ReturnRequest.Status.REFUND_FAILED: 'Refund failed',
            ReturnRequest.Status.REFUNDED: 'Refund processed',
        }.get(obj.status)
        if order_payment_status and obj.order.payment_status != order_payment_status:
            obj.order.payment_status = order_payment_status
            obj.order.save(update_fields=['payment_status', 'updated_at'])

    @admin.action(description='Approve selected return/replacement requests')
    def approve_returns(self, request, queryset):
        from home.models import ReturnRefundPolicy

        needs_return_address = queryset.filter(
            request_type=ReturnRequest.RequestType.RETURN,
            status=ReturnRequest.Status.REQUESTED,
        ).exists()
        if (
            needs_return_address
            and not ReturnRefundPolicy.get_solo().return_shipping_address.strip()
        ):
            self.message_user(
                request,
                'Configure the return shipping address under Home > Return and refund policy before approving returns.',
                level='error',
            )
            return

        updated = 0
        for return_request in queryset.filter(status=ReturnRequest.Status.REQUESTED):
            return_request.status = ReturnRequest.Status.APPROVED
            return_request.save(update_fields=['status', 'updated_at'])
            updated += 1
        self.message_user(request, f'{updated} return request(s) approved.')

    @admin.action(description='Reject selected return requests')
    def reject_returns(self, request, queryset):
        updated = 0
        for return_request in queryset.filter(status=ReturnRequest.Status.REQUESTED):
            if not return_request.admin_note.strip():
                self.message_user(
                    request,
                    f'Add an admin note explaining why {return_request.order.order_id} is rejected before running this action.',
                    level='error',
                )
                continue
            return_request.status = ReturnRequest.Status.REJECTED
            return_request.save(update_fields=['status', 'updated_at'])
            updated += 1
        self.message_user(request, f'{updated} return request(s) rejected.')

    @admin.action(description='Mark selected shipped returns as received')
    def mark_returns_received(self, request, queryset):
        updated = 0
        for return_request in queryset.filter(
            request_type=ReturnRequest.RequestType.RETURN,
            status=ReturnRequest.Status.SHIPPED,
        ):
            return_request.status = ReturnRequest.Status.RECEIVED
            return_request.received_at = timezone.now()
            return_request.save(update_fields=[
                'status', 'received_at', 'updated_at',
            ])
            updated += 1
        self.message_user(request, f'{updated} return(s) marked received.')

    @admin.action(description='Start preparing selected approved replacements')
    def start_replacement(self, request, queryset):
        updated = 0
        for return_request in queryset.filter(
            request_type=ReturnRequest.RequestType.REPLACEMENT,
            status=ReturnRequest.Status.APPROVED,
        ):
            return_request.status = ReturnRequest.Status.REPLACEMENT_PREPARING
            return_request.save(update_fields=['status', 'updated_at'])
            updated += 1
        self.message_user(
            request, f'{updated} replacement(s) moved to preparation.')

    @admin.action(description='Dispatch selected prepared replacements')
    def dispatch_replacement(self, request, queryset):
        eligible = queryset.filter(
            request_type=ReturnRequest.RequestType.REPLACEMENT,
            status=ReturnRequest.Status.REPLACEMENT_PREPARING,
        )
        missing_tracking = list(
            eligible.filter(replacement_tracking_number='').values_list(
                'order__order_id', flat=True)
        )
        if missing_tracking:
            self.message_user(
                request,
                'Enter the replacement carrier tracking number for: '
                + ', '.join(missing_tracking),
                level='error',
            )
        updated = 0
        for return_request in eligible.exclude(replacement_tracking_number=''):
            return_request.status = ReturnRequest.Status.REPLACEMENT_DISPATCHED
            return_request.replacement_dispatched_at = timezone.now()
            return_request.save(update_fields=[
                'status', 'replacement_dispatched_at', 'updated_at',
            ])
            updated += 1
        self.message_user(request, f'{updated} replacement(s) dispatched.')

    @admin.action(description='Mark selected dispatched replacements complete')
    def complete_replacement(self, request, queryset):
        updated = 0
        for return_request in queryset.filter(
            request_type=ReturnRequest.RequestType.REPLACEMENT,
            status=ReturnRequest.Status.REPLACEMENT_DISPATCHED,
        ):
            return_request.status = ReturnRequest.Status.REPLACEMENT_COMPLETED
            return_request.save(update_fields=['status', 'updated_at'])
            updated += 1
        self.message_user(
            request, f'{updated} replacement(s) marked complete.')

    @admin.action(description='Initiate refunds to original Razorpay payments')
    def initiate_online_refunds(self, request, queryset):
        client = None
        if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
            client = razorpay.Client(
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        if client is None:
            self.message_user(
                request, 'Razorpay credentials are not configured.',
                level='error',
            )
            return

        succeeded = 0
        for return_request in queryset.filter(
            request_type=ReturnRequest.RequestType.RETURN,
            status__in=[
                ReturnRequest.Status.RECEIVED,
                ReturnRequest.Status.REFUND_FAILED,
            ],
        ):
            order = return_request.order
            validation_error = self._inspection_error(return_request)
            if validation_error:
                self.message_user(
                    request,
                    f'{order.order_id}: {validation_error}',
                    level='error',
                )
                continue
            refund_amount = return_request.calculated_refund_amount
            if order.payment_mode == 'Cash on Delivery' or not order.razorpay_payment_id:
                self.message_user(
                    request,
                    f'{order.order_id} has no Razorpay payment. Use the manual refund action for a COD order.',
                    level='warning',
                )
                continue
            if refund_amount <= 0:
                self.message_user(
                    request,
                    f'{order.order_id} has no positive refund amount after deductions.',
                    level='error',
                )
                continue
            try:
                refund = client.payment.refund(
                    order.razorpay_payment_id,
                    {
                        'amount': int(refund_amount * 100),
                        'notes': {
                            'return_request_id': str(return_request.pk),
                            'order_id': order.order_id,
                        },
                    },
                )
            except Exception:
                logger.exception(
                    'Razorpay return refund failed for order %s', order.order_id)
                self.message_user(
                    request,
                    f'Razorpay could not initiate the refund for {order.order_id}. Check payment status before retrying.',
                    level='error',
                )
                continue

            return_request.refund_amount = refund_amount
            return_request.razorpay_refund_id = refund.get('id', '')
            refund_status = refund.get('status')
            is_processed = refund_status == 'processed'
            is_failed = refund_status == 'failed'
            return_request.status = (
                ReturnRequest.Status.REFUNDED if is_processed
                else ReturnRequest.Status.REFUND_FAILED if is_failed
                else ReturnRequest.Status.REFUND_PENDING
            )
            if is_processed:
                return_request.refunded_at = timezone.now()
            return_request.save(update_fields=[
                'refund_amount', 'razorpay_refund_id', 'status',
                'refunded_at', 'updated_at',
            ])
            order.payment_status = (
                'Refund processed' if is_processed
                else 'Refund failed' if is_failed
                else 'Refund pending'
            )
            order.save(update_fields=['payment_status', 'updated_at'])
            succeeded += 1
        self.message_user(
            request, f'Refunds initiated or completed for {succeeded} return(s).')

    @admin.action(description='Refresh status of selected Razorpay refunds')
    def refresh_online_refunds(self, request, queryset):
        client = None
        if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
            client = razorpay.Client(
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        if client is None:
            self.message_user(
                request, 'Razorpay credentials are not configured.',
                level='error',
            )
            return

        updated = 0
        for return_request in queryset.filter(
            request_type=ReturnRequest.RequestType.RETURN,
            status__in=[
                ReturnRequest.Status.REFUND_PENDING,
                ReturnRequest.Status.REFUND_FAILED,
            ],
        ).exclude(razorpay_refund_id=''):
            try:
                refund = client.refund.fetch(return_request.razorpay_refund_id)
            except Exception:
                logger.exception(
                    'Unable to refresh Razorpay refund %s',
                    return_request.razorpay_refund_id,
                )
                self.message_user(
                    request,
                    f'Unable to refresh refund {return_request.razorpay_refund_id}.',
                    level='error',
                )
                continue
            if refund.get('status') == 'failed':
                return_request.status = ReturnRequest.Status.REFUND_FAILED
                return_request.save(update_fields=['status', 'updated_at'])
                order = return_request.order
                order.payment_status = 'Refund failed'
                order.save(update_fields=['payment_status', 'updated_at'])
                continue
            if refund.get('status') != 'processed':
                continue
            return_request.status = ReturnRequest.Status.REFUNDED
            return_request.refunded_at = timezone.now()
            return_request.save(update_fields=[
                'status', 'refunded_at', 'updated_at',
            ])
            order = return_request.order
            order.payment_status = 'Refund processed'
            order.save(update_fields=['payment_status', 'updated_at'])
            updated += 1
        self.message_user(
            request, f'{updated} refund(s) confirmed as processed.')

    @admin.action(description='Mark selected COD refunds as manually processed')
    def mark_manual_refunds_processed(self, request, queryset):
        updated = 0
        for return_request in queryset.filter(
            request_type=ReturnRequest.RequestType.RETURN,
            status=ReturnRequest.Status.RECEIVED,
            order__payment_mode='Cash on Delivery',
        ):
            validation_error = self._inspection_error(return_request)
            if validation_error:
                self.message_user(
                    request,
                    f'{return_request.order.order_id}: {validation_error}',
                    level='error',
                )
                continue
            if not return_request.refund_reference.strip():
                self.message_user(
                    request,
                    f'Enter a refund reference for {return_request.order.order_id} before processing.',
                    level='error',
                )
                continue
            if not self._has_refund_payment_details(return_request):
                self.message_user(
                    request,
                    f'Add valid UPI or bank account details for {return_request.order.order_id} before processing.',
                    level='error',
                )
                continue
            refund_amount = return_request.calculated_refund_amount
            if refund_amount <= 0:
                self.message_user(
                    request,
                    f'{return_request.order.order_id} has no positive refund amount after deductions.',
                    level='error',
                )
                continue
            return_request.refund_amount = refund_amount
            return_request.status = ReturnRequest.Status.REFUNDED
            return_request.refunded_at = timezone.now()
            return_request.save(update_fields=[
                'refund_amount', 'status', 'refunded_at', 'updated_at',
            ])
            order = return_request.order
            order.payment_status = 'Refund processed'
            order.save(update_fields=['payment_status', 'updated_at'])
            updated += 1
        self.message_user(
            request, f'{updated} manual refund(s) marked as processed.')

    @staticmethod
    def _has_refund_payment_details(return_request):
        if return_request.refund_method == ReturnRequest.RefundMethod.UPI:
            return bool(return_request.refund_upi_id.strip())
        if return_request.refund_method == ReturnRequest.RefundMethod.BANK_ACCOUNT:
            return all((
                return_request.refund_account_holder_name.strip(),
                return_request.refund_account_number.strip(),
                return_request.refund_ifsc_code.strip(),
            ))
        return False

    @staticmethod
    def _inspection_error(return_request):
        if return_request.item_condition == ReturnRequest.ItemCondition.NOT_REVIEWED:
            return 'Record the item condition before processing the refund.'
        if return_request.deduction_amount > return_request.order.grand_total:
            return 'The condition deduction cannot exceed the order total.'
        if return_request.deduction_amount and not return_request.deduction_reason.strip():
            return 'Enter a reason for the condition deduction.'
        return ''
