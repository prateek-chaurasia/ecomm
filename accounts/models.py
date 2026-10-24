
from django.db import models
from django.core.validators import MinValueValidator
from django.contrib.auth.models import User
from django.utils import timezone
from base.models import BaseModel
from products.models import (
    Product, ColorVariant, SizeVariant, Coupon, BundleOffer,
    BundlePackagingOption, GSTConfiguration,
)
from home.models import ShippingAddress
from django.conf import settings
import os
import uuid
from decimal import Decimal, ROUND_HALF_UP
# Create your models here.


class Profile(BaseModel):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="profile")
    is_email_verified = models.BooleanField(default=False)
    email_token = models.CharField(max_length=100, null=True, blank=True)
    profile_image = models.URLField(max_length=500, blank=True, null=True)
    profile_image_file = models.ImageField(
        upload_to='profile/%Y/%m/%d/',
        blank=True,
        null=True,
    )
    bio = models.TextField(null=True, blank=True)
    shipping_address = models.ForeignKey(
        ShippingAddress, on_delete=models.CASCADE, related_name="shipping_address", null=True, blank=True)

    def __str__(self):
        return self.user.username

    def get_cart_count(self):
        return CartItem.objects.filter(
            cart__is_paid=False,
            cart__is_buy_now=False,
            cart__user=self.user,
        ).count()


class Cart(BaseModel):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="cart", null=True, blank=True)
    session_key = models.CharField(max_length=40, null=True, blank=True, unique=True)
    coupon = models.ForeignKey(
        Coupon, on_delete=models.SET_NULL, null=True, blank=True)
    is_paid = models.BooleanField(default=False)
    is_buy_now = models.BooleanField(default=False)
    razorpay_order_id = models.CharField(max_length=100, null=True, blank=True)
    razorpay_payment_id = models.CharField(
        max_length=100, null=True, blank=True)
    razorpay_payment_signature = models.CharField(
        max_length=100, null=True, blank=True)
    razorpay_delivery_option = models.CharField(max_length=20, blank=True)
    razorpay_priority_delivery = models.BooleanField(default=False)
    razorpay_priority_delivery_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
    )

    @staticmethod
    def _allocate_amount(amount, weights):
        amount = Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        weight_total = sum(weights, Decimal('0.00'))
        if not weights:
            return []
        if weight_total <= 0:
            weights = [Decimal('1.00')] * len(weights)
            weight_total = Decimal(len(weights))
        allocated = []
        remaining = amount
        for weight in weights[:-1]:
            share = (amount * weight / weight_total).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP,
            )
            allocated.append(share)
            remaining -= share
        allocated.append(remaining)
        return allocated

    def _get_product_lines(self):
        lines = [
            (
                Decimal(cart_item.get_product_price()),
                Decimal(cart_item.product.gst_rate or 0),
            )
            for cart_item in self.cart_items.select_related('product')
            if cart_item.product_id
        ]
        for bundle_item in self.bundle_items.all():
            products = list(bundle_item.products.all())
            if not products:
                continue
            amount = Decimal(bundle_item.get_product_price())
            allocations = self._allocate_amount(
                amount,
                [Decimal(product.price) for product in products],
            )
            lines.extend(
                (allocation, Decimal(product.gst_rate or 0))
                for product, allocation in zip(products, allocations)
            )
        return lines

    def _get_cart_total(self, apply_coupon):
        lines = self._get_product_lines()
        subtotal = sum((amount for amount, _ in lines), Decimal('0.00'))
        discount = Decimal('0.00')
        if (
            apply_coupon
            and self.coupon
            and self.coupon.is_available
        ):
            discount = self.coupon.calculate_discount(subtotal)

        line_discounts = self._allocate_amount(
            discount, [amount for amount, _ in lines],
        ) if lines else []
        discounted_amounts = [
            amount - line_discount
            for (amount, _), line_discount in zip(lines, line_discounts)
        ]
        prices_include_tax = GSTConfiguration.get_solo().prices_include_tax
        if prices_include_tax:
            return sum(discounted_amounts, Decimal('0.00'))

        total = Decimal('0.00')
        for amount, (_, rate) in zip(discounted_amounts, lines):
            tax = (amount * rate / Decimal('100')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP,
            )
            total += amount + tax
        return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def get_cart_total(self):
        return self._get_cart_total(apply_coupon=False)

    def get_cart_total_price_after_coupon(self):
        return self._get_cart_total(apply_coupon=True)


