import logging

import pytest

from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError

import requests

from rdmo_gnd.cache import GNDResponseCache
from rdmo_gnd.checks import check_gnd_settings
from rdmo_gnd.client import GNDClient
from rdmo_gnd.config import DEFAULT_USER_AGENT
from rdmo_gnd.sru import GNDSRUClient

from .helpers import record, response, search_response, sru_response, subject_record


def test_repeated_search_uses_cache_and_keeps_request_configuration(settings, http_get, caplog):
    caplog.set_level('DEBUG', logger='rdmo_gnd.client')
    settings.GND_PROVIDER_HEADERS = {'User-Agent': 'GND tests'}
    settings.GND_REQUEST_TIMEOUT = (2, 8)
    first = GNDClient().search('  Zeolite  ')
    second = GNDClient().search('Zeolite')
    assert first == search_response()
    assert first == second
    http_get.assert_called_once_with(
        'https://lobid.org/gnd/search', params={'q': 'Zeolite', 'format': 'json'},
        headers={'User-Agent': 'GND tests'}, timeout=(2, 8),
    )
    assert ('rdmo_gnd.client', logging.DEBUG, 'GND search cache miss.') in caplog.record_tuples
    assert ('rdmo_gnd.client', logging.DEBUG, 'GND search cache hit.') in caplog.record_tuples


def test_empty_results_are_cached(http_get):
    http_get.return_value = response({'totalItems': 0, 'member': []})
    assert GNDClient().search('unknown') == {'totalItems': 0, 'member': []}
    assert GNDClient().search('unknown') == {'totalItems': 0, 'member': []}
    http_get.assert_called_once()


def test_record_lookups_are_cached_across_identifier_formats(http_get):
    http_get.return_value = response(record())
    assert GNDClient().get_record('2129905-5') == record()
    assert GNDClient().get_record('https://d-nb.info/gnd/2129905-5') == record()
    http_get.assert_called_once_with(
        'https://lobid.org/gnd/2129905-5', params={'format': 'json'},
        headers={'User-Agent': DEFAULT_USER_AGENT}, timeout=(3, 10),
    )


def test_default_user_agent_is_added_without_losing_custom_headers(settings, http_get):
    settings.GND_PROVIDER_HEADERS = {'Accept-Language': 'de'}
    GNDClient().search('Zeolite')
    http_get.return_value = response(record())
    GNDClient().get_record('2129905-5')
    for call in http_get.call_args_list:
        assert call.kwargs['headers']['User-Agent'] == DEFAULT_USER_AGENT
        assert call.kwargs['headers']['Accept-Language'] == 'de'


def test_custom_user_agent_overrides_default_case_insensitively(settings, http_get):
    settings.GND_PROVIDER_HEADERS = {'user-agent': 'My RDMO service'}
    GNDClient().search('Zeolite')
    headers = http_get.call_args.kwargs['headers']
    assert headers['User-Agent'] == 'My RDMO service'
    assert len(headers) == 1


def test_none_headers_retain_default_user_agent(settings, http_get):
    settings.GND_PROVIDER_HEADERS = None
    GNDClient().search('Zeolite')
    assert http_get.call_args.kwargs['headers'] == {'User-Agent': DEFAULT_USER_AGENT}


def test_bytes_headers_share_cache_entries_with_equivalent_text(settings, http_get):
    settings.GND_PROVIDER_HEADERS = {b'user-agent': b'GND tests', b'X-Example': b'caf\xe9'}
    assert GNDClient().search('Zeolite') == search_response()
    assert http_get.call_args.kwargs['headers'] == {'User-Agent': 'GND tests', 'X-Example': 'caf\u00e9'}
    settings.GND_PROVIDER_HEADERS = {'User-Agent': 'GND tests', 'x-example': 'caf\u00e9'}
    assert GNDClient().search('Zeolite') == search_response()
    http_get.assert_called_once()
    settings.GND_PROVIDER_HEADERS = {'User-Agent': 'GND tests', 'X-Example': b'other'}
    GNDClient().search('Zeolite')
    assert http_get.call_count == 2


