import re

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.views.decorators.http import require_POST

from .admin import ManagedAdmin, site
from .models import GatewayModel, ModelCallLog, ModelRoute, Provider, User


class KeyEnvironmentWidget(forms.TextInput):
    def format_value(self, value):
        if value and not re.fullmatch(r"PORTAL_MODEL_KEY_[A-Z0-9_]+", str(value)):
            return None
        return super().format_value(value)


class ProviderForm(forms.ModelForm):
    class Meta:
        model = Provider
        fields = ("code", "name", "protocol", "base_url", "api_key_env", "enabled")
        widgets = {"api_key_env": KeyEnvironmentWidget()}


@admin.register(Provider, site=site)
class ProviderAdmin(ManagedAdmin):
    admin_label = admin_label_plural = "模型服务商"
    form = ProviderForm
    fields = ("code", "name", "protocol", "base_url", "api_key_env", "enabled")
    list_display = ("id", "name", "code", "protocol", "enabled")
    list_filter = ("enabled", "protocol")


@admin.register(GatewayModel, site=site)
class GatewayModelAdmin(ManagedAdmin):
    admin_label = admin_label_plural = "网关模型"
    change_form_template = "admin/portal/gatewaymodel/change_form.html"
    fields = ("name", "provider", "model_name", "supports_text", "enabled", "timeout_seconds", "max_output_tokens", "token_parameter")
    list_display = ("id", "name", "provider", "supports_text", "enabled")
    list_filter = ("enabled", "supports_text")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "provider":
            field.label_from_instance = lambda instance: f"{instance.name}（编号 {instance.pk}）"
        return field

    def get_urls(self):
        return [path("<int:object_id>/test-connection/",
            self.admin_site.admin_view(require_POST(self.test_connection_view)),
            name="portal_gatewaymodel_test_connection")] + super().get_urls()

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        actor = User.objects.filter(pk=request.user.pk).first()
        context["can_test_connection"] = bool(obj and actor and actor.is_platform_admin and not actor.must_change_password)
        if context["can_test_connection"]:
            context["test_connection_url"] = reverse("admin:portal_gatewaymodel_test_connection", args=[obj.pk])
        return super().render_change_form(request, context, add, change, form_url, obj)

    def test_connection_view(self, request, object_id):
        actor = User.objects.filter(pk=request.user.pk).first()
        if not actor or not actor.is_platform_admin or actor.must_change_password:
            raise PermissionDenied("仅已完成改密的平台管理员可测试连接。")
        model = get_object_or_404(GatewayModel, pk=object_id)
        change_url = reverse("admin:portal_gatewaymodel_change", args=[model.pk])
        if request.POST.get("confirm_cost") != "on":
            self.message_user(request, "请先确认测试可能产生费用，且仅发送固定短测试文本。", level=messages.ERROR)
            return redirect(change_url)
        if set(request.POST) - {"csrfmiddlewaretoken", "confirm_cost"}:
            raise PermissionDenied("测试仅接受已保存配置，不接受自定义输入。")
        from .model_gateway import GatewayError, test_connection

        try:
            result = test_connection(actor, model.pk)
        except GatewayError as error:
            self.message_user(request, error.message, level=messages.ERROR)
        else:
            self.message_user(request, f"连接测试成功，耗时 {int(result['duration_ms'])} 毫秒；不展示模型输出。", level=messages.SUCCESS)
        return redirect(change_url)


@admin.register(ModelRoute, site=site)
class ModelRouteAdmin(ManagedAdmin):
    admin_label = admin_label_plural = "业务模型路由"
    fields = ("code", "name", "module", "model", "enabled")
    list_display = ("id", "name", "code", "module", "model", "enabled")
    list_filter = ("enabled", "module")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "model":
            field.label_from_instance = lambda instance: f"{instance.name}（编号 {instance.pk}）"
        return field


@admin.register(ModelCallLog, site=site)
class ModelCallLogAdmin(ManagedAdmin):
    admin_label = admin_label_plural = "模型调用日志"
    list_display = ("id", "created_at", "actor", "route", "model", "purpose", "status_label", "duration_ms")
    list_filter = ("purpose",)
    readonly_fields = ("created_at", "actor", "route", "model", "purpose", "status_label", "duration_ms", "prompt_tokens", "completion_tokens")
    fields = readonly_fields

    @admin.display(description="调用状态", ordering="status")
    def status_label(self, obj):
        from .model_gateway import ERROR_MESSAGES

        return {"pending": "处理中", "success": "成功", **ERROR_MESSAGES}.get(obj.status, "调用失败")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
