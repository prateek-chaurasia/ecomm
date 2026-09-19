
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0020_product_brand_name_product_suitable_for_age'),
    ]

    operations = [
        migrations.CreateModel(
            name='AgeGroup',
            fields=[
                ('uid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now=True)),
                ('updated_at', models.DateTimeField(auto_now_add=True)),
                ('name', models.CharField(max_length=50, unique=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.RemoveField(
            model_name='product',
            name='suitable_for_age',
        ),
        migrations.AddField(
            model_name='product',
            name='age_groups',
            field=models.ManyToManyField(blank=True, related_name='products', to='products.agegroup'),
        ),
    ]
