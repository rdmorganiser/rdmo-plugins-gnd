from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

import dpath
import requests

from rdmo.projects.models import Value


@receiver(post_save, sender=Value)
def post_save_project_values(sender, **kwargs):
    # check if we are importing fixtures
    if kwargs.get('raw'):
        return

    # get the attribute map from the settings
    attribute_map_list = getattr(settings, 'GND_PROVIDER_MAP', None)
    if not attribute_map_list:
        return

    instance = kwargs.get('instance')
    if instance and instance.external_id:
        for attribute_map in attribute_map_list:
            gnd_identifier = attribute_map.pop('gndIdentifier', None)
            if instance.attribute.uri == gnd_identifier:
                try:
                    url = getattr(settings, 'GND_PROVIDER_URL', 'https://lobid.org/gnd').rstrip('/')
                    headers = getattr(settings, 'GND_PROVIDER_HEADERS', {})
                    response = requests.get(f'{url}/{instance.external_id}', headers=headers)
                    response.raise_for_status()

                    data = response.json()

                    for gnd_path, rdmo_uri in attribute_map.items():
                        try:
                            text = dpath.get(data, gnd_path)
                        except KeyError:
                            pass
                        else:
                            Value.objects.update_or_create(
                                project=instance.project,
                                attribute__uri=rdmo_uri,
                                defaults={'text': text}
                            )

                except (requests.exceptions.RequestException, requests.exceptions.HTTPError):
                    pass
