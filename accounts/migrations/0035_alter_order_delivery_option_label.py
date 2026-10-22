from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0034_cod_return_refund_details'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='delivery_option',
            field=models.CharField(
                blank=True,
                choices=[
                    ('local_free', 'Free local delivery'),
                    ('express', 'Express delivery'),
                    ('next_day', 'Standard delivery'),
                ],
                default='',
                max_length=20,
            ),
        ),
    ]
