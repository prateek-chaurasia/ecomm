from django import forms
from home.validators import validate_person_name


class ContactForm(forms.Form):
    SUBJECT_CHOICES = [
        ('general', 'General Inquiry'),
        ('order', 'Order Support'),
        ('product', 'Product Question'),
        ('return', 'Return/Exchange'),
        ('technical', 'Technical Support'),
        ('partnership', 'Partnership'),
        ('feedback', 'Feedback'),
    ]

    first_name = forms.CharField(max_length=100, validators=[validate_person_name])
    last_name = forms.CharField(
        max_length=100,
        required=False,
        validators=[validate_person_name],
    )
    email = forms.EmailField(max_length=254)
    subject = forms.ChoiceField(choices=SUBJECT_CHOICES)
    message = forms.CharField(max_length=5000, widget=forms.Textarea)
    newsletter = forms.BooleanField(required=False)