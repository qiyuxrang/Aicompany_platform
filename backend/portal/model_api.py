from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .model_gateway import GatewayError, selectable_models


@api_view(["GET"])
def route_options(request, route_code):
    try:
        return Response(selectable_models(request.user, route_code))
    except GatewayError as error:
        return Response({"detail": error.message, "code": error.code}, status=error.status)


urlpatterns = [
    path("routes/<slug:route_code>/options/", route_options),
]
