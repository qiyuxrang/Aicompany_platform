from copy import copy

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import logout
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import redirect

from .models import AuditEvent, BusinessLedgerGrant, BusinessMapping, Module, Role, User
from .security import audit


class PortalAdminSite(admin.AdminSite):
    site_header = "企业协同平台 · 管理"
    site_title = "平台管理"
    index_title = "账号、授权与接入配置"

    operations_links = {
        "/admin/portal/user/": ("/ops/people", "返回人员与权限"),
        "/admin/portal/role/": ("/ops/people", "返回人员与权限"),
        "/admin/portal/businessmapping/": ("/ops/people", "返回人员与权限"),
        "/admin/portal/module/": ("/ops/modules", "返回模块与接入管理"),
        "/admin/portal/auditevent/": ("/ops/maintenance?tab=audit", "返回审计记录"),
    }

    def has_permission(self, request):
        return (request.user.is_authenticated and request.user.is_platform_admin
                and not request.user.must_change_password)

    def login(self, request, extra_context=None):
        if request.user.is_authenticated:
            if request.user.must_change_password:
                return redirect("/password")
            raise PermissionDenied("仅平台管理员可访问管理后台。")
        return redirect("/login")

    def logout(self, request, extra_context=None):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if request.user.is_authenticated:
            audit(request.user, "logout", request.user.pk)
        logout(request)
        return redirect("/login")

    def password_change(self, request, extra_context=None):
        return redirect("/password")

    def each_context(self, request):
        context = super().each_context(request)
        target = next(
            (link for prefix, link in self.operations_links.items() if request.path.startswith(prefix)),
            ("/ops", "返回运维总览"),
        )
        context["operations_return_url"], context["operations_return_label"] = target
        return context

    def get_app_list(self, request, app_label=None):
        app_list = super().get_app_list(request, app_label)
        descriptions = {
            "User": (0, "人员", "创建个人账号，调整角色、启停状态与重置密码。", "管理账号", "新增账号"),
            "Role": (1, "授权", "维护角色对应的模块权限，账号可分配多个角色。", "配置角色权限", "新增角色"),
            "Module": (2, "入口", "维护业务入口地址、接入状态与启停配置。", "配置模块入口", "新增模块"),
            "BusinessMapping": (3, "身份", "关联平台账号与旧系统用户标识；建立映射不等于完成单点登录。", "管理账号映射", "新增映射"),
            "AuditEvent": (4, "留痕", "只读查询登录、账号变更与权限调整记录，不可修改或删除。", "查看审计记录", "新增记录"),
            "BusinessLedgerGrant": (5, "授权", "按账号和台账授予录入、提交或发布权限；管理员不因此获得台账正文访问权。", "管理台账授权", "新增台账授权"),
            "Provider": (6, "模型", "维护兼容接口及独立 FastAPI 服务的密钥环境变量引用；Key 不入库。", "管理模型服务商", "新增服务商"),
            "GatewayModel": (7, "模型", "配置已保存的模型参数；连接测试仅发送固定短文本，可能产生费用。", "管理网关模型", "新增模型"),
            "ModelRoute": (8, "业务", "按业务模块配置模型路由，新增配置默认停用。", "管理业务路由", "新增路由"),
            "ModelCallLog": (9, "留痕", "只读查看调用状态、耗时与令牌计数，不记录输入或输出内容。", "查看调用日志", "新增记录"),
        }
        labels = {
            model._meta.object_name: model_admin.admin_label_plural
            for model, model_admin in self._registry.items()
            if isinstance(model_admin, ManagedAdmin)
        }
        for app in app_list:
            for model in app["models"]:
                model["name"] = labels.get(model["object_name"], model["name"])
                order, category, description, action, add_action = descriptions.get(
                    model["object_name"], (99, "管理", "查看与维护已授权的配置。", "查看记录", "新增记录"),
                )
                model.update(order=order, category=category, description=description, action_label=action, add_label=add_action)
            app["models"].sort(key=lambda model: (model["order"], model["name"]))
        return app_list


site = PortalAdminSite(name="admin")


