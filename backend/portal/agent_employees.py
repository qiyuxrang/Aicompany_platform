from django.db import transaction
from django.http import Http404
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .agent_api import actor, body, endpoint, fail, page, string
from .models import Role, User
from .security import audit


DEPARTMENTS = {"product", "hr", "finance", "engineering"}


def department(data):
    value = string(data, "department_code", 20)
    if value not in DEPARTMENTS:
        fail()
    return value


def ordinary_users():
    return User.objects.filter(is_staff=False, is_superuser=False).exclude(
        roles__code__in=("platform_admin", "general_manager"))


def employee_data(user):
    return {"id": user.pk, "username": user.username, "display_name": user.display_name,
            "department_code": user.department_code, "is_active": user.is_active,
            "roles": list(user.roles.values_list("code", flat=True))}


@api_view(["GET", "POST"])
@endpoint
def employees(request):
    manager = actor(request, manager=True)
    if request.method == "GET":
        offset, size = page(request)
        query = ordinary_users().order_by("id")
        return Response({"items": [employee_data(user) for user in query[offset:offset + size]],
                         "total": query.count()})
    data = body(request, {"username", "department_code"}, {"display_name"})
    username = string(data, "username", 150)
    display_name = string(data, "display_name", 80, blank=True) if "display_name" in data else ""
    code = department(data)
    if User.objects.filter(username=username).exists():
        fail("conflict", "账号已存在。", 409)
    with transaction.atomic():
        user = User(username=username, display_name=display_name, department_code=code,
                    is_active=False, must_change_password=True, is_staff=False, is_superuser=False)
        user.set_unusable_password()
        user.save()
        if code != "finance":
            user.roles.add(Role.objects.get(code=code))
        audit(manager, "agent_employee_create", user.pk, changes=["username", "department_code"])
    return Response(employee_data(user), status=201)


@api_view(["PATCH"])
@endpoint
def employee_detail(request, user_id):
    manager = actor(request, manager=True)
    data = body(request, set(), {"display_name", "department_code", "is_active"})
    if not data:
        fail()
    code = department(data) if "department_code" in data else None
    display_name = string(data, "display_name", 80, blank=True) if "display_name" in data else None
    active = data.get("is_active")
    if "is_active" in data and type(active) is not bool:
        fail()
    with transaction.atomic():
        user = ordinary_users().select_for_update().filter(pk=user_id).first()
        if user is None:
            raise Http404
        if code is not None and code != user.department_code:
            user.department_code = code
            user.roles.set(Role.objects.filter(code=code))
        if display_name is not None:
            user.display_name = display_name
        if active is not None:
            user.is_active = active
        user.save(update_fields=["department_code", "display_name", "is_active"])
        audit(manager, "agent_employee_change", user.pk, changes=list(data))
    return Response(employee_data(user))


urlpatterns = [path("employees/", employees), path("employees/<int:user_id>/", employee_detail)]
