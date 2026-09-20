from rest_framework.authentication import SessionAuthentication


class PortalSessionAuthentication(SessionAuthentication):
    def authenticate_header(self, request):
        return "Session"
