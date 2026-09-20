from django.apps import AppConfig


class PortalConfig(AppConfig):
    name = "portal"
    verbose_name = "平台管理"

    def ready(self):
        from . import signals
