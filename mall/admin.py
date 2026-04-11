from django.contrib import admin

from .models import Product, Order


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'price', 'content_type', 'stock', 'is_active', 'sort_order', 'created_at')
    list_filter = ('is_active', 'content_type', 'created_at')
    search_fields = ('name', 'description')
    ordering = ('-sort_order', '-id')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_no', 'user', 'product_name', 'quantity', 'currency', 'total_amount', 'pay_amount', 'pay_method', 'status', 'created_at')
    list_filter = ('status', 'currency', 'pay_method', 'created_at')
    search_fields = ('order_no', 'product_name', 'tx_hash', 'user__tg_user_id', 'user__username')
    readonly_fields = ('created_at',)