class CartItem(BaseModel):
    cart = models.ForeignKey(
        Cart, on_delete=models.CASCADE, related_name="cart_items")
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True)
    color_variant = models.ForeignKey(
        ColorVariant, on_delete=models.SET_NULL, null=True, blank=True)
    size_variant = models.ForeignKey(
        SizeVariant, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.IntegerField(default=1)
    gift_wrap = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                models.F('cart'),
                models.F('product'),
                models.functions.Coalesce(
                    'size_variant',
                    models.Value(
                        uuid.UUID(int=0), output_field=models.UUIDField()),
                ),
                models.functions.Coalesce(
                    'color_variant',
                    models.Value(
                        uuid.UUID(int=0), output_field=models.UUIDField()),
                ),
                name='unique_cart_product_size_color',
            ),
        ]

    def get_product_price(self):
        price = self.product.price * self.quantity

        if self.color_variant:
            price += self.color_variant.price

        if self.size_variant:
            price += self.size_variant.price_adjustment

        if self.gift_wrap and self.product.gift_wrap_available:
            price += self.product.gift_wrap_price * self.quantity

        return price

    def get_original_product_price(self):
        price = max(self.product.marked_price, self.product.price) * self.quantity
        if self.color_variant:
            price += self.color_variant.price
        if self.size_variant:
            price += self.size_variant.price_adjustment
        if self.gift_wrap and self.product.gift_wrap_available:
            price += self.product.gift_wrap_price * self.quantity
        return price


class BundleCartItem(BaseModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='bundle_items')
    bundle_offer = models.ForeignKey(BundleOffer, on_delete=models.PROTECT, null=True, blank=True, related_name='cart_items')
    packaging_option = models.ForeignKey(BundlePackagingOption, on_delete=models.PROTECT, null=True, blank=True)
    packaging_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    quantity = models.PositiveIntegerField(default=1)
    original_unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    products = models.ManyToManyField(Product, through='BundleCartProduct', related_name='bundle_cart_items')

    def get_product_price(self):
        return self.unit_price * self.quantity


class BundleCartProduct(BaseModel):
    bundle_item = models.ForeignKey(BundleCartItem, on_delete=models.CASCADE, related_name='selected_products')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['bundle_item', 'product'], name='unique_bundle_cart_product')]


class ServiceablePincode(BaseModel):
    pincode = models.CharField(max_length=10, unique=True)
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['pincode']

    def save(self, *args, **kwargs):
        self.pincode = self.pincode.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.pincode


