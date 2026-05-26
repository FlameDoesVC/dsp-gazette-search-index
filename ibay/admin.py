from django.contrib import admin, messages
from django.utils.html import format_html
from treebeard.admin import TreeAdmin
from treebeard.forms import movenodeform_factory

from ibay.models import Category, Product, ProductCategory, ProductImage, ProductInfo, Seller


@admin.action(description="Mark as NOT_SCRAPED (re-queue for scraping)")
def mark_not_scraped(modeladmin, request, queryset):
    updated = queryset.update(status="NOT_SCRAPED", error_message="")
    modeladmin.message_user(request, f"{updated} products re-queued for scraping.")


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


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    readonly_fields = ('image_preview', 'image_url')
    fields = ('image_preview', 'image_url')

    def image_preview(self, obj):
        if obj.image_url:
            return format_html(
                '<img src="{}" style="max-height:150px; max-width:300px;" />',
                obj.image_url,
            )
        return "-"
    image_preview.short_description = "Preview"


class ProductAdmin(admin.ModelAdmin):
    list_display = ('listing_id', 'name', 'seller_name', 'price', 'status', 'updated_at')
    list_filter = ('status',)
    search_fields = ('listing_id', 'name', 'seller__name', 'seller__contact_number')
    inlines = [ProductCategoryInline, ProductImageInline]
    actions = [mark_not_scraped]

    def seller_name(self, obj):
        return str(obj.seller) if obj.seller else ""
    seller_name.short_description = "Seller"
    seller_name.admin_order_field = 'seller__name'


class ProductImageAdmin(admin.ModelAdmin):
    list_display = ('product', 'image_preview', 'image_url', 'created_at')
    search_fields = ('product__name', 'product__listing_id')
    list_filter = ('created_at',)

    def image_preview(self, obj):
        if obj.image_url:
            return format_html(
                '<img src="{}" style="max-height:80px; max-width:150px;" />',
                obj.image_url,
            )
        return "-"
    image_preview.short_description = "Preview"


class ProductInfoAdmin(admin.ModelAdmin):
    list_display = ('product', 'info_key', 'info_value')
    search_fields = ('product__name', 'product__listing_id', 'info_key', 'info_value')


admin.site.register(Category, CategoryAdmin)
admin.site.register(Seller, SellerAdmin)
admin.site.register(Product, ProductAdmin)
admin.site.register(ProductImage, ProductImageAdmin)
admin.site.register(ProductInfo, ProductInfoAdmin)
