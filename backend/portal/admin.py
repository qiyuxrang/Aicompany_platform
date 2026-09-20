from django import forms
from django.contrib import admin
from django.contrib.auth import logout
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import redirect

from .models import AuditEvent, BusinessMapping, Module, Role, User
from .security import audit


class PortalAdminSite(admin.AdminSite):
    site_header = "企业协同平台 · 管理"
    site_title = "平台管理"
    index_title = "账号、授权与接入配置"

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


site = PortalAdminSite(name="admin")


class ManagedAdmin(admin.ModelAdmin):
    actions = None

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
    form = PortalUserChangeForm
    add_form = PortalUserCreateForm
    change_password_form = ResetPasswordForm
    fieldsets = ((None, {"fields": ("username", "password", "display_name", "is_active", "roles", "must_change_password")}),)
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("username", "display_name", "password1", "password2", "roles")}),)
    readonly_fields = ("must_change_password",)
    list_display = ("username", "display_name", "is_active", "must_change_password")
    list_filter = ("is_active", "roles")
    filter_horizontal = ("roles",)
    ordering = ("username",)
    search_fields = ("username", "display_name")

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
            return response


@admin.register(Role, site=site)
class RoleAdmin(ManagedAdmin):
    fields = ("code", "name", "modules")
    readonly_fields = ("code",)
    filter_horizontal = ("modules",)

    def has_add_permission(self, request):
        return False


@admin.register(Module, site=site)
class ModuleAdmin(ManagedAdmin):
    readonly_fields = ("code",)
    fields = ("code", "name", "description", "url", "status", "enabled")
    list_display = ("name", "status", "enabled")

    def has_add_permission(self, request):
        return False


@admin.register(BusinessMapping, site=site)
class MappingAdmin(ManagedAdmin):
    fields = ("user", "external_user_id", "enabled")
    list_display = ("id", "user", "enabled")


@admin.register(AuditEvent, site=site)
class AuditAdmin(ManagedAdmin):
    list_display = ("created_at", "actor", "action", "target", "result")
    list_filter = ("action", "result")
    search_fields = ("target", "actor__username")
    readonly_fields = ("created_at", "actor", "action", "target", "result", "changes")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