class Order(BaseModel):
    class DeliveryOption(models.TextChoices):
        LOCAL_FREE = 'local_free', 'Free local delivery'
        EXPRESS = 'express', 'Express delivery'
        STANDARD = 'next_day', 'Standard delivery'

    class Status(models.TextChoices):
        PENDING_REVIEW = 'pending_review', 'Pending review'
        ACCEPTED = 'accepted', 'Order accepted'
        PACKING = 'packing', 'Being packed'
        DISPATCHED = 'dispatched', 'Dispatched'
        OUT_FOR_DELIVERY = 'out_for_delivery', 'Out for delivery'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="orders", null=True, blank=True)
    order_id = models.CharField(max_length=100, unique=True)
    guest_access_token = models.CharField(max_length=64, null=True, blank=True, unique=True)
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.ACCEPTED)
    delivery_pincode = models.CharField(max_length=10, blank=True)
    outside_service_area = models.BooleanField(default=False)
    delivery_option = models.CharField(
        max_length=20,
        choices=DeliveryOption.choices,
        blank=True,
        default='',
    )
    delivery_distance_km = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    additional_delivery_fee = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Delivery charge applied when the order is below the free-delivery threshold.',
    )
    is_priority_delivery = models.BooleanField(default=False)
    priority_delivery_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
    )
    local_delivery_window_promised = models.BooleanField(default=False)
    guest_name = models.CharField(max_length=200, blank=True)
    guest_email = models.EmailField(blank=True)
    prices_include_tax = models.BooleanField(default=True)
    seller_name = models.CharField(max_length=150, blank=True)
    seller_address = models.TextField(blank=True)
    seller_gstin = models.CharField(max_length=15, blank=True)
    seller_email = models.EmailField(blank=True)
    seller_state = models.CharField(max_length=100, blank=True)
    seller_state_code = models.CharField(max_length=2, blank=True)
    place_of_supply_state = models.CharField(max_length=100, blank=True)
    order_date = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    payment_status = models.CharField(max_length=100)
    razorpay_payment_id = models.CharField(max_length=100, blank=True)
    shipping_address = models.TextField(blank=True, null=True)
    payment_mode = models.CharField(max_length=100)
    order_total_price = models.DecimalField(max_digits=10, decimal_places=2)
    coupon = models.ForeignKey(
        Coupon, on_delete=models.SET_NULL, null=True, blank=True)
    grand_total = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        indexes = [
            models.Index(fields=['order_date'], name='order_date_idx'),
            models.Index(fields=['status', 'order_date'], name='order_status_date_idx'),
            models.Index(fields=['payment_status'], name='order_payment_status_idx'),
            models.Index(fields=['payment_mode'], name='order_payment_mode_idx'),
        ]

    def __str__(self):
        owner = self.user.username if self.user else 'Guest'
        return f"Order {self.order_id} by {owner}"

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if self.status == self.Status.DELIVERED and not self.delivered_at:
            self.delivered_at = timezone.now()
            if update_fields is not None:
                kwargs['update_fields'] = set(update_fields) | {'delivered_at'}
        super().save(*args, **kwargs)

    def get_order_total_price(self):
        return self.order_total_price

    @property
    def delivery_promise(self):
        if self.delivery_option == self.DeliveryOption.STANDARD:
            if self.is_priority_delivery:
                return 'Order will be delivered next day.'
            return 'Most orders will arrive in 2–3 business days.'
        if self.delivery_option == self.DeliveryOption.LOCAL_FREE:
            if self.local_delivery_window_promised:
                return 'Order will be delivered within 4–5 hours.'
            return 'Delivery timing will be confirmed after order placement.'
        return ''

    @property
    def can_be_cancelled(self):
        cancellable_statuses = {
            self.Status.PENDING_REVIEW,
            self.Status.ACCEPTED,
            self.Status.PACKING,
        }
        return self.status in cancellable_statuses

    @property
    def status_index(self):
        statuses = [
            self.Status.ACCEPTED,
            self.Status.PACKING,
            self.Status.DISPATCHED,
            self.Status.OUT_FOR_DELIVERY,
            self.Status.DELIVERED,
        ]
        return statuses.index(self.status) if self.status in statuses else -1


class OrderItem(BaseModel):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="order_items")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    size_variant = models.ForeignKey(
        SizeVariant, on_delete=models.SET_NULL, null=True, blank=True)
    color_variant = models.ForeignKey(
        ColorVariant, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    product_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True)
    original_product_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True)
    hsn_code = models.CharField(max_length=32, blank=True)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    gift_wrap = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.product.product_name} - {self.quantity}"

    def get_total_price(self):
        # Use the get_product_price method from CartItem
        cart_item = CartItem(
            product=self.product,
            size_variant=self.size_variant,
            color_variant=self.color_variant,
            quantity=self.quantity,
            gift_wrap=self.gift_wrap,
        )
        return cart_item.get_product_price()


