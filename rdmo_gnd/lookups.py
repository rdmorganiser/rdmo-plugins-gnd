"""Select local or lobid records without changing existing lookup defaults."""

import logging
import re

from .client import GNDClient
from .local_client import GNDLocalClient
from .utils import get_identifier

logger = logging.getLogger(__name__)
LOCAL_SCALAR_FIELDS = frozenset({'id', 'gndIdentifier', 'preferredName'})


def supports_path(path):
    return isinstance(path, str) and (
        path in LOCAL_SCALAR_FIELDS or path == 'variantName' or re.fullmatch(r'variantName/[0-9]+', path) is not None
    )


def get_gnd_record(identifier, source='lobid', required_paths=()):
    identifier = get_identifier(identifier)
    if identifier is None:
        return None
    if source not in ('lobid', 'local', 'auto'):
        logger.warning('Unknown GND lookup policy: %r', source)
        return None
    if source == 'local':
        return GNDLocalClient().get_record(identifier)
    if source == 'auto' and all(supports_path(path) for path in required_paths):
        data = GNDLocalClient().get_record(identifier)
        if data is not None:
            return data
    return GNDClient().get_record(identifier)
