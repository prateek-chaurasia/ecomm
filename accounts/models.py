
from django.db import models
from django.core.validators import MinValueValidator
from django.contrib.auth.models import User
from django.utils import timezone
from base.models import BaseModel
from products.models import (
    Product, ColorVariant, SizeVariant, Coupon, BundleOffer,
    BundlePackagingOption,
)
from home.models import ShippingAddress
from django.conf import settings
import os
import uuid
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
        return CartItem.objects.filter(cart__is_paid=False, cart__user=self.user).count()


class Cart(BaseModel):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="cart", null=True, blank=True)
    session_key = models.CharField(max_length=40, null=True, blank=True, unique=True)
    coupon = models.ForeignKey(
        Coupon, on_delete=models.SET_NULL, null=True, blank=True)
    is_paid = models.BooleanField(default=False)
    razorpay_order_id = models.CharField(max_length=100, null=True, blank=True)
    razorpay_payment_id = models.CharField(
        max_length=100, null=True, blank=True)
    razorpay_payment_signature = models.CharField(
        max_length=100, null=True, blank=True)

    def get_cart_total(self):
        cart_items = self.cart_items.all()
        total_price = 0

        for cart_item in cart_items:
            total_price += cart_item.get_product_price()

        total_price += sum(
            bundle_item.get_product_price()
            for bundle_item in self.bundle_items.all()
        )

        return total_price

    def get_cart_total_price_after_coupon(self):
        total = self.get_cart_total()

        if self.coupon and total >= self.coupon.minimum_amount:
            total -= self.coupon.discount_amount

        return total


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
    guest_name = models.CharField(max_length=200, blank=True)
    guest_email = models.EmailField(blank=True)
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

    order = models.OneToOneField(
        Order, on_delete=models.CASCADE, related_name='return_request')
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

    def __str__(self):
        return f'Return for {self.order.order_id}'

    @property
    def calculated_refund_amount(self):
        return max(self.order.grand_total - self.deduction_amount, 0)

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
            and self.deduction_amount > self.order.grand_total
        ):
            raise ValidationError({
                'deduction_amount': 'The deduction cannot exceed the order total.',
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

    class Meta:
        constraints = [models.UniqueConstraint(fields=['bundle_item', 'product'], name='unique_bundle_order_product')]
