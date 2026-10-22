import hashlib
import json
import logging
from contextlib import ExitStack, contextmanager

from django.core.cache import caches
from django.core.cache.backends.db import DatabaseCache
from django.db import connections, transaction


@contextmanager
def cache_savepoints(cache):
    with ExitStack() as stack:
        if isinstance(cache, DatabaseCache):
            # Routers can select different read/write aliases, and reads may delete
            # expired rows. Protect every already-active transaction without
            # opening unused connections or assuming an alias is named "default".
            for connection in connections.all(initialized_only=True):
                if connection.in_atomic_block:
                    stack.enter_context(transaction.atomic(using=connection.alias))
        yield


def make_cache_key(kind, url, params, headers):
    """Hash the effective request without including tenant or project context."""
    headers = sorted((name.lower(), value) for name, value in headers.items())
    request = json.dumps([url, params, headers], sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(request.encode()).hexdigest()
    return f'gnd:{kind}:v1:{digest}'


class GNDResponseCache:
    """Cache validated responses independently of their upstream transport."""

    def __init__(self, alias, timeout, logger=None):
        self.alias = alias
        self.timeout = timeout
        self.logger = logger if logger is not None else logging.getLogger(__name__)

    def get_or_fetch(self, kind, url, params, headers, fetch, validate):
        """Reuse valid cached data, or fetch validated data (None means failure)."""
        if not self.timeout:
            return fetch()

        cache = caches[self.alias]
        key = make_cache_key(kind, url, params, headers)
        data = self._read(cache, key)
        if data is not None:
            if validate(data):
                self.logger.debug('GND %s cache hit.', kind)
                return data
            self.logger.warning('Invalid cached GND %s response in cache %s.', kind, self.alias)
        self.logger.debug('GND %s cache miss.', kind)

        data = fetch()
        if data is not None:
            self._write(cache, key, data)
        return data

    def _read(self, cache, key):
        try:
            with cache_savepoints(cache):
                return cache.get(key)
        except Exception as error:
            # Cache backends may raise database, filesystem, or network errors.
            self.logger.warning('Could not read GND cache %s (%s).', self.alias, type(error).__name__)
            return None

    def _write(self, cache, key, data):
        try:
            with cache_savepoints(cache):
                cache.set(key, data, timeout=self.timeout)
        except Exception as error:
            self.logger.warning('Could not write GND cache %s (%s).', self.alias, type(error).__name__)