def test_none_header_values_preserve_requests_removal_semantics(settings, http_get):
    settings.GND_PROVIDER_HEADERS = {'Accept': None}
    GNDClient().search('Zeolite')
    headers = http_get.call_args.kwargs['headers']
    prepared = requests.Session().prepare_request(requests.Request('GET', 'https://example.org', headers=headers))
    assert 'Accept' not in prepared.headers
    assert prepared.headers['User-Agent'] == DEFAULT_USER_AGENT


@pytest.mark.parametrize('client_class', [GNDClient, GNDSRUClient])
def test_response_cache_can_be_replaced(settings, mocker, http_get, client_class):
    settings.GND_PROVIDER_HEADERS = {'User-Agent': 'GND tests', 'Authorization': 'Lobid-only token'}
    data = {'totalItems': 1, 'member': [subject_record()]}
    response_cache = mocker.Mock(spec=GNDResponseCache)
    response_cache.get_or_fetch.return_value = data
    cache_class = mocker.patch.object(client_class, 'cache_class', return_value=response_cache)
    client = client_class()
    assert client.response_cache is response_cache
    assert client.search('example') == data
    cache_class.assert_called_once()
    assert cache_class.call_args.kwargs['alias'] == 'default'
    assert cache_class.call_args.kwargs['timeout'] == 43200
    for call in response_cache.get_or_fetch.call_args_list:
        assert call.kwargs['url'].startswith(client.url)
        assert call.kwargs['headers'] == client.headers
        assert callable(call.kwargs['fetch'])
        assert callable(call.kwargs['validate'])
    http_get.assert_not_called()


@pytest.mark.parametrize('client_class', [GNDClient, GNDSRUClient])
@pytest.mark.parametrize('cache_config', [
    'unset', None, {}, {'timeout': 0}, {'alias': 'not-configured', 'timeout': 0},
], ids=['unset', 'none', 'empty', 'zero-timeout', 'unused-alias'])
def test_unconfigured_cache_fetches_directly(settings, mocker, http_get, caplog, client_class, cache_config):
    if cache_config == 'unset':
        del settings.GND_CACHE_CONFIG
    else:
        settings.GND_CACHE_CONFIG = cache_config
    cache_class = mocker.patch.object(client_class, 'cache_class')
    get_or_fetch = mocker.patch.object(GNDResponseCache, 'get_or_fetch')
    backends = mocker.patch('rdmo_gnd.cache.caches')
    make_key = mocker.patch('rdmo_gnd.cache.make_cache_key')
    savepoints = mocker.patch('rdmo_gnd.cache.cache_savepoints')
    caplog.set_level('DEBUG', logger='rdmo_gnd.client')
    if client_class is GNDSRUClient:
        data = {'totalItems': 1, 'member': [subject_record()]}
        http_get.return_value = sru_response([subject_record()])
        request_count = 4
    else:
        data = search_response()
        request_count = 2

    client = client_class()
    assert client.response_cache is None
    assert client.search('example') == data
    assert client.search('example') == data
    assert http_get.call_count == request_count
    cache_class.assert_not_called()
    get_or_fetch.assert_not_called()
    backends.__getitem__.assert_not_called()
    make_key.assert_not_called()
    savepoints.assert_not_called()
    assert not any('cache' in record.getMessage().lower() for record in caplog.records)


@pytest.mark.parametrize('cache_config,alias,timeout', [
    ({'alias': 'default'}, 'default', 43200),
    ({'timeout': 60}, 'default', 60),
    ({'alias': 'gnd', 'timeout': 120}, 'gnd', 120),
])
def test_enabled_cache_config_is_normalized_without_changing_settings(settings, cache_config, alias, timeout):
    original = cache_config.copy()
    settings.GND_CACHE_CONFIG = cache_config
    client = GNDClient()
    assert client.response_cache.alias == alias
    assert client.response_cache.timeout == timeout
    assert settings.GND_CACHE_CONFIG == original
    assert check_gnd_settings(None) == []


