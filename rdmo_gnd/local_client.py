"""Adapt the optional local index to the existing provider client interface."""

import logging
import sqlite3

from django.conf import settings

logger = logging.getLogger(__name__)


class GNDLocalClient:
    def __init__(self):
        self.path = getattr(settings, 'GND_INDEX_PATH', None)
        self.min_len = getattr(settings, 'GND_SEARCH_MIN_LEN', 1)

    def _read(self, operation, *args):
        if self.path is None:
            logger.warning('Local GND index is not configured (GND_INDEX_PATH).')
            return None
        try:
            from .index.search import GNDIndex
            return getattr(GNDIndex(self.path), operation)(*args)
        except (ImportError, OSError, sqlite3.Error, ValueError) as error:
            logger.warning('Local GND index unavailable (%s): %s', type(error).__name__, error)
            return None

    def search(self, query):
        if len(query.strip()) < self.min_len:
            return None
        records = self._read('search', query)
        return {'member': records, 'totalItems': len(records)} if records is not None else None

    def get_record(self, identifier):
        return self._read('get', identifier)
