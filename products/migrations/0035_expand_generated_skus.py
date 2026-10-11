from django.db import migrations
from django.utils.text import slugify


def _component(value, fallback):
    component = ''.join(character for character in slugify(value).upper() if character.isalnum())
    return component[:3] or fallback


def _sequence(number):
    characters = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    result = ''
    while number:
        number, remainder = divmod(number, len(characters))
        result = characters[remainder] + result
    return result.rjust(4, '0')


def expand_generated_skus(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    database = schema_editor.connection.alias
    products = Product.objects.using(database).select_related('category').order_by('pk')

    for product in products.iterator():
        category_code = _component(product.category.category_name, 'GEN')
        product_code = _component(product.product_name, 'PRD')
        prefix = f'{category_code}-{product_code}-'
        if len(product.sku) != 11 or not product.sku.startswith(prefix):
            continue

        candidate = f'{prefix}0{product.sku[-3:]}'
        if Product.objects.using(database).filter(sku=candidate).exclude(pk=product.pk).exists():
            for sequence in range(1, 36 ** 4):
                candidate = f'{prefix}{_sequence(sequence)}'
                if not Product.objects.using(database).filter(sku=candidate).exclude(pk=product.pk).exists():
                    break
            else:
                raise RuntimeError(f'Could not generate a unique SKU for product {product.pk}.')
        Product.objects.using(database).filter(pk=product.pk).update(sku=candidate)


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0034_product_sku'),
    ]

    operations = [
        migrations.RunPython(expand_generated_skus, migrations.RunPython.noop),
    ]
