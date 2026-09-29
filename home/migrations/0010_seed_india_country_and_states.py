from django.db import migrations


INDIAN_STATES_AND_UNION_TERRITORIES = [
    'Andhra Pradesh',
    'Arunachal Pradesh',
    'Assam',
    'Bihar',
    'Chhattisgarh',
    'Goa',
    'Gujarat',
    'Haryana',
    'Himachal Pradesh',
    'Jharkhand',
    'Karnataka',
    'Kerala',
    'Madhya Pradesh',
    'Maharashtra',
    'Manipur',
    'Meghalaya',
    'Mizoram',
    'Nagaland',
    'Odisha',
    'Punjab',
    'Rajasthan',
    'Sikkim',
    'Tamil Nadu',
    'Telangana',
    'Tripura',
    'Uttar Pradesh',
    'Uttarakhand',
    'West Bengal',
    'Andaman and Nicobar Islands',
    'Chandigarh',
    'Dadra and Nagar Haveli and Daman and Diu',
    'Delhi',
    'Jammu and Kashmir',
    'Ladakh',
    'Lakshadweep',
    'Puducherry',
]


def seed_india(apps, schema_editor):
    CountryModel = apps.get_model('home', 'CountryModel')
    State = apps.get_model('home', 'State')

    country, _ = CountryModel.objects.update_or_create(
        code='IN',
        defaults={'name': 'India'},
    )
    for state_name in INDIAN_STATES_AND_UNION_TERRITORIES:
        State.objects.get_or_create(country_id=country.pk, name=state_name)


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0009_countrymodel_alter_shippingaddress_zip_code_state_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_india, migrations.RunPython.noop),
    ]