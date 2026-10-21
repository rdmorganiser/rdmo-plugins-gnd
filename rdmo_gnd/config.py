import math
from collections.abc import Mapping
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error

from requests.exceptions import InvalidHeader
from requests.structures import CaseInsensitiveDict
from requests.utils import check_header_validity

from .utils import DEFAULT_USER_AGENT


def configuration_error(name, message, hint):
    return Error(f'{name} {message}', obj=name, hint=hint, id='rdmo_gnd.E001')


def normalize_headers(value):
    """Use the same text headers for Requests and cache-key serialization."""
    headers = CaseInsensitiveDict({'User-Agent': DEFAULT_USER_AGENT})
    errors = []
    if value is None:
        return headers, errors
    if not isinstance(value, Mapping):
        errors.append(configuration_error(
            'GND_PROVIDER_HEADERS', 'must be a mapping or None.',
            'Use a dictionary of HTTP headers, or None to retain the default User-Agent.',
        ))
        return headers, errors

    for name, header_value in value.items():
        try:
            if isinstance(name, bytes):
                name = name.decode('ascii')
            if isinstance(header_value, bytes):
                header_value = header_value.decode('latin-1')
            check_header_validity((name, '' if header_value is None else header_value))
            name.encode('ascii')
            if header_value is not None:
                header_value.encode('latin-1')
        except (InvalidHeader, UnicodeError):
            errors.append(configuration_error(
                'GND_PROVIDER_HEADERS', 'contains an invalid HTTP header.',
                'Use ASCII header names and text or bytes values encodable as Latin-1; '
                'header values must not contain newlines or leading whitespace.',
            ))
        else:
            headers[name] = header_value

    user_agent = headers.get('User-Agent')
    if not isinstance(user_agent, str) or not user_agent.strip():
        errors.append(configuration_error(
            'GND_PROVIDER_HEADERS', 'must provide a non-empty User-Agent string.',
            'Remove the User-Agent override to use the default, or provide a stable service identifier.',
        ))
    return headers, errors


def is_positive_number(value):
    return (type(value) is int and value > 0) or (
        type(value) is float and math.isfinite(value) and value > 0
    )


def normalize_cache_config(value):
    """Return an enabled cache configuration, or None for direct fetching."""
    if value is None:
        return None, []
    if not isinstance(value, Mapping):
        return None, [configuration_error(
            'GND_CACHE_CONFIG', 'must be a mapping or None.',
            'Use a dictionary with alias and timeout keys, or None to disable caching.',
        )]
    if not value:
        return None, []

    config = {'alias': value.get('alias', 'default'), 'timeout': value.get('timeout', 43200)}
    errors = []
    if any(key not in ('alias', 'timeout') for key in value):
        errors.append(configuration_error(
            'GND_CACHE_CONFIG', 'must contain only alias and timeout keys.',
            'Use e.g. {"alias": "gnd", "timeout": 43200}.',
        ))
    timeout = config['timeout']
    if type(timeout) is not int or timeout < 0:
        errors.append(configuration_error(
            'GND_CACHE_CONFIG', 'timeout must be a non-negative integer (seconds).',
            'Use 43200 for 12 hours, or 0 to disable caching.',
        ))
    alias = config['alias']
    if not isinstance(alias, str) or not alias:
        errors.append(configuration_error(
            'GND_CACHE_CONFIG', 'alias must be a non-empty string.',
            'Use an alias defined in CACHES, e.g. "default".',
        ))
    elif type(timeout) is int and timeout > 0 and alias not in settings.CACHES:
        errors.append(configuration_error(
            'GND_CACHE_CONFIG', 'alias must name a cache configured in CACHES.',
            'Configure the alias in CACHES, or set timeout to 0 to disable caching.',
        ))
    return (None if errors or timeout == 0 else config), errors


def load_settings():
    """Return normalized client settings and all independent configuration errors."""
    config = {
        'min_len': getattr(settings, 'GND_SEARCH_MIN_LEN', 1),
        'request_timeout': getattr(settings, 'GND_REQUEST_TIMEOUT', (3, 10)),
        'url': getattr(settings, 'GND_PROVIDER_URL', 'https://lobid.org/gnd'),
    }
    config['headers'], errors = normalize_headers(getattr(settings, 'GND_PROVIDER_HEADERS', None))
    config['cache_config'], cache_errors = normalize_cache_config(getattr(settings, 'GND_CACHE_CONFIG', None))
    errors.extend(cache_errors)

    if type(config['min_len']) is not int or config['min_len'] < 1:
        errors.append(configuration_error(
            'GND_SEARCH_MIN_LEN', 'must be a positive integer.', 'Use 1 to preserve short searches, or e.g. 3.',
        ))
    timeout = config['request_timeout']
    valid_timeout = all(is_positive_number(part) for part in timeout) if isinstance(timeout, (tuple, list)) else (
        is_positive_number(timeout)
    )
    if not valid_timeout or (isinstance(timeout, (tuple, list)) and len(timeout) != 2):
        errors.append(configuration_error(
            'GND_REQUEST_TIMEOUT', 'must be a positive number or a (connect, read) pair.',
            'Use finite positive values, e.g. (3, 10) seconds.',
        ))
    elif isinstance(timeout, list):
        config['request_timeout'] = tuple(timeout)

    url = config['url']
    valid_url = False
    if isinstance(url, str) and url.strip():
        url = url.strip().rstrip('/')
        try:
            parsed = urlsplit(url)
            valid_url = parsed.scheme in ('http', 'https') and bool(parsed.hostname)
            _ = parsed.port  # validate the port if present
        except ValueError:
            valid_url = False
    if valid_url:
        config['url'] = url
    else:
        errors.append(configuration_error(
            'GND_PROVIDER_URL', 'must be a non-empty HTTP or HTTPS URL.',
            'Use the API base URL, e.g. "https://lobid.org/gnd".',
        ))
    return config, errors
