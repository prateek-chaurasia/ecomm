from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from products.image_utils import create_product_thumbnail, product_thumbnail_name
from products.models import ProductImage


class Command(BaseCommand):
    help = 'Generate WebP thumbnails for existing product images.'

    def handle(self, *args, **options):
        generated_count = 0
        images = ProductImage.objects.exclude(image_url='').iterator()

        for product_image in images:
            thumbnail_name = product_thumbnail_name(product_image.image_url.name)
            if default_storage.exists(thumbnail_name):
                continue

            thumbnail = create_product_thumbnail(product_image.image_url)
            default_storage.save(thumbnail_name, thumbnail)
            generated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Generated {generated_count} product thumbnail(s).',
            ),
        )
