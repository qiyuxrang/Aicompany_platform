import json
import re

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .admin import ManagedAdmin, site
from .models import GatewayModel, ModelCallLog, ModelRoute, ModelRouteOption, Provider, User
from .security import audit


MAX_MODEL_IMPORT_BYTES = 64 * 1024
MAX_MODEL_IMPORT_COUNT = 100
MODEL_IMPORT_KEYS = {
    "name", "provider_code", "model_name", "capabilities", "timeout_seconds",
    "max_output_tokens", "token_parameter",
}
MODEL_IMPORT_AUDIT_FIELDS = (
    "name", "provider", "model_name", "supports_text", "supports_vision", "enabled",
    "timeout_seconds", "max_output_tokens", "token_parameter",
)
PLAINTEXT_CREDENTIAL_RE = re.compile(
    r"(?:sk-(?:ant-)?[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{20,})",
    re.IGNORECASE,
)
MODEL_IMPORT_TEMPLATE = {
    "models": [{
        "name": "示例文本模型",
        "provider_code": "example-provider",
        "model_name": "example-chat-model",
        "capabilities": ["text"],
        "timeout_seconds": 20,
        "max_output_tokens": 1024,
        "token_parameter": "max_tokens",
    }],
}


class InvalidModelImport(Exception):
    pass


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise InvalidModelImport
        value[key] = item
    return value


def _reject_json_constant(value):
    raise InvalidModelImport


def _parse_model_import(upload):
    if getattr(upload, "size", 0) > MAX_MODEL_IMPORT_BYTES:
        raise InvalidModelImport
    content = upload.read(MAX_MODEL_IMPORT_BYTES + 1)
    if len(content) > MAX_MODEL_IMPORT_BYTES:
        raise InvalidModelImport
    try:
        document = json.loads(content.decode("utf-8-sig"), object_pairs_hook=_unique_json_object,
                              parse_constant=_reject_json_constant)
    except (InvalidModelImport, UnicodeError, ValueError, RecursionError):
        raise InvalidModelImport from None
    if not isinstance(document, dict) or set(document) != {"models"}:
        raise InvalidModelImport
    configs = document["models"]
    if not isinstance(configs, list) or not 1 <= len(configs) <= MAX_MODEL_IMPORT_COUNT:
        raise InvalidModelImport
    identities = set()
    for config in configs:
        if not isinstance(config, dict) or set(config) != MODEL_IMPORT_KEYS:
            raise InvalidModelImport
        strings = (config["name"], config["provider_code"], config["model_name"], config["token_parameter"])
        if any(not isinstance(value, str) or not value or value != value.strip() for value in strings):
            raise InvalidModelImport
        if any("://" in config[field] or "PORTAL_MODEL_KEY_" in config[field]
                or "-----BEGIN " in config[field] or PLAINTEXT_CREDENTIAL_RE.search(config[field])
                for field in ("name", "model_name")):
            raise InvalidModelImport
        capabilities = config["capabilities"]
        if (not isinstance(capabilities, list) or not capabilities
                or any(not isinstance(item, str) for item in capabilities)
                or len(capabilities) != len(set(capabilities))
                or not set(capabilities) <= {"text", "vision"}
                or "text" not in capabilities):
            raise InvalidModelImport
        if type(config["timeout_seconds"]) is not int or type(config["max_output_tokens"]) is not int:
            raise InvalidModelImport
        identity = (config["provider_code"], config["model_name"])
        if identity in identities:
            raise InvalidModelImport
        identities.add(identity)
    return configs


class GatewayModelImportForm(forms.Form):
    config_file = forms.FileField(
        label="模型配置 JSON",
        help_text=f"仅接受 UTF-8 JSON，最多 {MAX_MODEL_IMPORT_COUNT} 个模型、{MAX_MODEL_IMPORT_BYTES // 1024} KB。",
        widget=forms.FileInput(attrs={"accept": ".json,application/json"}),
    )

    def clean_config_file(self):
        upload = self.cleaned_data["config_file"]
        if upload.size > MAX_MODEL_IMPORT_BYTES:
            raise forms.ValidationError(f"JSON 文件不能超过 {MAX_MODEL_IMPORT_BYTES // 1024} KB。")
        return upload


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


