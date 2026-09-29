from django.core.exceptions import ValidationError


def validate_person_name(value):
    if value and any(character.isdigit() for character in value):
        raise ValidationError('Numbers are not allowed in names.')