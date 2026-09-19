from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0022_bundlecartitem_packaging_option_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[
                    ('accepted', 'Order accepted'),
                    ('packing', 'Being packed'),
                    ('dispatched', 'Dispatched'),
                    ('out_for_delivery', 'Out for delivery'),
                    ('delivered', 'Delivered'),
                    ('cancelled', 'Cancelled'),
                ],
                default='accepted',
                max_length=30,
            ),
        ),
    ]