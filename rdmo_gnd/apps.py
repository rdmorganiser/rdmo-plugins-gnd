from django.apps import AppConfig


class RDMOGNDConfig(AppConfig):
    name = 'rdmo_gnd'
    verbose_name = 'GND Plugin'

    def ready(self):
        from . import checks, handlers  # noqa: F401
