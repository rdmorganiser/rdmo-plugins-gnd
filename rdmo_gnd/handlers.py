from django.conf import settings
from django.dispatch import receiver

import dpath

from rdmo.domain.models import Attribute
from rdmo.projects.models import Value
from rdmo.projects.signals import value_created, value_updated

from .lookups import get_gnd_record


@receiver(value_created, sender=Value)
@receiver(value_updated, sender=Value)
def gnd_handler(signal, sender, instance=None, **kwargs):
    # check for GND_PROVIDER_MAP
    if not getattr(settings, 'GND_PROVIDER_MAP', None):
        return

    # check if we are importing fixtures
    if kwargs.get('raw'):
        return

    # check if this value instance has an external_id or has an attribute (it may have been deleted)
    if not instance.external_id or not instance.attribute_id:
        return

    # get the attribute map from the settings
    for attribute_map in settings.GND_PROVIDER_MAP:
        if 'gndIdentifier' in attribute_map and instance.attribute.uri == attribute_map['gndIdentifier']:
            required_paths = tuple(path for path in attribute_map if path not in ('gndIdentifier', 'lookup'))
            data = get_gnd_record(
                instance.external_id,
                source=attribute_map.get('lookup', 'lobid'),
                required_paths=required_paths,
            )
            if data is None:
                return

            for gnd_path, rdmo_uri in attribute_map.items():
                if gnd_path not in ('gndIdentifier', 'lookup') and rdmo_uri != instance.attribute.uri:
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
                                'text': text,
                            },
                        )
