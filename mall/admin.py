from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from bot.config import BOT_TOKEN
from cloud.bootstrap import build_mtproxy_links
from .models import Product, Order, CloudServerPlan, CloudServerOrder


def _send_telegram_message(chat_id: int, text: str):
    import json
    from urllib import request

    if not BOT_TOKEN or not chat_id:
        return False, '缺少 BOT_TOKEN 或 chat_id'
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    payload = json.dumps({'chat_id': chat_id, 'text': text}).encode('utf-8')
    req = request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with request.urlopen(req, timeout=20) as resp:
            return 200 <= resp.status < 300, f'HTTP {resp.status}'
    except Exception as exc:
        return False, str(exc)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'price', 'content_type', 'stock', 'is_active', 'sort_order', 'created_at')
    list_filter = ('is_active', 'content_type', 'created_at')
    search_fields = ('name', 'description')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-sort_order', '-id')
    list_per_page = 50
    fieldsets = (
        ('商品信息', {'fields': ('name', 'description', 'price', 'content_type')}),
        ('商品内容', {'fields': ('content_text', 'content_image', 'content_video')}),
        ('库存与展示', {'fields': ('stock', 'is_active', 'sort_order')}),
        ('时间信息', {'fields': ('created_at', 'updated_at')}),
    )


@admin.register(CloudServerPlan)
class CloudServerPlanAdmin(admin.ModelAdmin):
    list_display = ('id', 'provider', 'region_name', 'plan_name', 'price', 'currency', 'is_active', 'sort_order')
    list_filter = ('provider', 'region_name', 'currency', 'is_active')
    search_fields = ('region_name', 'plan_name', 'cpu', 'memory', 'storage', 'bandwidth')
    ordering = ('provider', 'region_name', '-sort_order', 'id')
    list_editable = ('price', 'is_active', 'sort_order')
    list_per_page = 50
    fieldsets = (
        ('基础信息', {'fields': ('provider', 'region_code', 'region_name', 'plan_name', 'currency')}),
        ('规格信息', {'fields': ('cpu', 'memory', 'storage', 'bandwidth')}),
        ('销售信息', {'fields': ('price', 'is_active', 'sort_order')}),
        ('时间信息', {'fields': ('created_at', 'updated_at')}),
    )