@pytest.mark.parametrize('cache_config', [None, {'alias': 'default'}])
@pytest.mark.parametrize('old_alias,old_timeout', [('gnd', 10), (None, -1)])
def test_legacy_cache_settings_are_ignored(settings, http_get, cache_config, old_alias, old_timeout):
    settings.GND_CACHE_CONFIG = cache_config
    settings.GND_CACHE_ALIAS = old_alias
    settings.GND_CACHE_TIMEOUT = old_timeout
    assert check_gnd_settings(None) == []
    client = GNDClient()
    assert (client.response_cache is None) == (cache_config is None)
    assert client.search('example') == search_response()
    assert client.search('example') == search_response()
    assert http_get.call_count == (2 if cache_config is None else 1)


@pytest.mark.parametrize('identifier', ['', '../search', 'https://example.org/2129905-5', 'Zeolite'])
def test_invalid_identifier_is_not_requested(identifier, http_get):
    assert GNDClient().get_record(identifier) is None
    http_get.assert_not_called()


def test_disabled_cache_bypasses_even_an_existing_entry(settings, mocker, http_get):
    GNDClient().search('Zeolite')
    settings.GND_CACHE_CONFIG = {'timeout': 0}
    cache = caches['default']
    get = mocker.patch.object(cache, 'get')
    set_ = mocker.patch.object(cache, 'set')
    GNDClient().search('Zeolite')
    GNDClient().search('Zeolite')
    get.assert_not_called()
    set_.assert_not_called()
    assert http_get.call_count == 3


def test_expiration_fetches_a_fresh_response(settings, mocker, http_get):
    settings.GND_CACHE_CONFIG = {'timeout': 1}
    clock = mocker.patch('time.time', return_value=100)
    GNDClient().search('Zeolite')
    clock.return_value = 100.5
    GNDClient().search('Zeolite')
    assert http_get.call_count == 1
    clock.return_value = 102
    GNDClient().search('Zeolite')
    assert http_get.call_count == 2


def test_endpoint_query_case_and_headers_separate_cache_entries(settings, http_get):
    GNDClient().search('Zeolite')
    GNDClient().search('zeolite')
    settings.GND_PROVIDER_URL = 'https://example.org/gnd/'
    GNDClient().search('Zeolite')
    del settings.GND_PROVIDER_URL
    settings.GND_PROVIDER_HEADERS = {'Accept-Language': 'de', 'User-Agent': 'test'}
    GNDClient().search('Zeolite')
    settings.GND_PROVIDER_HEADERS = {'user-agent': 'test', 'accept-language': 'de'}
    GNDClient().search('Zeolite')
    settings.GND_PROVIDER_HEADERS = {'Accept-Language': 'en', 'User-Agent': 'test'}
    GNDClient().search('Zeolite')
    assert http_get.call_count == 5


def test_search_and_record_namespaces_are_separate(http_get):
    GNDClient().search('2129905-5')
    http_get.return_value = response(record())
    assert GNDClient().get_record('2129905-5') == record()
    assert http_get.call_count == 2


def test_conflicting_header_case_uses_the_effective_request_headers(settings, http_get):
    settings.GND_PROVIDER_HEADERS = {'Accept-Language': 'de', 'accept-language': 'en'}
    GNDClient().search('Zeolite')
    settings.GND_PROVIDER_HEADERS = {'accept-language': 'en'}
    GNDClient().search('Zeolite')
    assert http_get.call_count == 1
    settings.GND_PROVIDER_HEADERS = {'accept-language': 'en', 'Accept-Language': 'de'}
    GNDClient().search('Zeolite')
    assert http_get.call_count == 2


