from django.conf import settings
from django.dispatch import receiver

import dpath
import requests

from rdmo.domain.models import Attribute
from rdmo.projects.models import Value
from rdmo.projects.signals import value_created, value_updated


@receiver(value_created, sender=Value)
@receiver(value_updated, sender=Value)
def gnd_handler(signal, sender, instance=None, **kwargs):
    # check for ROR_PROVIDER_MAP
    if not getattr(settings, 'GND_PROVIDER_MAP', None):
        return

    # check if we are importing fixtures
    if kwargs.get('raw'):
        return

    # check if this value instance has an external_id
    if not instance.external_id:
        return

    # get the attribute map from the settings
    for attribute_map in settings.GND_PROVIDER_MAP:
        if 'gndIdentifier' in attribute_map and instance.attribute.uri == attribute_map['gndIdentifier']:
            try:
                url = getattr(settings, 'GND_PROVIDER_URL', 'https://lobid.org/gnd').rstrip('/')
                headers = getattr(settings, 'GND_PROVIDER_HEADERS', {})

                response = requests.get(f'{url}/{instance.external_id}', headers=headers)
                response.raise_for_status()

                data = response.json()
            except (requests.exceptions.RequestException, requests.exceptions.HTTPError):
                return

            for gnd_path, rdmo_uri in attribute_map.items():
                if rdmo_uri != instance.attribute.uri:
                    try:
                        text = dpath.get(data, gnd_path)
                    except KeyError:
                        pass
                    else:
                        Value.objects.update_or_create(
                            project=instance.project,
                            snapshot=None,
                            attribute=Attribute.objects.get(uri=rdmo_uri),
                            set_prefix=instance.set_prefix,
                            set_index=instance.set_index,
                            defaults={
                                'text': text
                            }
                        )
