from io import BytesIO
from pathlib import PurePosixPath

from django.core.files.base import ContentFile
from PIL import Image, ImageOps


PRODUCT_THUMBNAIL_MAX_SIZE = (640, 640)


def product_thumbnail_name(image_name):
    image_path = PurePosixPath(image_name)
    thumbnail_path = PurePosixPath(
        image_path.parts[0],
        'thumbnails',
        *image_path.parts[1:-1],
        f'{image_path.name}.webp',
    )
    return str(thumbnail_path)


def create_product_thumbnail(image_file):
    image_file.open('rb')
    with Image.open(image_file) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail(PRODUCT_THUMBNAIL_MAX_SIZE, Image.Resampling.LANCZOS)
        image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')

        output = BytesIO()
        image.save(output, format='WEBP', quality=75, method=6)

    return ContentFile(output.getvalue())