class ReturnRequest(BaseModel):
    class RequestType(models.TextChoices):
        RETURN = 'return', 'Return and refund'
        REPLACEMENT = 'replacement', 'Replacement'

    class RefundMethod(models.TextChoices):
        UPI = 'upi', 'UPI ID'
        BANK_ACCOUNT = 'bank_account', 'Bank account'

    class Status(models.TextChoices):
        REQUESTED = 'requested', 'Requested'
        APPROVED = 'approved', 'Approved'
        SHIPPED = 'shipped', 'Shipped by customer'
        RECEIVED = 'received', 'Received - awaiting inspection'
        REFUND_PENDING = 'refund_pending', 'Refund initiated'
        REFUND_FAILED = 'refund_failed', 'Refund failed'
        REPLACEMENT_PREPARING = 'replacement_preparing', 'Replacement being prepared'
        REPLACEMENT_DISPATCHED = 'replacement_dispatched', 'Replacement dispatched'
        REPLACEMENT_COMPLETED = 'replacement_completed', 'Replacement completed'
        REJECTED = 'rejected', 'Rejected'
        REFUNDED = 'refunded', 'Refund processed'

    class ItemCondition(models.TextChoices):
        NOT_REVIEWED = 'not_reviewed', 'Not reviewed'
        GOOD = 'good', 'Good'
        MINOR_DAMAGE = 'minor_damage', 'Minor damage'
        DAMAGED = 'damaged', 'Damaged'

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='return_requests')
    order_item = models.ForeignKey(
        'accounts.OrderItem',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='return_requests',
    )
    bundle_order_product = models.ForeignKey(
        'accounts.BundleOrderProduct',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='return_requests',
    )
    request_type = models.CharField(
        max_length=20, choices=RequestType.choices, default=RequestType.RETURN)
    replacement_reason = models.ForeignKey(
        'accounts.ReplacementReason',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='requests',
    )
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.REQUESTED)
    reason = models.TextField()
    admin_note = models.TextField(blank=True)
    courier_partner_name = models.CharField(max_length=120, blank=True)
    tracking_number = models.CharField(max_length=120, blank=True)
    refund_method = models.CharField(
        max_length=20, choices=RefundMethod.choices, blank=True)
    refund_upi_id = models.CharField(max_length=120, blank=True)
    refund_account_holder_name = models.CharField(max_length=150, blank=True)
    refund_account_number = models.CharField(max_length=34, blank=True)
    refund_ifsc_code = models.CharField(max_length=11, blank=True)
    replacement_tracking_number = models.CharField(max_length=120, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    replacement_dispatched_at = models.DateTimeField(null=True, blank=True)
    item_condition = models.CharField(
        max_length=20,
        choices=ItemCondition.choices,
        default=ItemCondition.NOT_REVIEWED,
    )
    deduction_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )
    deduction_reason = models.TextField(blank=True)
    refund_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True)
    razorpay_refund_id = models.CharField(max_length=100, blank=True)
    refund_reference = models.CharField(max_length=120, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=~(
                    models.Q(order_item__isnull=False)
                    & models.Q(bundle_order_product__isnull=False)
                ),
                name='return_request_single_order_line',
            ),
            models.UniqueConstraint(
                fields=['order_item'],
                condition=(
                    models.Q(order_item__isnull=False)
                    & ~models.Q(status='rejected')
                ),
                name='unique_active_order_item_return_request',
            ),
            models.UniqueConstraint(
                fields=['bundle_order_product'],
                condition=(
                    models.Q(bundle_order_product__isnull=False)
                    & ~models.Q(status='rejected')
                ),
                name='unique_active_bundle_product_return_request',
            ),
        ]

    def __str__(self):
        return f'Return for {self.order.order_id}'

    @property
    def requested_item_name(self):
        if self.order_item_id:
            return (
                self.order_item.product.product_name
                if self.order_item.product_id else 'Product no longer available'
            )
        if self.bundle_order_product_id:
            return self.bundle_order_product.product.product_name
        return 'Order items'

    @property
    def calculated_refund_amount_before_deduction(self):
        line_amount = self.requested_line_amount
        if line_amount is None:
            return self.order.grand_total
        else:
            order_line_total = sum(
                (
                    amount or Decimal('0.00')
                    for amount in OrderItem.objects.filter(
                        order_id=self.order_id,
                    ).values_list('product_price', flat=True)
                ),
                Decimal('0.00'),
            )
            order_line_total += sum(
                (
                    item.product_price * item.bundle_item.quantity
                    for item in BundleOrderProduct.objects.filter(
                        bundle_item__order_id=self.order_id,
                    ).select_related('bundle_item')
                ),
                Decimal('0.00'),
            )
            refund_basis = (
                self.order.grand_total * line_amount / order_line_total
                if order_line_total else line_amount
            ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return refund_basis

    @property
    def calculated_refund_amount(self):
        return max(
            self.calculated_refund_amount_before_deduction - self.deduction_amount,
            Decimal('0.00'),
        )

    @property
    def requested_line_amount(self):
        if self.order_item_id:
            return self.order_item.product_price
        if self.bundle_order_product_id:
            return (
                self.bundle_order_product.product_price
                * self.bundle_order_product.bundle_item.quantity
            )
        return None

    def clean(self):
        from django.core.exceptions import ValidationError

        if (
            self.request_type == self.RequestType.REPLACEMENT
            and not self.replacement_reason_id
        ):
            raise ValidationError({
                'replacement_reason': 'Choose a reason for the replacement.',
            })
        if (
            self.order_id
            and self.deduction_amount
            > self.calculated_refund_amount_before_deduction
        ):
            raise ValidationError({
                'deduction_amount': 'The deduction cannot exceed the item refund amount.',
            })
        if (
            self.order_item_id
            and self.order_item.order_id != self.order_id
        ):
            raise ValidationError({
                'order_item': 'Choose an item from this order.',
            })
        if (
            self.bundle_order_product_id
            and self.bundle_order_product.bundle_item.order_id != self.order_id
        ):
            raise ValidationError({
                'bundle_order_product': 'Choose an item from this order.',
            })
        if self.deduction_amount and not self.deduction_reason.strip():
            raise ValidationError({
                'deduction_reason': (
                    'Enter a reason for the condition deduction.'
                ),
            })


class ReplacementReason(BaseModel):
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class ReturnShipmentImage(BaseModel):
    return_request = models.ForeignKey(
        ReturnRequest, on_delete=models.CASCADE, related_name='shipment_images')
    image = models.ImageField(upload_to='returns/%Y/%m/%d/')


class ReturnRequestImage(BaseModel):
    return_request = models.ForeignKey(
        ReturnRequest, on_delete=models.CASCADE, related_name='request_images')
    image = models.ImageField(upload_to='returns/request-evidence/%Y/%m/%d/')


class BundleOrderItem(BaseModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='bundle_items')
    bundle_offer = models.ForeignKey(BundleOffer, on_delete=models.PROTECT, null=True, blank=True)
    packaging_option = models.ForeignKey(BundlePackagingOption, on_delete=models.PROTECT, null=True, blank=True)
    packaging_name = models.CharField(max_length=100, blank=True)
    packaging_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    title = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1)
    original_unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    products = models.ManyToManyField(Product, through='BundleOrderProduct', related_name='bundle_order_items')

    def get_total_price(self):
        return self.unit_price * self.quantity


class BundleOrderProduct(BaseModel):
    bundle_item = models.ForeignKey(BundleOrderItem, on_delete=models.CASCADE, related_name='selected_products')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    product_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    original_product_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    hsn_code = models.CharField(max_length=32, blank=True)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        constraints = [models.UniqueConstraint(fields=['bundle_item', 'product'], name='unique_bundle_order_product')]
