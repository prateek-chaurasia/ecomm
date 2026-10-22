from django.db import migrations, models


DELIVERY_COPY = (
    'We strive to deliver your order at the earliest possible opportunity. '
    'Most orders are delivered within 2-3 business days, subject to location '
    'and service availability.'
)

LEGACY_SUMMARIES = {
    'Delivery availability and timelines depend on your location and order details.',
}

LEGACY_DETAILS = {
    (
        'Delivery timelines depend on your delivery area, order time, product availability, '
        'and weather. Orders outside our regular service area may require review; any '
        'additional delivery fee will be confirmed before acceptance.'
    ),
    (
        'Delivery timelines depend on your delivery area, order time, product availability, '
        'and weather. Orders to PIN codes outside the Standard Service Area are estimated '
        'to arrive within 4–6 days.'
    ),
}


def update_delivery_copy(apps, schema_editor):
    DeliveryGuideline = apps.get_model('home', 'DeliveryGuideline')
    guidelines = DeliveryGuideline.objects.using(schema_editor.connection.alias).all()
    for guideline in guidelines:
        updates = []
        summary = guideline.summary or ''
        details = guideline.details or ''
        if (
            summary in LEGACY_SUMMARIES
            or 'next day' in summary.casefold().replace('-', ' ').replace('_', ' ')
        ):
            guideline.summary = DELIVERY_COPY
            updates.append('summary')
        if (
            details in LEGACY_DETAILS
            or 'next day' in details.casefold().replace('-', ' ').replace('_', ' ')
        ):
            guideline.details = DELIVERY_COPY
            updates.append('details')
        if updates:
            guideline.save(using=schema_editor.connection.alias, update_fields=updates)


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0019_returnrefundpolicy_return_shipping_address'),
    ]

    operations = [
        migrations.RunPython(update_delivery_copy, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='deliveryguideline',
            name='next_day_delivery_enabled',
            field=models.BooleanField(default=True, verbose_name='Standard delivery enabled'),
        ),
        migrations.AlterField(
            model_name='deliveryguideline',
            name='summary',
            field=models.TextField(default=DELIVERY_COPY),
        ),
        migrations.AlterField(
            model_name='deliveryguideline',
            name='details',
            field=models.TextField(default=DELIVERY_COPY),
        ),
    ]
