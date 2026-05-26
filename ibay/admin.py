from django.contrib import admin
from treebeard.admin import TreeAdmin
from treebeard.forms import movenodeform_factory

from ibay.models import Category, Product, ProductCategory, ProductImage, ProductInfo, Seller


class CategoryAdmin(TreeAdmin):
    form = movenodeform_factory(Category)


class ProductCategoryInline(admin.TabularInline):
    model = ProductCategory
    extra = 0
    readonly_fields = ('category_name',)
    fields = ('category_name',)

    def category_name(self, obj):
        return str(obj.category)
    category_name.short_description = "Category"


class ProductAdmin(admin.ModelAdmin):
    inlines = [ProductCategoryInline]


admin.site.register(Category, CategoryAdmin)
admin.site.register(Seller)
admin.site.register(Product, ProductAdmin)
admin.site.register(ProductImage)
admin.site.register(ProductInfo)
