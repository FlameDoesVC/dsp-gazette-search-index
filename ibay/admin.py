from django.contrib import admin
from treebeard.admin import TreeAdmin
from treebeard.forms import movenodeform_factory

from ibay.models import Category, Product, ProductCategory, ProductImage, ProductInfo, Seller


class CategoryAdmin(TreeAdmin):
    form = movenodeform_factory(Category)
    search_fields = ('name',)


class SellerAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'contact_number', 'location', 'updated_at')
    list_filter = ('location',)
    search_fields = ('id', 'name', 'contact_number')


class ProductCategoryInline(admin.TabularInline):
    model = ProductCategory
    extra = 0
    readonly_fields = ('category_name',)
    fields = ('category_name',)

    def category_name(self, obj):
        return str(obj.category)
    category_name.short_description = "Category"


class ProductAdmin(admin.ModelAdmin):
    list_display = ('listing_id', 'name', 'seller_name', 'price', 'status', 'updated_at')
    list_filter = ('status',)
    search_fields = ('listing_id', 'name', 'seller__name', 'seller__contact_number')
    inlines = [ProductCategoryInline]

    def seller_name(self, obj):
        return str(obj.seller) if obj.seller else ""
    seller_name.short_description = "Seller"
    seller_name.admin_order_field = 'seller__name'


class ProductImageAdmin(admin.ModelAdmin):
    list_display = ('product', 'image_url', 'created_at')
    search_fields = ('product__name', 'product__listing_id')
    list_filter = ('created_at',)


class ProductInfoAdmin(admin.ModelAdmin):
    list_display = ('product', 'info_key', 'info_value')
    search_fields = ('product__name', 'product__listing_id', 'info_key', 'info_value')


admin.site.register(Category, CategoryAdmin)
admin.site.register(Seller, SellerAdmin)
admin.site.register(Product, ProductAdmin)
admin.site.register(ProductImage, ProductImageAdmin)
admin.site.register(ProductInfo, ProductInfoAdmin)
