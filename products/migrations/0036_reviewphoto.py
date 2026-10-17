import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0035_expand_generated_skus'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReviewPhoto',
            fields=[
                ('uid', models.UUIDField(
                    default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now=True)),
                ('updated_at', models.DateTimeField(auto_now_add=True)),
                ('image', models.ImageField(
                    upload_to='product_reviews/%Y/%m/%d/')),
                ('review', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='photos',
                    to='products.productreview',
                )),
            ],
        ),
    ]
