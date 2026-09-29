from datetime import timedelta

from django.contrib.auth.models import User
from base.models import BaseModel
from django.urls import reverse
from django.db import models
from django import forms
from django.core.exceptions import ValidationError
from django_countries.fields import CountryField
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.utils import timezone
import re

# Create your models here.


class HomeBanner(BaseModel):
    title = models.CharField(max_length=150)
    subtitle = models.CharField(max_length=250, blank=True)
    image = models.ImageField(upload_to='banners/%Y/%m/%d/', blank=True, null=True)
    button_text = models.CharField(max_length=50, default='Shop Now')
    link_url = models.URLField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-starts_at']

    def __str__(self):
        return self.title

    @property
    def is_live(self):
        if not self.is_active:
            return False
        now = timezone.now()
        if self.ends_at is not None:
            return self.starts_at <= now <= self.ends_at
        return self.starts_at <= now


class DeliveryGuideline(models.Model):
    title = models.CharField(max_length=120, default='Delivery information')
    operational_office_city = models.CharField(max_length=100, blank=True)
    operational_office_latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    operational_office_longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    free_delivery_radius_km = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=5,
        validators=[MinValueValidator(0.01)],
        help_text='Free local delivery radius, provided the customer city also matches.',
    )
    delivery_charge_threshold = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=500,
        validators=[MinValueValidator(0)],
        help_text='Minimum post-discount order total for free delivery (configurable).',
    )
    delivery_fee = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=80,
        validators=[MinValueValidator(0)],
        help_text='Delivery charge applied when the order is below the free-delivery threshold.',
    )
    express_delivery_enabled = models.BooleanField(default=True)
    express_delivery_min_hours = models.PositiveSmallIntegerField(default=4)
    express_delivery_max_hours = models.PositiveSmallIntegerField(default=5)
    next_day_delivery_enabled = models.BooleanField(default=True)
    summary = models.TextField(
        default='Delivery availability and timelines depend on your location and order details.')
    details = models.TextField(
        default=(
            'Delivery timelines depend on your delivery area, order time, product availability, '
            'and weather. Orders to PIN codes outside the Standard Service Area are estimated '
            'to arrive within 4–6 days.'
        ),
    )
    outside_area_notice = models.TextField(
        default='Orders to PIN codes outside the Standard Service Area are estimated to arrive within 4–6 days.')
    fee_review_notice = models.TextField(
        default=(
            'Any additional delivery fee will be decided by our team during review and shared '
            'with you before acceptance.'
        ),
    )

    class Meta:
        verbose_name = 'delivery guideline'
        verbose_name_plural = 'delivery guideline'

    @classmethod
    def get_solo(cls):
        guideline, _ = cls.objects.get_or_create(pk=1)
        return guideline

    def save(self, *args, **kwargs):
        self.pk = 1
        kwargs.pop('force_insert', None)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if (
            self.operational_office_latitude is None
        ) != (
            self.operational_office_longitude is None
        ):
            raise ValidationError(
                'Set both office coordinates or leave both blank.'
            )
        if (
            self.express_delivery_enabled
            and self.express_delivery_min_hours >= self.express_delivery_max_hours
        ):
            raise ValidationError(
                {'express_delivery_max_hours': 'Must be greater than the minimum hours.'}
            )
        if not self.express_delivery_enabled and not self.next_day_delivery_enabled:
            raise ValidationError('At least one non-local delivery option must remain enabled.')


class ReturnRefundPolicy(models.Model):
    return_title = models.CharField(max_length=120, default='Returns')
    return_summary = models.TextField(
        default='Please inspect your order when it is delivered and contact us if there is an issue.')
    return_details = models.TextField(
        default=(
            'Product-specific return or replacement eligibility, time limits, and conditions '
            'are shown on each product page and apply to that item.'
        ),
    )
    refund_title = models.CharField(max_length=120, default='Refunds')
    refund_summary = models.TextField(
        default='Approved refunds are issued to the original payment method where possible.')
    refund_details = models.TextField(
        default='Contact our support team with your order details to request help with a refund.')
    cancellation_window_hours = models.PositiveSmallIntegerField(
        default=24,
        validators=[MinValueValidator(1)],
        help_text='Number of hours after placement when customers may cancel an order.',
    )
    cancellation_policy = models.TextField(
        default=(
            'Contact our support team as soon as possible to request an order cancellation.'
        ),
    )

    class Meta:
        verbose_name = 'return and refund policy'
        verbose_name_plural = 'return and refund policy'

    @classmethod
    def get_solo(cls):
        policy, _ = cls.objects.get_or_create(pk=1)
        return policy

    def save(self, *args, **kwargs):
        self.pk = 1
        kwargs.pop('force_insert', None)
        super().save(*args, **kwargs)

    def __str__(self):
        return 'Return and refund policy'


class CountryModel(models.Model):
    # You can still use CountryField for choices/flags on the parent table
    code = CountryField(unique=True, primary_key=True)
    name = models.CharField(max_length=100)
    
    def __str__(self):
        return f"{self.code} -- {self.name}"


class State(models.Model):
    country = models.ForeignKey(CountryModel, on_delete=models.CASCADE, related_name='states')
    name = models.CharField(max_length=100)
    
    class Meta:
        unique_together = ('country', 'name')
        verbose_name = 'State'
        verbose_name_plural = 'States'
        
    def __str__(self):
        return f'{self.name}'


class ShippingAddress(BaseModel):
    # 1. Define the validator
    alphanumeric_username = RegexValidator(
        regex=r'^[a-zA-Z0-9_]+$',
        message='Username must be alphanumeric or contain underscores.',
        code='invalid_username'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    street = models.CharField(max_length=100)
    street_number = models.CharField(max_length=100)
    zip_code = models.CharField(
                max_length=6,
                validators=[
                    RegexValidator(
                        regex=r'^[1-9][0-9]{5}$',
                        message='Enter a valid 6-digit Indian PIN code.'
                    )
                ]
            )
    city = models.CharField(max_length=50)
    country = CountryField()
    state = models.ForeignKey(State, on_delete=models.PROTECT)
    phone = models.CharField(max_length=30)
    current_address = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    
    def __str__(self):
        return f'{self.street}, {self.street_number}, {self.city}, {self.state}, {self.country}, {self.zip_code}, {self.phone}'

    def get_absolute_url(self):
        return reverse('shipping-address')

    def save(self, *args, **kwargs):
        if self.is_default:
            ShippingAddress.objects.filter(user=self.user, is_default=True).exclude(uid=self.uid).update(is_default=False)
            self.current_address = True
        elif not ShippingAddress.objects.filter(user=self.user).exists():
            self.is_default = True
            self.current_address = True
        super().save(*args, **kwargs)


class ShippingAddressForm(forms.ModelForm):
    save_address = forms.BooleanField(required=False, label='Save the billing addres')

    class Meta:
        model = ShippingAddress
        fields = [
            'first_name',
            'last_name',
            'street',
            'street_number',
            'zip_code',
            'city',
            'country',
            'phone'
        ]