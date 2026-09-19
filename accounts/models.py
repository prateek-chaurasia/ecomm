
from django.db import models
from django.contrib.auth.models import User
from base.models import BaseModel
from products.models import (
    Product, ColorVariant, SizeVariant, Coupon, BundleOffer,
    BundlePackagingOption,
)
from home.models import ShippingAddress
from django.conf import settings
import os
# Create your models here.


class Profile(BaseModel):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="profile")
    is_email_verified = models.BooleanField(default=False)
    email_token = models.CharField(max_length=100, null=True, blank=True)
    profile_image = models.URLField(max_length=500, blank=True, null=True)
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

    def get_product_price(self):
        price = self.product.price * self.quantity

        if self.color_variant:
            price += self.color_variant.price

        if self.size_variant:
            price += self.size_variant.price

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
    additional_delivery_fee = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Set this during review for non-serviceable pincodes.',
    )
    guest_name = models.CharField(max_length=200, blank=True)
    guest_email = models.EmailField(blank=True)
    order_date = models.DateTimeField(auto_now_add=True)
    payment_status = models.CharField(max_length=100)
    shipping_address = models.TextField(blank=True, null=True)
    payment_mode = models.CharField(max_length=100)
    order_total_price = models.DecimalField(max_digits=10, decimal_places=2)
    coupon = models.ForeignKey(
        Coupon, on_delete=models.SET_NULL, null=True, blank=True)
    grand_total = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        owner = self.user.username if self.user else 'Guest'
        return f"Order {self.order_id} by {owner}"

    def get_order_total_price(self):
        return self.order_total_price

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
