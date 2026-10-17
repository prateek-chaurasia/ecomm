from django import forms
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.core.mail import send_mail
from django.template import loader

from base.emails import send_customer_email


class BrandedPasswordResetForm(PasswordResetForm):
    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        subject = loader.render_to_string(subject_template_name, context)
        subject = ''.join(subject.splitlines())
        plain_message = loader.render_to_string(email_template_name, context)
        if html_email_template_name:
            return send_customer_email(
                subject,
                plain_message,
                to_email,
                html_email_template_name,
                context,
                from_email=from_email,
            )

        return send_mail(
            subject,
            plain_message,
            from_email,
            [to_email],
            fail_silently=False,
        )
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from accounts.models import Profile, ReplacementReason, ReturnRequest
from home.models import City, CountryModel, ShippingAddress, State
from home.validators import validate_person_name

import re


def validate_registration_password(value):
    errors = []
    if not 8 <= len(value) <= 10:
        errors.append('Password must be 8 to 10 characters long.')
    if not any(character.isdigit() for character in value):
        errors.append('Password must include at least one number.')
    if not any(not character.isalnum() and not character.isspace() for character in value):
        errors.append('Password must include at least one special character.')
    if errors:
        raise ValidationError(errors)


class UserProfileForm(forms.ModelForm):
    profile_image_file = forms.ImageField(
        required=False,
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control-file',
            'accept': 'image/jpeg,image/png,image/webp',
            'data-profile-image-input': 'true',
        }),
        help_text='Upload a JPG, PNG, or WebP image no larger than 5 MB.',
    )

    class Meta:
        model = Profile
        fields = ['profile_image_file', 'bio']
        widgets = {
            'bio': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4
            })
        }

    def clean_profile_image_file(self):
        image = self.cleaned_data.get('profile_image_file')
        if image and image.size > 5 * 1024 * 1024:
            raise ValidationError('Image must be no larger than 5 MB.')
        return image


class UserUpdateForm(forms.ModelForm):
    first_name = forms.CharField(
        max_length=150,
        required=False,
        validators=[validate_person_name],
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        validators=[validate_person_name],
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']


class RegistrationForm(forms.Form):
    first_name = forms.CharField(max_length=150, validators=[validate_person_name])
    last_name = forms.CharField(max_length=150, validators=[validate_person_name])
    email = forms.EmailField(label='Email')
    password = forms.CharField(
        max_length=128,
        strip=False,
        validators=[validate_registration_password],
    )


class ShippingAddressForm(forms.ModelForm):
    first_name = forms.CharField(
        max_length=100,
        validators=[validate_person_name],
    )
    last_name = forms.CharField(
        max_length=100,
        validators=[validate_person_name],
    )

    class Meta:
        model = ShippingAddress
        fields = '__all__'
        exclude = ['user', 'current_address']
        
        labels = {
            'street': 'House No / Flat / Building / Floor',
            'street_number': 'Building / Society/ Street / Colony Details',
            'zip_code': 'Pincode',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['is_default'].label = 'Use as my preferred shipping address'
        self.fields['country'].choices = [
            (country.code, country.name)
            for country in CountryModel.objects.filter(code='IN')
        ]

        if not self.is_bound and self.instance._state.adding and not self.initial.get('country'):
            self.initial['country'] = 'IN'

        country = (
            self.data.get(self.add_prefix('country'))
            if self.is_bound
            else self.initial.get('country')
        )
        if not country and not self.instance._state.adding:
            country = self.instance.country
        country_code = getattr(country, 'code', country)
        if country_code == 'IN':
            self.fields['state'].queryset = State.objects.filter(
                country__code='IN').order_by('name')
        else:
            self.fields['state'].queryset = State.objects.none()
        self.fields['state'].widget.attrs['class'] = 'form-control select2-state'
        state_value = self.data.get(self.add_prefix('state')) if self.is_bound else None
        state_value = state_value or self.initial.get('state') or self.instance.state_id
        state_id = getattr(state_value, 'pk', state_value)
        self.fields['city'].queryset = City.objects.filter(
            state_id=state_id,
            state__country__code='IN',
        ).order_by('name') if state_id else City.objects.none()
        self.fields['city'].widget.attrs.update({
            'class': 'form-control select2-city',
            'data-placeholder': 'Select a city',
        })
        if not state_id:
            self.fields['city'].widget.attrs['disabled'] = True

        self.fields['zip_code'].widget.attrs.update({
            'pattern': r'[1-9][0-9]{5}',
            'maxlength': 6,
            'inputmode': 'numeric',
        })
        self.fields['phone'].widget.attrs.update({
            'pattern': r'[6-9][0-9]{9}',
            'maxlength': 10,
            'inputmode': 'numeric',
        })
        
    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        # Matches 10-digit numbers starting with 6, 7, 8, or 9
        if not re.match(r'^[6-9]\d{9}$', phone):
            raise ValidationError('Enter a valid 10-digit Indian phone number.')
        return phone
    
class GuestShippingAddressForm(ShippingAddressForm):
    email = forms.EmailField(required=True, label='Email address')


class CustomPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label="Current password",
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
    )
    new_password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
    )
    new_password2 = forms.CharField(
        label="New password confirmation",
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
    )


