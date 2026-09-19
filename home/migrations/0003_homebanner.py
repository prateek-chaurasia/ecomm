
from django.db import migrations, models
import django.utils.timezone
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0002_alter_shippingaddress_street_number'),
    ]

    operations = [
        migrations.CreateModel(
            name='HomeBanner',
            fields=[
                ('uid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now=True)),
                ('updated_at', models.DateTimeField(auto_now_add=True)),
                ('title', models.CharField(max_length=150)),
                ('subtitle', models.CharField(blank=True, max_length=250)),
                ('image', models.ImageField(blank=True, null=True, upload_to='banners/%Y/%m/%d/')),
                ('button_text', models.CharField(default='Shop Now', max_length=50)),
                ('link_url', models.URLField(blank=True, max_length=500)),
                ('is_active', models.BooleanField(default=True)),
                ('starts_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('ends_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ['-starts_at'],
            },
        ),
    ]
