import django.db.models.deletion
from django.db import migrations, models


def assign_legacy_roorkee_state(apps, schema_editor):
    ShippingAddress = apps.get_model('home', 'ShippingAddress')
    State = apps.get_model('home', 'State')
    database = schema_editor.connection.alias
    addresses = ShippingAddress.objects.using(database).filter(state__isnull=True)
    unknown_addresses = [
        address
        for address in addresses.only('pk', 'country', 'city')
        if address.country != 'IN' or address.city.strip().casefold() != 'roorkee'
    ]
    if unknown_addresses:
        raise RuntimeError(
            'Cannot require ShippingAddress.state until all existing addresses '
            'without a state have been reviewed.'
        )

    state = State.objects.using(database).get(country_id='IN', name='Uttarakhand')
    addresses.update(state_id=state.pk)


class Migration(migrations.Migration):
    dependencies = [
        ('home', '0010_seed_india_country_and_states'),
    ]

    operations = [
        migrations.RunPython(assign_legacy_roorkee_state, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='shippingaddress',
            name='state',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='home.state'),
        ),
    ]