class ManagedAdmin(admin.ModelAdmin):
    actions = None
    admin_label = "记录"
    admin_label_plural = "记录"
    form_labels = {}
    form_help_texts = {}

    class Media:
        js = ("portal/admin-zh.js",)

    def localized_options(self):
        options = copy(self.opts)
        options.verbose_name = self.admin_label
        options.verbose_name_plural = self.admin_label_plural
        return options

    def has_module_permission(self, request):
        return site.has_permission(request)

    def has_view_permission(self, request, obj=None):
        return site.has_permission(request)

    def has_change_permission(self, request, obj=None):
        return site.has_permission(request)

    def has_add_permission(self, request):
        return site.has_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        for field_name, label in self.form_labels.items():
            if field_name in form.base_fields:
                form.base_fields[field_name].label = label
        for field_name, help_text in self.form_help_texts.items():
            if field_name in form.base_fields:
                form.base_fields[field_name].help_text = help_text
        return form

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        field = super().formfield_for_manytomany(db_field, request, **kwargs)
        if field and db_field.name in self.form_help_texts:
            field.help_text = self.form_help_texts[db_field.name]
        return field

    def get_changelist_instance(self, request):
        changelist = super().get_changelist_instance(request)
        changelist.opts = self.localized_options()
        if changelist.is_popup:
            changelist.title = f"选择{self.admin_label}"
        elif self.has_change_permission(request):
            changelist.title = f"选择要修改的{self.admin_label}"
        else:
            changelist.title = f"选择要查看的{self.admin_label}"
        return changelist

    def changelist_view(self, request, extra_context=None):
        context = {"module_name": self.admin_label_plural, **(extra_context or {})}
        return super().changelist_view(request, context)

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        action = "新增" if add else "修改" if change else "查看"
        context["title"] = f"{action}{self.admin_label}"
        response = super().render_change_form(request, context, add, change, form_url, obj)
        response.context_data["opts"] = self.localized_options()
        return response

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        fields = [field for field in form.changed_data if "password" not in field]
        audit(request.user, f"{self.model._meta.model_name}_{'change' if change else 'create'}", form.instance.pk, changes=fields)


class PortalUserCreateForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "display_name", "roles")


class PortalUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = ("username", "display_name", "is_active", "roles")


class ResetPasswordForm(AdminPasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("usable_password", None)
        self.fields["password1"].required = True
        self.fields["password2"].required = True

    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = True
        user.session_version += 1
        if commit:
            user.save()
        return user


@admin.register(User, site=site)
class PortalUserAdmin(ManagedAdmin, UserAdmin):
    admin_label = "账号"
    admin_label_plural = "账号"
    form_labels = {
        "username": "用户名",
        "password": "密码",
        "display_name": "显示名称",
        "is_active": "启用账号",
        "roles": "角色",
    }
    form_help_texts = {
        "roles": "左侧为可选角色，右侧为已分配角色；可双击或使用箭头调整。",
    }
    form = PortalUserChangeForm
    add_form = PortalUserCreateForm
    change_password_form = ResetPasswordForm
    add_form_template = "admin/portal/user/add_form.html"
    change_user_password_template = "admin/portal/user/change_password.html"
    fieldsets = ((None, {"fields": ("username", "password", "display_name", "is_active", "roles", "password_change_required")}),)
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("username", "display_name", "password1", "password2", "roles")}),)
    readonly_fields = ("password_change_required",)
    list_display = ("username", "display_name", "is_active", "password_change_required")
    list_filter = ("is_active", "roles")
    filter_horizontal = ("roles",)
    ordering = ("username",)
    search_fields = ("username", "display_name")

    @admin.display(boolean=True, description="首次登录须改密")
    def password_change_required(self, obj):
        return obj.must_change_password

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)

        class GuardedForm(base):
            def clean(self):
                cleaned = super().clean()
                if obj and obj.pk == request.user.pk:
                    roles = cleaned.get("roles")
                    if not cleaned.get("is_active") or roles is not None and not roles.filter(code="platform_admin").exists():
                        raise forms.ValidationError("不能停用本人或移除本人的平台管理员角色。")
                return cleaned

        return GuardedForm

    def save_model(self, request, obj, form, change):
        if not change:
            obj.must_change_password = True
            obj.is_superuser = False
            obj.is_staff = False
        super().save_model(request, obj, form, change)
        obj.refresh_from_db()

    def response_add(self, request, obj, post_url_continue=None):
        response = super().response_add(request, obj, post_url_continue)
        messages.info(
            request,
            f"账号“{obj.username}”已创建并分配 {obj.roles.count()} 个角色；首次登录必须修改初始密码。",
        )
        return response

    def user_change_password(self, request, id, form_url=""):
        with transaction.atomic():
            try:
                user = User.objects.select_for_update().filter(pk=id).first()
            except (ValueError, TypeError) as error:
                raise Http404 from error
            if user and user.pk == request.user.pk:
                return redirect("/password")
            response = super().user_change_password(request, id, form_url)
            if (request.method == "POST" and user and response.status_code == 302
                    and User.objects.filter(pk=user.pk, session_version__gt=user.session_version).exists()):
                audit(request.user, "password_reset", id)
                messages.info(request, "临时密码已重置；该账号下次登录必须修改密码，旧会话已失效。")
            if hasattr(response, "context_data"):
                response.context_data["opts"] = self.localized_options()
            return response


