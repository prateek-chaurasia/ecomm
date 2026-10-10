import unicodedata

from django.db import migrations, models
import django.db.models.deletion


def normalize_city_name(name):
    normalized = unicodedata.normalize('NFKD', name)
    normalized = ''.join(
        character for character in normalized
        if not unicodedata.combining(character)
    ).casefold()
    return ''.join(character for character in normalized if character.isalnum())


def migrate_shipping_cities(apps, schema_editor):
    City = apps.get_model('home', 'City')
    ShippingAddress = apps.get_model('home', 'ShippingAddress')
    database = schema_editor.connection.alias

    for address in ShippingAddress.objects.using(database).all().iterator():
        city_name = ' '.join((address.city_name or '').split())
        normalized_name = normalize_city_name(city_name)
        if normalized_name == 'dehradun':
            city_name = 'Dehradun'
        city, _ = City.objects.using(database).get_or_create(
            state_id=address.state_id,
            normalized_name=normalized_name,
            defaults={'name': city_name},
        )
        address.city_id = city.pk
        address.save(using=database, update_fields=['city'])


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0016_returnrefundpolicy_cancellation_window_hours_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='City',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('normalized_name', models.CharField(editable=False, max_length=100)),
                ('state', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cities', to='home.state')),
            ],
            options={
                'ordering': ['name'],
                'constraints': [
                    models.UniqueConstraint(fields=('state', 'normalized_name'), name='unique_city_name_per_state'),
                ],
            },
        ),
        migrations.RenameField(
            model_name='shippingaddress',
            old_name='city',
            new_name='city_name',
        ),
        migrations.AddField(
            model_name='shippingaddress',
            name='city',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='shipping_addresses', to='home.city'),
        ),
        migrations.RunPython(migrate_shipping_cities, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='shippingaddress',
            name='city_name',
        ),
        migrations.AlterField(
            model_name='shippingaddress',
            name='city',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='shipping_addresses', to='home.city'),
        ),
    ]