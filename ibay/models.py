from django.db import models
from treebeard.mp_tree import MP_Node


class Category(MP_Node):
    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    product_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    node_order_by = ['name']

    class Meta:
        verbose_name_plural = "categories"

    def __str__(self):
        prefix = "  " * (self.depth - 1) if self.depth else ""
        return f"{prefix}{self.name}"


class Seller(models.Model):
    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=255, blank=True)
    contact_number = models.CharField(max_length=255, blank=True)
    image_src = models.TextField(blank=True)
    is_premium = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    member_since = models.DateField(null=True, blank=True)
    last_login = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or str(self.id)


class Product(models.Model):
    listing_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=255)
    url = models.URLField()
    seller = models.ForeignKey(
        Seller, null=True, blank=True, on_delete=models.SET_NULL
    )
    price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    product_location = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    last_updated = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=50, default='NOT_SCRAPED')
    error_message = models.TextField(blank=True)
    categories = models.ManyToManyField(
        Category, through='ProductCategory'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class ProductCategory(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('product', 'category')

    def __str__(self):
        return f"{self.product} - {self.category}"


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='images'
    )
    image_url = models.URLField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.image_url


class ProductInfo(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='info'
    )
    info_key = models.CharField(max_length=255)
    info_value = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.info_key}: {self.info_value}"
