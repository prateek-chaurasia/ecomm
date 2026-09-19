import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0023_order_status'),
    ]

    operations = [
        migrations.CreateModel(
            name='ServiceablePincode',
            fields=[
                ('uid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now=True)),
                ('updated_at', models.DateTimeField(auto_now_add=True)),
                ('pincode', models.CharField(max_length=10, unique=True)),
                ('is_active', models.BooleanField(default=True)),
                ('notes', models.CharField(blank=True, max_length=200)),
            ],
            options={
                'ordering': ['pincode'],
            },
        ),
        migrations.AddField(
            model_name='order',
            name='delivery_pincode',
            field=models.CharField(blank=True, max_length=10),
        ),
        migrations.AddField(
            model_name='order',
            name='outside_service_area',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending_review', 'Pending review'),
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