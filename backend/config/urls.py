from django.urls import path, re_path

from portal import integration, views
from portal.admin import site

urlpatterns = [
    path("admin/", site.urls),
    path("api/csrf/", views.csrf),
    path("api/login/", views.sign_in),
    path("api/logout/", views.sign_out),
    path("api/me/", views.me),
    path("api/password/", views.change_password),
    path("api/modules/", views.modules),
    path("api/modules/<slug:code>/", views.module_detail),
    path("api/modules/<slug:code>/launch/", views.launch),
    path("api/business/summary/", integration.summary),
    path("api/integration/redeem/", integration.redeem),
    path("health/", views.health),
    re_path(r"^(?P<path>(?!api(?:/|$)|admin(?:/|$)|static(?:/|$)).*)$", views.frontend),
]
