from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from accounts.models import Profile
from home.models import CountryModel, ShippingAddress, State
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
    class Meta:
        model = Profile
        fields = ['profile_image', 'bio']
        widgets = {
            'profile_image': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter image URL (e.g., https://example.com/image.jpg)'
            }),
            'bio': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4
            })
        }


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
