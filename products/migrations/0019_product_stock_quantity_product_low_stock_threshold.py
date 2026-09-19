
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0018_alter_category_category_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='low_stock_threshold',
            field=models.PositiveIntegerField(default=5),
        ),
        migrations.AddField(
            model_name='product',
            name='stock_quantity',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
