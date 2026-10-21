import logging

from django.core.exceptions import ImproperlyConfigured

import requests

from .cache import GNDResponseCache
from .config import load_settings
from .utils import get_identifier, is_record, is_search_response, is_subject_record

logger = logging.getLogger(__name__)


class GNDHTTPClient:
    """Share request handling and caching for public GND services."""

    cache_class = GNDResponseCache
    parse_error_message = 'invalid JSON'

    def __init__(self):
        config, errors = load_settings()
        if errors:
            raise ImproperlyConfigured('\n'.join(error.msg for error in errors))
        self.min_len = config['min_len']
        self.response_cache = None
        cache_config = config['cache_config']
        if cache_config is not None:
            self.response_cache = self.cache_class(**cache_config, logger=logger)
        self.request_timeout = config['request_timeout']
        self.url = config['url']
        self.headers = config['headers']

    def _get(self, kind, path, params, validate):
        url = f'{self.url}/{path}' if path else self.url
        if self.response_cache is None:
            return self._fetch(kind, url, params, validate)
        return self.response_cache.get_or_fetch(
            kind=kind, url=url, params=params, headers=self.headers,
            fetch=lambda: self._fetch(kind, url, params, validate), validate=validate,
        )

    def _fetch(self, kind, url, params, validate):
        """Return parsed, validated data, or None on an upstream failure."""
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=self.request_timeout)
        except requests.exceptions.RequestException as error:
            logger.warning('GND %s request failed at %s (%s).', kind, self.url, type(error).__name__)
            return None

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            try:
                data = response.json()
            except ValueError:
                data = None
            self._log_response_error(kind, response, 'HTTP error', data)
            return None

        try:
            data = self._parse_response(response)
        except ValueError:
            self._log_response_error(kind, response, self.parse_error_message)
            return None
        if not validate(data):
            self._log_response_error(kind, response, 'unexpected response structure', data)
            return None

        return data

    def _parse_response(self, response):
        return response.json()

    def _log_response_error(self, kind, response, reason, data=None):
        request_id = data.get('requestId') if isinstance(data, dict) else None
        if not isinstance(request_id, str):
            request_id = None
        request_id = request_id or response.headers.get('X-Request-ID', '-')
        logger.warning(
            'GND %s request failed at %s: %s (HTTP %s, request ID %s).',
            kind, self.url, reason, response.status_code, request_id,
        )


class GNDClient(GNDHTTPClient):
    """Fetch and cache public Lobid data independently of RDMO tenants."""

    def search(self, query):
        query = query.strip()
        if len(query) < self.min_len:
            return None
        return self._get('search', 'search', {'q': query, 'format': 'json'}, is_search_response)

    def get_record(self, identifier):
        identifier = get_identifier(identifier)
        if identifier is None:
            return None
        return self._get('record', identifier, {'format': 'json'}, is_record)


class GNDKeywordSynonymClient(GNDClient):
    """Search Lobid subject headings by their preferred names and synonyms."""

    def search(self, query):
        query = query.strip()
        if len(query) < self.min_len:
            return None
        # Treat input as a phrase within each field, preserving the fixed type filter.
        literal = '"' + query.replace('\\', '\\\\').replace('"', '\\"') + '"'
        params = {
            'q': f'preferredName:{literal} OR variantName:{literal}',
            'filter': 'type:SubjectHeading', 'format': 'json',
        }
        data = self._get('search', 'search', params, is_search_response)
        if data is None:
            return None
        return {**data, 'member': [item for item in data['member'] if is_subject_record(item)]}

    def get_record(self, identifier):
        data = super().get_record(identifier)
        return data if is_subject_record(data) else None
