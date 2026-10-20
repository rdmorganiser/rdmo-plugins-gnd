from django.core.checks import register

from .config import load_settings


@register()
def check_gnd_settings(app_configs, **kwargs):
    return load_settings()[1]
