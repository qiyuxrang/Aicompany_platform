from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

from .model_gateway import GatewayError, generate_for_use, selectable_models


DEPARTMENT_ROUTES = {
    "product": "product_assistant",
    "hr": "hr_assistant",
    "cost": "engineering_assistant",
}
MAX_PROMPT_LENGTH = 4000


def _error_response(error):
    return Response({"detail": error.message, "code": error.code}, status=error.status)


@api_view(["GET"])
def route_options(request, route_code):
    try:
        return Response(selectable_models(request.user, route_code))
    except GatewayError as error:
        return _error_response(error)


@api_view(["POST"])
def department_ask(request, module_code):
    try:
        route_code = DEPARTMENT_ROUTES.get(module_code)
        if not route_code:
            raise GatewayError("forbidden", status=403)
        body = request.data
        if not isinstance(body, dict) or set(body) != {"prompt", "model_selection"}:
            raise GatewayError("invalid_request", status=400)
        prompt = body["prompt"]
        selection = body["model_selection"]
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_LENGTH:
            raise GatewayError("invalid_request", status=400)
        if not isinstance(selection, dict) or set(selection) != {"model_id", "config_version"}:
            raise GatewayError("invalid_request", status=400)
        route = selectable_models(request.user, route_code)["route"]
        if route["module"] != module_code:
            raise GatewayError("forbidden", status=403)
        result = generate_for_use(
            request.user, route_code, [{"role": "user", "content": prompt}],
            model_selection=selection,
        )
        return Response({"content": result["content"]})
    except ParseError:
        return _error_response(GatewayError("invalid_request", status=400))
    except GatewayError as error:
        return _error_response(error)


urlpatterns = [
    path("routes/<slug:route_code>/options/", route_options),
    path("departments/<slug:module_code>/ask/", department_ask),
]
