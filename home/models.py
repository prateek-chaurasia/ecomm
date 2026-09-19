from datetime import timedelta

from django.contrib.auth.models import User
from base.models import BaseModel
from django.urls import reverse
from django.db import models
from django import forms
from django_countries.fields import CountryField
from django.utils import timezone

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


class ShippingAddress(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    street = models.CharField(max_length=100)
    street_number = models.CharField(max_length=100)
    zip_code = models.CharField(max_length=30)
    city = models.CharField(max_length=50)
    country = CountryField()
    phone = models.CharField(max_length=30)
    current_address = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.street}, {self.street_number}, {self.city}, {self.country}, {self.zip_code}, {self.phone}'

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