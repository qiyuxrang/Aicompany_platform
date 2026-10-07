"""Security boundaries required from the locked REST framework version."""
from django.core.exceptions import RequestDataTooBig
from django.test import SimpleTestCase, override_settings
from rest_framework.permissions import BasePermission
from rest_framework.renderers import AdminRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView


class BodyEcho(APIView):
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        return Response(request.data)


class WriteOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in {"POST", "OPTIONS"}


class WriteOnlyAdmin(APIView):
    authentication_classes = ()
    permission_classes = (WriteOnly,)
    renderer_classes = (AdminRenderer, JSONRenderer)
    get_calls = 0

    def post(self, request):
        return Response({"field": ["Invalid synthetic input"]}, status=400)

    def get(self, request):
        type(self).get_calls += 1
        return Response({"secret": "SYNTHETIC_GET_ONLY_SECRET"})


class FrameworkSecurityTests(SimpleTestCase):
    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=32)
    def test_json_and_form_request_data_obey_body_limit(self):
        factory = APIRequestFactory()
        for content_type, body in (
            ("application/json", '{"value":"' + "A" * 100 + '"}'),
            ("application/x-www-form-urlencoded", "value=" + "A" * 100),
        ):
            with self.subTest(content_type=content_type):
                with self.assertRaises(RequestDataTooBig):
                    BodyEcho.as_view()(factory.post("/synthetic/", body, content_type=content_type))
        response = BodyEcho.as_view()(factory.post("/synthetic/", {"value": "ok"}, format="json"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"value": "ok"})

    def test_invalid_admin_write_cannot_render_get_protected_data(self):
        WriteOnlyAdmin.get_calls = 0
        response = WriteOnlyAdmin.as_view()(APIRequestFactory().post(
            "/synthetic/", {}, format="json", HTTP_ACCEPT="text/html",
        ))
        response.render()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WriteOnlyAdmin.get_calls, 0)
        self.assertNotIn(b"SYNTHETIC_GET_ONLY_SECRET", response.content)
