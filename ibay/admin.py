from django.contrib import admin

from ibay.models import Category, Product, ProductCategory, ProductImage, ProductInfo, Seller


admin.site.register(Category)
admin.site.register(Seller)
admin.site.register(Product)
admin.site.register(ProductCategory)
admin.site.register(ProductImage)
admin.site.register(ProductInfo)