def test_http_error_logs_request_id_and_is_not_cached(http_get, caplog):
    http_get.return_value = response({
        'status': 500, 'error': 'Internal Server Error',
        'message': 'private error detail', 'requestId': '38a49fb3-845858',
    }, status=500)
    assert GNDClient().search('private search term') is None
    assert '500' in caplog.text
    assert '38a49fb3-845858' in caplog.text
    assert 'private error detail' not in caplog.text
    assert 'private search term' not in caplog.text
    http_get.return_value = response(search_response())
    assert GNDClient().search('private search term') == search_response()
    assert http_get.call_count == 2


def test_invalid_json_logs_header_request_id_and_is_not_cached(http_get, caplog):
    invalid_response = response(None)
    invalid_response._content = b'<html>Service unavailable</html>'
    invalid_response.headers['X-Request-ID'] = 'header-request-id'
    http_get.return_value = invalid_response
    assert GNDClient().search('Zeolite') is None
    assert 'header-request-id' in caplog.text
    http_get.return_value = response(search_response())
    assert GNDClient().search('Zeolite') == search_response()
    assert http_get.call_count == 2


@pytest.mark.parametrize('payload', [
    None, [], {}, {'totalItems': 1}, {'totalItems': True, 'member': []},
    {'totalItems': -1, 'member': []}, {'totalItems': 0, 'member': [record()]},
    {'totalItems': 1, 'member': {}}, {'totalItems': 1, 'member': [{}]},
    {'totalItems': 1, 'member': [{**record(), 'preferredName': None}]},
])
def test_malformed_search_responses_are_not_cached(payload, http_get, caplog):
    http_get.return_value = response(payload)
    assert GNDClient().search('Zeolite') is None
    assert 'unexpected response structure' in caplog.text
    http_get.return_value = response(search_response())
    assert GNDClient().search('Zeolite') == search_response()
    assert http_get.call_count == 2


def test_malformed_record_is_not_cached(http_get, caplog):
    http_get.return_value = response({'id': record()['id']})
    assert GNDClient().get_record('2129905-5') is None
    assert 'unexpected response structure' in caplog.text
    http_get.return_value = response(record())
    assert GNDClient().get_record('2129905-5') == record()
    assert http_get.call_count == 2


@pytest.mark.parametrize('error', [requests.exceptions.ConnectionError, requests.exceptions.Timeout])
def test_network_failures_are_not_cached(error, http_get, caplog):
    http_get.side_effect = error('sensitive connection detail')
    assert GNDClient().search('Zeolite') is None
    assert error.__name__ in caplog.text
    assert 'sensitive connection detail' not in caplog.text
    http_get.side_effect = None
    assert GNDClient().search('Zeolite') == search_response()
    assert http_get.call_count == 2


def test_cache_read_failure_falls_back_to_lobid(mocker, http_get, caplog):
    get = mocker.patch.object(caches['default'], 'get', side_effect=DatabaseError('cache unavailable'))
    assert GNDClient().search('Zeolite') == search_response()
    assert 'Could not read GND cache' in caplog.text
    mocker.stop(get)
    assert GNDClient().search('Zeolite') == search_response()
    http_get.assert_called_once()


def test_cache_write_failure_preserves_upstream_result(mocker, http_get, caplog):
    set_ = mocker.patch.object(caches['default'], 'set', side_effect=OSError('cache unavailable'))
    assert GNDClient().search('Zeolite') == search_response()
    assert 'Could not write GND cache' in caplog.text
    mocker.stop(set_)
    assert GNDClient().search('Zeolite') == search_response()
    assert http_get.call_count == 2


def test_malformed_cached_response_is_replaced_with_upstream_data(mocker, http_get, caplog):
    get = mocker.patch.object(caches['default'], 'get', return_value={'error': 'invalid cached data'})
    assert GNDClient().search('Zeolite') == search_response()
    assert 'Invalid cached GND search response' in caplog.text
    mocker.stop(get)
    assert GNDClient().search('Zeolite') == search_response()
    http_get.assert_called_once()


def test_defaults(settings):
    del settings.GND_CACHE_CONFIG
    client = GNDClient()
    assert client.min_len == 1
    assert client.response_cache is None
    assert client.request_timeout == (3, 10)
    assert check_gnd_settings(None) == []


