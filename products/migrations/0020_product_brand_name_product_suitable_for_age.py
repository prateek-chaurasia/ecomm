
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0019_product_stock_quantity_product_low_stock_threshold'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='brand_name',
            field=models.CharField(default='Generic', max_length=100),
        ),
        migrations.AddField(
            model_name='product',
            name='suitable_for_age',
            field=models.CharField(choices=[('0-2 years', '0-2 years'), ('3-5 years', '3-5 years'), ('6-8 years', '6-8 years'), ('9-12 years', '9-12 years'), ('13-17 years', '13-17 years'), ('18+ years', '18+ years'), ('All ages', 'All ages')], default='All ages', max_length=30),
        ),
    ]
