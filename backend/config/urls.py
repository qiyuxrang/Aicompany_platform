from django.urls import include, path, re_path

from portal import integration, operations, views, work_summary
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
    path("api/ops/overview/", operations.ops_overview),
    path("api/ops/users/", operations.ops_users),
    path("api/ops/users/<int:user_id>/", operations.ops_user_detail),
    path("api/ops/usage/", operations.ops_usage),
    path("api/ops/modules/", operations.ops_modules),
    path("api/ops/modules/<slug:code>/check/", operations.ops_module_check),
    path("api/ops/issues/", operations.ops_issues),
    path("api/ops/issues/<int:issue_id>/", operations.ops_issue_detail),
    path("api/ops/maintenance/", operations.ops_maintenance),
    path("api/business/summary/", integration.summary),
    path("api/work/summary/", work_summary.summary),
    path("api/integration/redeem/", integration.redeem),
    path("api/product/", include("portal.product_source_api")),
    path("api/product/", include("portal.product_workspace")),
    path("api/product/", include("portal.product_outputs")),
    path("api/product/", include("portal.product_history")),
    path("api/product/", include("portal.product_api")),
    path("api/hr/", include("portal.hr_api")),
    path("health/", views.health),
    re_path(r"^(?P<path>(?!api(?:/|$)|admin(?:/|$)|static(?:/|$)).*)$", views.frontend),
]