@admin.register(Role, site=site)
class RoleAdmin(ManagedAdmin):
    admin_label = "角色"
    admin_label_plural = "角色"
    form_labels = {"name": "角色名称", "modules": "模块授权"}
    form_help_texts = {
        "modules": "左侧为可授权模块，右侧为已授权模块；可双击或使用箭头调整。",
    }
    fields = ("role_code", "name", "modules")
    readonly_fields = ("role_code",)
    list_display = ("name", "role_code")
    filter_horizontal = ("modules",)

    @admin.display(description="角色编码", ordering="code")
    def role_code(self, obj):
        return obj.code

    def has_add_permission(self, request):
        return False


@admin.register(Module, site=site)
class ModuleAdmin(ManagedAdmin):
    admin_label = "模块"
    admin_label_plural = "模块"
    readonly_fields = ("module_code",)
    fields = ("module_code", "name", "description", "url", "status", "enabled")
    list_display = ("name", "status", "enabled")

    @admin.display(description="模块编码", ordering="code")
    def module_code(self, obj):
        return obj.code

    def has_add_permission(self, request):
        return False


@admin.register(BusinessMapping, site=site)
class MappingAdmin(ManagedAdmin):
    admin_label = "旧系统账号映射"
    admin_label_plural = "旧系统账号映射"
    fields = ("user", "external_user_id", "enabled")
    list_display = ("mapping_id", "user", "enabled")

    @admin.display(description="编号", ordering="id")
    def mapping_id(self, obj):
        return obj.pk


@admin.register(BusinessLedgerGrant, site=site)
class BusinessLedgerGrantAdmin(ManagedAdmin):
    admin_label = "台账授权"
    admin_label_plural = "台账授权"
    fields = ("user", "department", "can_edit", "can_submit", "can_publish")
    list_display = ("user", "department", "can_edit", "can_submit", "can_publish")
    list_filter = ("department", "can_edit", "can_submit", "can_publish")
    search_fields = ("user__username", "user__display_name")
    form_labels = {
        "user": "账号", "department": "台账", "can_edit": "允许录入与编辑",
        "can_submit": "允许提交", "can_publish": "允许发布与退回",
    }

    def has_delete_permission(self, request, obj=None):
        return site.has_permission(request)

    def delete_model(self, request, obj):
        target = f'{obj.user_id}:{obj.department}'
        super().delete_model(request, obj)
        audit(request.user, 'businessledgergrant_delete', target, changes=['grant'])


@admin.register(AuditEvent, site=site)
class AuditAdmin(ManagedAdmin):
    admin_label = "审计事件"
    admin_label_plural = "审计事件"
    list_display = ("created_at", "actor", "action", "target", "result")
    list_filter = ("action", "result")
    search_fields = ("target", "actor__username")
    readonly_fields = ("created_at", "actor", "action", "target", "result", "changes")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


from . import model_admin