class ReturnRequestForm(forms.ModelForm):
    RETURN_REASON_CHOICES = (
        ('bought_by_mistake', 'Bought by mistake'),
        ('better_price_available', 'Better price available'),
        ('no_longer_needed', 'No longer needed'),
        ('item_arrived_too_late', 'Item arrived too late'),
        ('defective_or_doesnt_work', "Defective or doesn't work"),
        ('box_damaged_product_ok', 'Shipping box is damaged, but the product is ok'),
        ('product_damaged_box_ok', 'Product is damaged, but shipping box is ok'),
        ('incorrect_item_sent', 'Incorrect item was sent (Wrong item/color/size)'),
        ('missing_parts_or_accessories', 'Missing parts or accessories'),
        ('quality_not_adequate', 'Performance or quality not adequate'),
        ('inaccurate_website_description', 'Inaccurate website description'),
        ('not_defined', 'Reason not defined'),
    )

    return_reason = forms.ChoiceField(
        choices=(('', 'Choose a reason'),) + RETURN_REASON_CHOICES,
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-control',
            'id': 'return-reason',
        }),
    )
    reason = forms.CharField(
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'maxlength': 2000,
            'placeholder': 'Tell us why you would like to return this order.',
        }),
    )

    refund_method = forms.ChoiceField(
        choices=(('', 'Choose a refund method'),) + tuple(
            ReturnRequest.RefundMethod.choices),
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'}),
    )

    class Meta:
        model = ReturnRequest
        fields = [
            'request_type', 'replacement_reason', 'reason', 'refund_method',
            'refund_upi_id', 'refund_account_holder_name',
            'refund_account_number', 'refund_ifsc_code',
        ]
        widgets = {
            'request_type': forms.RadioSelect,
            'replacement_reason': forms.Select(attrs={'class': 'form-control'}),
            'refund_upi_id': forms.TextInput(attrs={
                'class': 'form-control',
                'autocomplete': 'off',
                'maxlength': 120,
            }),
            'refund_account_holder_name': forms.TextInput(attrs={
                'class': 'form-control',
                'autocomplete': 'name',
                'maxlength': 150,
            }),
            'refund_account_number': forms.TextInput(attrs={
                'class': 'form-control',
                'autocomplete': 'off',
                'inputmode': 'numeric',
                'maxlength': 34,
            }),
            'refund_ifsc_code': forms.TextInput(attrs={
                'class': 'form-control',
                'autocomplete': 'off',
                'maxlength': 11,
            }),
        }

    def __init__(self, *args, order=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.order = order or getattr(self.instance, 'order', None)
        self.fields['replacement_reason'].queryset = ReplacementReason.objects.filter(
            is_active=True)
        self.fields['request_type'].widget.choices = (
            ReturnRequest.RequestType.choices
        )

    def clean(self):
        cleaned_data = super().clean()
        request_type = cleaned_data.get('request_type')
        reason = (cleaned_data.get('reason') or '').strip()
        return_reason = cleaned_data.get('return_reason')
        replacement_reason = cleaned_data.get('replacement_reason')
        if request_type == ReturnRequest.RequestType.RETURN:
            if not return_reason:
                self.add_error('return_reason', 'Choose a reason for the return.')
            elif return_reason == 'not_defined':
                if not reason:
                    self.add_error(
                        'reason',
                        'Please tell us why you are returning this order.',
                    )
                else:
                    cleaned_data['reason'] = f'Reason not defined: {reason}'
            else:
                reason_label = dict(self.RETURN_REASON_CHOICES)[return_reason]
                cleaned_data['reason'] = reason_label
            if self.order and self.order.payment_mode == 'Cash on Delivery':
                self._clean_cod_refund_details(cleaned_data)
            else:
                self._clear_cod_refund_details(cleaned_data)
        if (
            request_type == ReturnRequest.RequestType.REPLACEMENT
            and not replacement_reason
        ):
            self.add_error(
                'replacement_reason', 'Choose why you need a replacement.')
        elif request_type == ReturnRequest.RequestType.REPLACEMENT:
            cleaned_data['reason'] = (
                f'{replacement_reason.name}: {reason}'.rstrip(': ')
            )
            self._clear_cod_refund_details(cleaned_data)
        return cleaned_data

    def _clean_cod_refund_details(self, cleaned_data):
        refund_method = cleaned_data.get('refund_method')
        upi_id = (cleaned_data.get('refund_upi_id') or '').strip()
        account_holder = (
            cleaned_data.get('refund_account_holder_name') or ''
        ).strip()
        account_number = (
            cleaned_data.get('refund_account_number') or ''
        ).strip()
        ifsc_code = (
            cleaned_data.get('refund_ifsc_code') or ''
        ).strip().upper()

        if not refund_method:
            self.add_error(
                'refund_method',
                'Choose UPI ID or bank account for your refund.',
            )
        elif refund_method == ReturnRequest.RefundMethod.UPI:
            if not upi_id:
                self.add_error('refund_upi_id', 'Enter your UPI ID.')
            elif not re.fullmatch(
                r'[A-Za-z0-9][A-Za-z0-9._-]{1,255}@[A-Za-z][A-Za-z0-9.-]{1,63}',
                upi_id,
            ):
                self.add_error('refund_upi_id', 'Enter a valid UPI ID.')
            self._clear_bank_details(cleaned_data)
        elif refund_method == ReturnRequest.RefundMethod.BANK_ACCOUNT:
            if not account_holder:
                self.add_error(
                    'refund_account_holder_name',
                    'Enter the account holder name.',
                )
            if not account_number:
                self.add_error(
                    'refund_account_number', 'Enter the bank account number.')
            elif not account_number.isdigit() or not 9 <= len(account_number) <= 18:
                self.add_error(
                    'refund_account_number',
                    'Enter a valid 9 to 18 digit bank account number.',
                )
            if not ifsc_code:
                self.add_error('refund_ifsc_code', 'Enter the bank IFSC code.')
            elif not re.fullmatch(r'[A-Z]{4}0[A-Z0-9]{6}', ifsc_code):
                self.add_error('refund_ifsc_code', 'Enter a valid IFSC code.')
            cleaned_data['refund_ifsc_code'] = ifsc_code
            cleaned_data['refund_upi_id'] = ''

    @staticmethod
    def _clear_bank_details(cleaned_data):
        cleaned_data['refund_account_holder_name'] = ''
        cleaned_data['refund_account_number'] = ''
        cleaned_data['refund_ifsc_code'] = ''

    @classmethod
    def _clear_cod_refund_details(cls, cleaned_data):
        cleaned_data['refund_method'] = ''
        cleaned_data['refund_upi_id'] = ''
        cls._clear_bank_details(cleaned_data)