INVALID_SETTINGS = {
    'GND_SEARCH_MIN_LEN': (0, -1, True, 3.5, '3'),
    'GND_CACHE_CONFIG': (
        False, True, 0, '', [], [('alias', 'default')], 'default',
        {'alias': 'missing'}, {'alias': ''}, {'alias': None}, {'alias': 1}, {'alias': False},
        {'timeout': -1}, {'timeout': True}, {'timeout': None}, {'timeout': '43200'}, {'timeout': 1.5},
        {'unknown': 1}, {1: 'value'}, {'alias': None, 'timeout': 0},
    ),
    'GND_PROVIDER_HEADERS': (
        [], 'not a mapping', {1: 'value'}, {'X-Example': 1}, {b'\xff': 'value'},
        {'X-Example': 'value\nother'}, {'X-Example': '\u2603'},
        {'User-Agent': None}, {'user-agent': ''}, {'User-Agent': '  '},
    ),
    'GND_PROVIDER_URL': (None, 42, '', '   ', 'https://', 'not a URL', 'ftp://example.org', 'https://[invalid'),
    'GND_REQUEST_TIMEOUT': (0, -1, None, True, float('inf'), float('nan'), (), (1,), (1, 0), (1, 2, 3)),
}


@pytest.mark.parametrize('name,value', [
    (name, value) for name, values in INVALID_SETTINGS.items() for value in values
])
def test_invalid_configuration_is_reported(settings, name, value, http_get):
    setattr(settings, name, value)
    with pytest.raises(ImproperlyConfigured, match=name):
        GNDClient()
    errors = check_gnd_settings(None)
    assert len(errors) == 1
    assert errors[0].id == 'rdmo_gnd.E001'
    assert errors[0].obj == name
    assert errors[0].hint
    http_get.assert_not_called()


def test_checks_collect_independent_errors_without_requests_or_cache_access(settings, mocker, http_get):
    settings.GND_PROVIDER_URL = None
    settings.GND_SEARCH_MIN_LEN = 0
    settings.GND_PROVIDER_HEADERS = {'X-Example': 42}
    settings.GND_CACHE_CONFIG = {'alias': None, 'timeout': -1}
    mocker.patch('django.core.cache.CacheHandler.__getitem__', side_effect=AssertionError('cache accessed'))
    errors = check_gnd_settings(None)
    assert len(errors) == 5
    assert {error.obj for error in errors} == {
        'GND_PROVIDER_URL', 'GND_SEARCH_MIN_LEN', 'GND_PROVIDER_HEADERS', 'GND_CACHE_CONFIG',
    }
    with pytest.raises(ImproperlyConfigured, match='GND_PROVIDER_URL') as raised:
        GNDClient()
    for error in errors:
        assert error.obj in str(raised.value)
    http_get.assert_not_called()


def test_disabled_cache_does_not_require_the_alias_to_exist(settings):
    settings.GND_CACHE_CONFIG = {'alias': 'not-configured', 'timeout': 0}
    assert check_gnd_settings(None) == []
    client = GNDClient()
    assert client.response_cache is None
    assert client.search('Zeolite') == search_response()


def test_url_and_timeout_are_normalized(settings):
    settings.GND_PROVIDER_URL = ' https://example.org/gnd/ '
    settings.GND_REQUEST_TIMEOUT = [2, 8]
    client = GNDClient()
    assert client.url == 'https://example.org/gnd'
    assert client.request_timeout == (2, 8)


@pytest.mark.parametrize('timeout', [2.5, [2, 8]])
def test_numeric_and_list_timeouts_are_supported(settings, timeout, http_get):
    settings.GND_REQUEST_TIMEOUT = timeout
    settings.GND_CACHE_CONFIG = None
    GNDClient().search('Zeolite')
    assert http_get.call_args.kwargs['timeout'] == (tuple(timeout) if isinstance(timeout, list) else timeout)
