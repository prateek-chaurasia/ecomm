from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0028_product_show_in_catalog'),
    ]

    operations = [
        migrations.AddField(
            model_name='returndetails',
            name='return_policy',
            field=models.CharField(
                choices=[
                    ('returnable', 'Returnable'),
                    ('replaceable', 'Replaceable'),
                    ('non_returnable', 'Non-Returnable'),
                ],
                default='returnable',
                max_length=20,
            ),
        ),
        migrations.RunPython(
            lambda apps, schema_editor: apps.get_model(
                'products', 'ReturnDetails').objects.filter(
                    is_returnable=False).update(return_policy='non_returnable'),
            migrations.RunPython.noop,
        ),
    ]