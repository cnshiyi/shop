from django.contrib import admin

from .models import Product, Order, CloudServerPlan, CloudServerOrder


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'price', 'content_type', 'stock', 'is_active', 'sort_order', 'created_at')
    list_filter = ('is_active', 'content_type', 'created_at')
    search_fields = ('name', 'description')
    ordering = ('-sort_order', '-id')


@admin.register(CloudServerPlan)
class CloudServerPlanAdmin(admin.ModelAdmin):
    list_display = ('id', 'provider', 'region_name', 'plan_name', 'price', 'currency', 'is_active', 'sort_order')
    list_filter = ('provider', 'region_name', 'currency', 'is_active')
    search_fields = ('region_name', 'plan_name', 'cpu', 'memory', 'storage', 'bandwidth')
    ordering = ('provider', 'region_name', '-sort_order', 'id')


@admin.register(CloudServerOrder)
class CloudServerOrderAdmin(admin.ModelAdmin):
    list_display = ('order_no', 'user', 'provider', 'region_name', 'plan_name', 'server_name', 'public_ip', 'service_expires_at', 'ip_recycle_at', 'status', 'created_at')
    list_filter = ('provider', 'region_name', 'status', 'currency', 'pay_method', 'created_at')
    search_fields = ('order_no', 'server_name', 'public_ip', 'plan_name', 'region_name', 'tx_hash', 'user__tg_user_id', 'user__username')
    readonly_fields = ('created_at', 'paid_at', 'completed_at', 'updated_at')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_no', 'user', 'product_name', 'quantity', 'currency', 'total_amount', 'pay_amount', 'pay_method', 'status', 'created_at')
    list_filter = ('status', 'currency', 'pay_method', 'created_at')
    search_fields = ('order_no', 'product_name', 'tx_hash', 'user__tg_user_id', 'user__username')
    readonly_fields = ('created_at',)
