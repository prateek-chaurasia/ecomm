from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0024_serviceablepincode_order_delivery_pincode_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='additional_delivery_fee',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Set this during review for non-serviceable pincodes.',
                max_digits=10,
                null=True,
            ),
        ),
    ]