import uuid

from django.db import migrations, models


def seed_marquee_announcement(apps, schema_editor):
    MarqueeAnnouncement = apps.get_model('home', 'MarqueeAnnouncement')
    MarqueeAnnouncement.objects.using(schema_editor.connection.alias).get_or_create(
        text=(
            'Use Code NEW10 and get 10% OFF* on all items over Rs. 4499. '
            'Maximum Discount: Rs. 500. T&C Apply.'
        ),
        defaults={'is_active': True, 'display_order': 0},
    )


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0017_city_and_shippingaddress_city_fk'),
    ]

    operations = [
        migrations.CreateModel(
            name='MarqueeAnnouncement',
            fields=[
                ('uid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now=True)),
                ('updated_at', models.DateTimeField(auto_now_add=True)),
                ('text', models.CharField(max_length=500)),
                ('is_active', models.BooleanField(default=True)),
                ('display_order', models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                'ordering': ['display_order', 'created_at'],
            },
        ),
        migrations.RunPython(seed_marquee_announcement, migrations.RunPython.noop),
    ]