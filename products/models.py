from django.db import models
from base.models import BaseModel
from django.utils.text import slugify
from django.utils.html import mark_safe
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator

# Create your models here.


class Category(BaseModel):
    category_name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, null=True, blank=True)
    # category_image = models.URLField(max_length=500, blank=True, null=True)
    category_image = models.ImageField(upload_to='categories/%Y/%m/%d/', blank=True, null=True)

    def save(self, *args, **kwargs):
        self.slug = slugify(self.category_name)
        super(Category, self).save(*args, **kwargs)

    def __str__(self) -> str:
        return self.category_name


class AgeGroup(BaseModel):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class Tag(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True, blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ColorVariant(BaseModel):
    color_name = models.CharField(max_length=100)
    price = models.IntegerField(default=0)

    def __str__(self) -> str:
        return self.color_name


class SizeVariant(BaseModel):
    size_name = models.CharField(max_length=100)
    price = models.IntegerField(default=0)
    order = models.IntegerField(default=0)

    def __str__(self) -> str:
        return self.size_name


class Product(BaseModel):
    AGE_GROUP_CHOICES = [
        ('0-2 years', '0-2 years'),
        ('3-5 years', '3-5 years'),
        ('6-8 years', '6-8 years'),
        ('9-12 years', '9-12 years'),
        ('13-17 years', '13-17 years'),
        ('18+ years', '18+ years'),
        ('All ages', 'All ages'),
    ]

    parent = models.ForeignKey(
        'self', related_name='variants', on_delete=models.CASCADE, blank=True, null=True)
    product_name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, null=True, blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="products")
    brand_name = models.CharField(max_length=100, default='Generic')
    age_groups = models.ManyToManyField('AgeGroup', related_name='products', blank=True)
    tags = models.ManyToManyField('Tag', related_name='products', blank=True)
    price = models.IntegerField()
    marked_price = models.IntegerField(default=0, help_text='Original price before discount')
    product_desription = models.TextField()
    color_variant = models.ManyToManyField(ColorVariant, blank=True)
    size_variant = models.ManyToManyField(SizeVariant, blank=True)
    newest_product = models.BooleanField(default=False)
    stock_quantity = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=5)
    gift_wrap_available = models.BooleanField(default=False)
    gift_wrap_price = models.PositiveIntegerField(default=0)
    bundle_eligible = models.BooleanField(
        default=False,
        help_text='Allow this product to be selected in gifting bundles.',
    )
    show_in_catalog = models.BooleanField(
        default=True,
        help_text='Show this product in the normal product listings and search results.',
    )

    @property
    def suitable_for_age(self):
        ages = self.age_groups.values_list('name', flat=True)
        return ', '.join(ages) if ages else 'All ages'

    @property
    def age_group_labels(self):
        return list(self.age_groups.values_list('name', flat=True))

    @property
    def discount_percentage(self):
        if not self.marked_price or self.marked_price <= self.price:
            return 0
        return int(round(((self.marked_price - self.price) / self.marked_price) * 100))

    @property
    def discount_label(self):
        percentage = self.discount_percentage
        return f'{percentage}% OFF' if percentage > 0 else ''

    @property
    def has_discount(self):
        return self.marked_price > self.price

    @property
    def in_stock(self):
        return self.stock_quantity > 0

    @property
    def stock_status(self):
        if self.stock_quantity <= 0:
            return 'Out of stock'
        if self.stock_quantity <= self.low_stock_threshold:
            return 'Low stock'
        return 'In stock'

    def save(self, *args, **kwargs):
        self.slug = slugify(self.product_name)
        super(Product, self).save(*args, **kwargs)

    def __str__(self) -> str:
        return self.product_name

    def get_product_price_by_size(self, size):
        return self.price + SizeVariant.objects.get(size_name=size).price

    def get_rating(self):
        total = sum(int(review['stars']) for review in self.reviews.values())

        if self.reviews.count() > 0:
            return total / self.reviews.count()
        else:
            return 0


class ReturnDetails(BaseModel):
    product = models.OneToOneField(
        Product, on_delete=models.CASCADE, related_name='return_details')
    is_returnable = models.BooleanField(default=True)
    return_window_days = models.PositiveIntegerField(default=7)
    policy = models.TextField(blank=True)
    conditions = models.TextField(blank=True)

    def __str__(self):
        return f'Return details for {self.product.product_name}'


class ProductImage(BaseModel):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='product_images')
    # image_url = models.URLField(
    #     max_length=500, default='https://via.placeholder.com/500')
    image_url = models.ImageField(upload_to='products/%Y/%m/%d/', blank=True, null=True)

    def img_preview(self):
        return mark_safe(f'<img src="{self.image_url.url}" width="500"/>')


class Coupon(BaseModel):
    coupon_code = models.CharField(max_length=10)
    is_expired = models.BooleanField(default=False)
    discount_amount = models.IntegerField(default=100)
    minimum_amount = models.IntegerField(default=500)


class BundleConfiguration(models.Model):
    max_products = models.PositiveIntegerField(
        default=6,
        validators=[MinValueValidator(1)],
        help_text='Maximum number of different products a customer can put in one bundle.',
    )

    @classmethod
    def get_solo(cls):
        configuration, _ = cls.objects.get_or_create(pk=1)
        return configuration

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Bundle settings (up to {self.max_products} products)'


class BundlePackagingOption(BaseModel):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    sample_image = models.ImageField(upload_to='bundles/packaging-samples/%Y/%m/%d/')
    price = models.PositiveIntegerField(
        default=0,
        help_text='Additional charge for this packaging per complete bundle.',
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return f'{self.name} (+₹{self.price})'


class BundleOffer(BaseModel):
    title = models.CharField(max_length=150)
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(blank=True)
    banner = models.ImageField(upload_to='bundles/banners/%Y/%m/%d/', blank=True, null=True)
    packaging_image = models.ImageField(upload_to='bundles/packaging/%Y/%m/%d/', blank=True, null=True)
    discount_percentage = models.PositiveIntegerField(
        default=0, validators=[MaxValueValidator(100)])
    products = models.ManyToManyField(
        Product, related_name='bundle_offers', blank=True)
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    @property
    def max_products(self):
        return BundleConfiguration.get_solo().max_products

    def price_for(self, products):
        subtotal = sum(product.price for product in products)
        return subtotal - (subtotal * self.discount_percentage // 100)

    def clean(self):
        if self.pk and self.products.count() > self.max_products:
            raise ValidationError(
                {'products': f'Choose no more than {self.max_products} products.'})

    def __str__(self):
        return self.title


class ProductReview(BaseModel):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='reviews')
    stars = models.IntegerField(
        default=3, choices=[(i, i) for i in range(1, 6)])
    content = models.TextField(blank=True, null=True)
    date_added = models.DateTimeField(auto_now_add=True)
    likes = models.ManyToManyField(
        User, related_name="liked_reviews", blank=True)
    dislikes = models.ManyToManyField(
        User, related_name="disliked_reviews", blank=True)

    def like_count(self):
        return self.likes.count()

    def dislike_count(self):
        return self.dislikes.count()


class Wishlist(BaseModel):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="wishlist")
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="wishlisted_by")
    size_variant = models.ForeignKey(SizeVariant, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name="wishlist_items")

    added_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'product', 'size_variant')

    def __str__(self) -> str:
        return f'{self.user.username} - {self.product.product_name} - {self.size_variant.size_name if self.size_variant else "No Size"}'