class ModelRouteForm(forms.ModelForm):
    max_calls_per_minute = forms.IntegerField(
        label="单用户每分钟调用上限", required=False, initial=10, min_value=1, max_value=120,
    )

    class Meta:
        model = ModelRoute
        fields = ("code", "name", "module", "model", "max_calls_per_minute", "enabled")

    def clean_max_calls_per_minute(self):
        return self.cleaned_data.get("max_calls_per_minute") or 10


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
    change_list_template = "admin/portal/gatewaymodel/change_list.html"
    fields = ("name", "provider", "model_name", "supports_text", "supports_vision", "enabled", "timeout_seconds", "max_output_tokens", "token_parameter")
    list_display = ("id", "name", "provider", "supports_text", "supports_vision", "enabled")
    list_filter = ("enabled", "supports_text")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "provider":
            field.label_from_instance = lambda instance: f"{instance.name}（编号 {instance.pk}）"
        return field

    def get_urls(self):
        return [
            path("catalog/", self.admin_site.admin_view(require_http_methods(("GET", "POST"))(self.catalog_view)),
                name="portal_gatewaymodel_catalog"),
            path("import/template.json", self.admin_site.admin_view(require_GET(self.import_template_view)),
                name="portal_gatewaymodel_import_template"),
            path("import/", self.admin_site.admin_view(require_http_methods(("GET", "POST"))(self.import_view)),
                name="portal_gatewaymodel_import"),
            path("<int:object_id>/test-connection/",
                self.admin_site.admin_view(require_POST(self.test_connection_view)),
                name="portal_gatewaymodel_test_connection"),
        ] + super().get_urls()

    def import_template_view(self, request):
        self._import_actor(request)
        response = HttpResponse(json.dumps(MODEL_IMPORT_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
                                content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="gateway-models-template.json"'
        return response

    def import_view(self, request):
        actor = self._import_actor(request)
        form = GatewayModelImportForm(
            request.POST if request.method == "POST" else None,
            request.FILES if request.method == "POST" else None,
        )
        if request.method == "POST" and form.is_valid():
            try:
                configs = _parse_model_import(form.cleaned_data["config_file"])
                imported = self._import_models(request, actor, configs)
            except (InvalidModelImport, ValidationError):
                form.add_error("config_file", "配置文件不符合格式要求，未导入任何模型。")
            else:
                self.message_user(request, f"已导入 {len(imported)} 个网关模型，均保持停用。", messages.SUCCESS)
                return redirect("admin:portal_gatewaymodel_changelist")
        context = {
            **self.admin_site.each_context(request),
            "opts": self.localized_options(),
            "title": "导入网关模型配置",
            "form": form,
            "media": self.media + form.media,
            "changelist_url": reverse("admin:portal_gatewaymodel_changelist"),
            "template_url": reverse("admin:portal_gatewaymodel_import_template"),
        }
        return TemplateResponse(request, "admin/portal/gatewaymodel/import_form.html", context)

    def catalog_view(self, request):
        actor = self._import_actor(request)
        providers = Provider.objects.filter(enabled=True, protocol=Provider.Protocol.OPENAI_CHAT).order_by("name", "pk")
        provider = None
        available = []
        total = 0
        if request.method == "POST":
            provider_id = request.POST.get("provider_id", "")
            action = request.POST.get("action")
            if (set(request.POST) - {"csrfmiddlewaretoken", "provider_id", "action", "model_ids"}
                    or not provider_id.isdecimal() or len(provider_id) > 20 or action not in ("fetch", "import")):
                raise PermissionDenied("模型目录请求参数不合法。")
            provider = get_object_or_404(providers, pk=int(provider_id))
            from .model_gateway import GatewayError, fetch_model_catalog

            try:
                catalog = fetch_model_catalog(provider)
            except GatewayError as error:
                self.message_user(request, error.message, level=messages.ERROR)
            else:
                total = len(catalog)
                existing = set(GatewayModel.objects.filter(provider=provider, model_name__in=catalog)
                               .values_list("model_name", flat=True))
                available = [name for name in catalog if name not in existing]
                if action == "import":
                    selected = request.POST.getlist("model_ids")
                    if (not selected or len(selected) > MAX_MODEL_IMPORT_COUNT or len(selected) != len(set(selected))
                            or not set(selected) <= set(available)):
                        self.message_user(request, "请选择最多 100 个尚未导入的模型。", level=messages.ERROR)
                    else:
                        configs = [{"name": name[:100], "provider_code": provider.code, "model_name": name,
                                    "capabilities": ["text"], "timeout_seconds": 20,
                                    "max_output_tokens": 1024, "token_parameter": "max_tokens"} for name in selected]
                        try:
                            self._import_models(request, actor, configs)
                        except (InvalidModelImport, ValidationError):
                            self.message_user(request, "模型目录已变化或配置不符合要求，未导入。", level=messages.ERROR)
                        else:
                            self.message_user(request, f"已导入 {len(configs)} 个模型，均保持停用；请逐个核对能力和连接。", messages.SUCCESS)
                            return redirect("admin:portal_gatewaymodel_changelist")
        return TemplateResponse(request, "admin/portal/gatewaymodel/catalog.html", {
            **self.admin_site.each_context(request),
            "opts": self.localized_options(), "title": "获取服务商模型目录",
            "providers": providers, "provider": provider, "available": available, "total": total,
            "changelist_url": reverse("admin:portal_gatewaymodel_changelist"),
        })

    @staticmethod
    def _import_actor(request):
        actor = User.objects.filter(pk=request.user.pk).first()
        if not actor or not actor.is_platform_admin or actor.must_change_password:
            raise PermissionDenied("仅已完成改密的平台管理员可导入模型配置。")
        return actor

    def _import_models(self, request, actor, configs):
        with transaction.atomic():
            provider_codes = {config["provider_code"] for config in configs}
            providers = {
                provider.code: provider
                for provider in Provider.objects.select_for_update().filter(code__in=provider_codes).order_by("pk")
            }
            if set(providers) != provider_codes:
                raise InvalidModelImport
            identities = {(providers[config["provider_code"]].pk, config["model_name"]) for config in configs}
            existing = set(GatewayModel.objects.filter(
                provider_id__in={identity[0] for identity in identities},
                model_name__in={identity[1] for identity in identities},
            ).values_list("provider_id", "model_name"))
            if identities & existing:
                raise InvalidModelImport
            models = []
            for config in configs:
                capabilities = set(config["capabilities"])
                model = GatewayModel(
                    name=config["name"],
                    provider=providers[config["provider_code"]],
                    model_name=config["model_name"],
                    supports_text="text" in capabilities,
                    supports_vision="vision" in capabilities,
                    enabled=False,
                    timeout_seconds=config["timeout_seconds"],
                    max_output_tokens=config["max_output_tokens"],
                    token_parameter=config["token_parameter"],
                )
                model.full_clean()
                models.append(model)
            for model in models:
                model.save()
                audit(actor, "gatewaymodel_import", model.pk, changes=list(MODEL_IMPORT_AUDIT_FIELDS))
                self.log_addition(request, model, [{"added": {
                    "name": self.admin_label,
                    "object": str(model),
                    "fields": ", ".join(MODEL_IMPORT_AUDIT_FIELDS),
                }}])
            return models

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
    form = ModelRouteForm
    fields = ("code", "name", "module", "model", "max_calls_per_minute", "enabled")
    list_display = ("id", "name", "code", "module", "model", "max_calls_per_minute", "enabled")
    list_filter = ("enabled", "module")
    inlines = ()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "model":
            field.label_from_instance = lambda instance: f"{instance.name}（编号 {instance.pk}）"
        return field


class ModelRouteOptionInline(admin.TabularInline):
    model = ModelRouteOption
    extra = 0
    fields = ("model", "allowed_roles", "allowed_users", "enabled", "display_order")
    filter_horizontal = ("allowed_roles", "allowed_users")
    verbose_name = "可选模型授权"
    verbose_name_plural = "可选模型授权（角色和用户均为空时，对具有模块权限的用户开放）"
    can_delete = False


ModelRouteAdmin.inlines = (ModelRouteOptionInline,)


@admin.register(ModelCallLog, site=site)
class ModelCallLogAdmin(ManagedAdmin):
    admin_label = admin_label_plural = "模型调用日志"
    list_display = ("id", "created_at", "actor", "route", "model", "purpose", "status_label", "duration_ms")
    list_filter = ("purpose",)
    readonly_fields = ("created_at", "actor", "route", "model", "model_public_id", "config_version",
                       "purpose", "status_label", "duration_ms", "prompt_tokens", "completion_tokens")
    fields = readonly_fields

    @admin.display(description="调用状态", ordering="status")
    def status_label(self, obj):
        from .model_gateway import ERROR_MESSAGES

        return {"pending": "处理中", "success": "成功", **ERROR_MESSAGES}.get(obj.status, "调用失败")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
