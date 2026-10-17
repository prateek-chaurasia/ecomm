from django import forms
from django.core.exceptions import ValidationError
from .models import ProductReview


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    widget = MultipleImageInput

    def clean(self, data, initial=None):
        if not data:
            return []
        if isinstance(data, (list, tuple)):
            return [forms.ImageField.clean(self, item, initial) for item in data]
        return [forms.ImageField.clean(self, data, initial)]


class ReviewForm(forms.ModelForm):
    photos = MultipleImageField(required=False)

    class Meta:
        model = ProductReview
        fields = ['stars', 'content']
        widgets = {
            "stars": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 5}),
            "content": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }

    def clean_photos(self):
        photos = self.cleaned_data['photos']
        if len(photos) > 5:
            raise ValidationError('You can upload up to five review photos.')
        return photos
