from django.http import JsonResponse
from django.contrib.auth import logout
from django.shortcuts import redirect


class SessionPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            if request.session.get("version") != request.user.session_version:
                logout(request)
            elif request.user.must_change_password:
                allowed = ("/api/me/", "/api/csrf/", "/api/password/", "/api/logout/")
                if request.path.startswith("/api/") and request.path not in allowed:
                    response = JsonResponse({"detail": "请先修改初始密码。", "code": "password_change_required"}, status=403)
                    response["Cache-Control"] = "no-store"
                    return response
                if request.path.startswith("/admin/"):
                    return redirect("/password")
        response = self.get_response(request)
        if request.path.startswith(("/api/", "/admin/")):
            response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        response["X-Content-Type-Options"] = "nosniff"
        return response