@admin.register(CloudServerOrder)
class CloudServerOrderAdmin(admin.ModelAdmin):
    list_display = ('order_no', 'user', 'provider', 'region_name', 'plan_name', 'quantity', 'status_badge', 'service_status', 'public_ip', 'service_expires_at', 'ip_recycle_at', 'mtproxy_port', 'mtproxy_link_preview', 'created_at')
    list_filter = ('provider', 'region_name', 'status', 'currency', 'pay_method', 'created_at')
    search_fields = ('order_no', 'server_name', 'public_ip', 'plan_name', 'region_name', 'tx_hash', 'user__tg_user_id', 'user__username')
    readonly_fields = ('order_no', 'created_at', 'paid_at', 'completed_at', 'updated_at', 'tx_hash', 'mtproxy_link', 'mtproxy_secret', 'provider_resource_id', 'instance_id', 'last_user_id', 'pay_amount', 'total_amount')
    ordering = ('-id',)
    list_per_page = 50
    actions = ('renew_31_days', 'restore_service', 'resend_mtproxy_links')
    fieldsets = (
        ('订单信息', {'fields': ('order_no', 'user', 'plan', 'provider', 'region_code', 'region_name', 'plan_name', 'quantity', 'status')}),
        ('支付信息', {'fields': ('currency', 'total_amount', 'pay_amount', 'pay_method', 'tx_hash', 'paid_at', 'expired_at')}),
        ('服务器信息', {'fields': ('server_name', 'image_name', 'instance_id', 'provider_resource_id', 'public_ip', 'previous_public_ip', 'static_ip_name')}),
        ('代理信息', {'fields': ('mtproxy_port', 'mtproxy_host', 'mtproxy_link', 'mtproxy_secret')}),
        ('生命周期', {'fields': ('lifecycle_days', 'service_started_at', 'service_expires_at', 'renew_grace_expires_at', 'suspend_at', 'delete_at', 'ip_recycle_at', 'last_renewed_at')}),
        ('运维备注', {'fields': ('last_user_id', 'login_user', 'login_password', 'provision_note', 'created_at', 'completed_at', 'updated_at')}),
    )

    def status_badge(self, obj):
        color_map = {
            'pending': '#999999', 'paid': '#1677ff', 'provisioning': '#1677ff', 'completed': '#52c41a',
            'renew_pending': '#faad14', 'expiring': '#fa8c16', 'suspended': '#722ed1', 'deleting': '#ff4d4f',
            'deleted': '#595959', 'failed': '#ff4d4f', 'cancelled': '#8c8c8c', 'expired': '#8c8c8c',
        }
        label = dict(CloudServerOrder.STATUS_CHOICES).get(obj.status, obj.status)
        color = color_map.get(obj.status, '#999999')
        return format_html('<span style="color:#fff;background:{};padding:2px 8px;border-radius:10px;">{}</span>', color, label)
    status_badge.short_description = '状态'

    def service_status(self, obj):
        if obj.status == 'completed' and obj.service_expires_at:
            if obj.service_expires_at < timezone.now():
                return '已到期'
            remaining = obj.service_expires_at - timezone.now()
            return f'剩余 {remaining.days} 天'
        if obj.status == 'failed':
            return '创建失败'
        if obj.status == 'pending':
            return '待支付'
        return '-'
    service_status.short_description = '服务状态'

    def mtproxy_link_preview(self, obj):
        if not obj.mtproxy_link:
            return '-'
        return format_html('<a href="{}" target="_blank">代理链接</a>', obj.mtproxy_link)
    mtproxy_link_preview.short_description = '代理链接'

    def save_model(self, request, obj, form, change):
        if change:
            old = CloudServerOrder.objects.filter(pk=obj.pk).first()
            notes = []
            if old and old.user_id != obj.user_id:
                obj.last_user_id = old.user.tg_user_id if hasattr(old.user, 'tg_user_id') else old.last_user_id
                notes.append(f'后台改绑用户: {old.user_id} -> {obj.user_id}')
            if old and (old.public_ip != obj.public_ip or old.mtproxy_port != obj.mtproxy_port) and obj.mtproxy_secret and obj.public_ip:
                tg_link, _ = build_mtproxy_links(obj.public_ip, obj.mtproxy_port, obj.mtproxy_secret)
                obj.mtproxy_host = obj.public_ip
                obj.mtproxy_link = tg_link
                obj.previous_public_ip = old.public_ip if old.public_ip != obj.public_ip else old.previous_public_ip
                notes.append('后台更新了 IP/端口，旧 MTProxy 链接已失效，已重新生成新链接。')
            if notes:
                obj.provision_note = '\n'.join(filter(None, [obj.provision_note, *notes]))
        super().save_model(request, obj, form, change)

    @admin.action(description='手动续费/恢复31天')
    def renew_31_days(self, request, queryset):
        now = timezone.now()
        count = 0
        for order in queryset:
            base = order.service_expires_at or now
            if base < now:
                base = now
            order.service_expires_at = base + timezone.timedelta(days=31)
            order.last_renewed_at = now
            order.status = 'completed'
            order.provision_note = '\n'.join(filter(None, [order.provision_note, '后台手动续费/恢复 31 天。']))
            order.save(update_fields=['service_expires_at', 'last_renewed_at', 'status', 'provision_note', 'updated_at'])
            count += 1
        self.message_user(request, f'已手动续费/恢复 {count} 条云服务器订单。', level=messages.SUCCESS)

    @admin.action(description='手动恢复为已创建')
    def restore_service(self, request, queryset):
        count = queryset.update(status='completed', updated_at=timezone.now())
        self.message_user(request, f'已恢复 {count} 条云服务器订单为已创建状态。', level=messages.SUCCESS)

    @admin.action(description='重发 MTProxy 链接给用户')
    def resend_mtproxy_links(self, request, queryset):
        success = 0
        failed = 0
        for order in queryset:
            if not order.mtproxy_link or not order.user_id or not getattr(order.user, 'tg_user_id', None):
                failed += 1
                continue
            ok, _ = _send_telegram_message(order.user.tg_user_id, f'🔁 MTProxy 链接已重新发送\n\n{order.mtproxy_link}')
            if ok:
                success += 1
            else:
                failed += 1
        if success:
            self.message_user(request, f'成功重发 {success} 条代理链接。', level=messages.SUCCESS)
        if failed:
            self.message_user(request, f'有 {failed} 条代理链接重发失败。', level=messages.WARNING)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_no', 'user', 'product_name', 'quantity', 'currency', 'total_amount', 'pay_amount', 'pay_method', 'status', 'created_at')
    list_filter = ('status', 'currency', 'pay_method', 'created_at')
    search_fields = ('order_no', 'product_name', 'tx_hash', 'user__tg_user_id', 'user__username')
    readonly_fields = ('order_no', 'created_at', 'tx_hash', 'pay_amount', 'total_amount')
    ordering = ('-id',)
    list_per_page = 50
    fieldsets = (
        ('订单信息', {'fields': ('order_no', 'user', 'product', 'product_name', 'quantity', 'status')}),
        ('支付信息', {'fields': ('currency', 'total_amount', 'pay_amount', 'pay_method', 'tx_hash', 'paid_at', 'expired_at')}),
        ('时间信息', {'fields': ('created_at',)}),
    )
