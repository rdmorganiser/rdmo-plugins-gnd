import logging

import pytest

from django.core.cache import InvalidCacheBackendError, caches
from django.db import DatabaseError

from rdmo_gnd.cache import GNDResponseCache, make_cache_key
from rdmo_gnd.config import DEFAULT_USER_AGENT
from rdmo_gnd.utils import is_search_response

from .helpers import search_response

URL = 'https://lobid.org/gnd/search'
PARAMS = {'q': 'Zeolite', 'format': 'json'}
HEADERS = {'User-Agent': DEFAULT_USER_AGENT}
LEGACY_KEY = 'gnd:search:v1:6a6ec356b897fffab2126af9b6af9bfb91e537ee270d9d1ba2a325a9fceaf3fc'


@pytest.fixture
def response_cache():
    return GNDResponseCache(alias='default', timeout=43200)


@pytest.fixture
def fetch(mocker):
    return mocker.Mock(return_value=search_response())


def get_response(response_cache, fetch):
    return response_cache.get_or_fetch('search', URL, PARAMS, HEADERS, fetch, is_search_response)


def test_cache_reuses_data_without_an_http_client(response_cache, fetch, http_get, caplog):
    caplog.set_level('DEBUG', logger='rdmo_gnd.cache')
    assert get_response(response_cache, fetch) == search_response()
    assert get_response(response_cache, fetch) == search_response()
    fetch.assert_called_once_with()
    http_get.assert_not_called()
    assert ('rdmo_gnd.cache', logging.DEBUG, 'GND search cache miss.') in caplog.record_tuples
    assert ('rdmo_gnd.cache', logging.DEBUG, 'GND search cache hit.') in caplog.record_tuples


def test_cache_reuses_an_entry_written_with_the_previous_key_algorithm(response_cache, fetch):
    caches['default'].set(LEGACY_KEY, search_response())
    assert make_cache_key('search', URL, PARAMS, HEADERS) == LEGACY_KEY
    assert get_response(response_cache, fetch) == search_response()
    fetch.assert_not_called()


def test_expiration_fetches_new_data(response_cache, fetch, mocker):
    response_cache.timeout = 1
    clock = mocker.patch('time.time', return_value=100)
    assert get_response(response_cache, fetch) == search_response()
    clock.return_value = 100.5
    assert get_response(response_cache, fetch) == search_response()
    fetch.assert_called_once_with()
    clock.return_value = 102
    assert get_response(response_cache, fetch) == search_response()
    assert fetch.call_count == 2


def test_empty_results_are_cached(response_cache, fetch):
    fetch.return_value = {'totalItems': 0, 'member': []}
    assert get_response(response_cache, fetch) == {'totalItems': 0, 'member': []}
    assert get_response(response_cache, fetch) == {'totalItems': 0, 'member': []}
    fetch.assert_called_once_with()


def test_failed_fetches_are_not_cached(response_cache, fetch):
    fetch.side_effect = [None, search_response()]
    assert get_response(response_cache, fetch) is None
    assert get_response(response_cache, fetch) == search_response()
    assert get_response(response_cache, fetch) == search_response()
    assert fetch.call_count == 2


def test_fetch_exceptions_propagate_without_being_cached(response_cache, fetch, caplog):
    fetch.side_effect = ValueError('fetch failed')
    with pytest.raises(ValueError, match='fetch failed'):
        get_response(response_cache, fetch)
    assert 'Could not' not in caplog.text
    fetch.side_effect = None
    assert get_response(response_cache, fetch) == search_response()
    assert get_response(response_cache, fetch) == search_response()
    assert fetch.call_count == 2


def test_disabled_cache_bypasses_backend_keys_and_cached_data_validation(fetch, mocker):
    response_cache = GNDResponseCache(alias='not-configured', timeout=0)
    backends = mocker.patch('rdmo_gnd.cache.caches')
    key = mocker.patch('rdmo_gnd.cache.make_cache_key')
    validate = mocker.Mock()
    assert response_cache.get_or_fetch('search', URL, PARAMS, HEADERS, fetch, validate) == search_response()
    fetch.assert_called_once_with()
    backends.__getitem__.assert_not_called()
    key.assert_not_called()
    validate.assert_not_called()


def test_backend_lookup_is_lazy_and_configuration_errors_are_not_hidden(fetch):
    response_cache = GNDResponseCache(alias='not-configured', timeout=1)
    with pytest.raises(InvalidCacheBackendError, match='not-configured'):
        get_response(response_cache, fetch)
    fetch.assert_not_called()


@pytest.mark.parametrize('operation,error', [('get', DatabaseError), ('set', OSError)])
def test_backend_errors_preserve_fetched_results(response_cache, fetch, mocker, caplog, operation, error):
    failure = mocker.patch.object(caches['default'], operation, side_effect=error('private cache detail'))
    assert get_response(response_cache, fetch) == search_response()
    assert 'GND cache default' in caplog.text
    assert error.__name__ in caplog.text
    assert 'private cache detail' not in caplog.text
    mocker.stop(failure)
    assert get_response(response_cache, fetch) == search_response()
    assert fetch.call_count == (1 if operation == 'get' else 2)


def test_invalid_cached_data_is_replaced(response_cache, fetch, caplog):
    caches['default'].set(LEGACY_KEY, {'error': 'invalid cached response'})
    assert get_response(response_cache, fetch) == search_response()
    assert 'Invalid cached GND search response' in caplog.text
    assert get_response(response_cache, fetch) == search_response()
    fetch.assert_called_once_with()


def test_memory_cache_does_not_inspect_connections_or_start_transactions(response_cache, fetch, mocker):
    all_connections = mocker.patch('rdmo_gnd.cache.connections.all')
    atomic = mocker.patch('rdmo_gnd.cache.transaction.atomic')
    assert get_response(response_cache, fetch) == search_response()
    assert get_response(response_cache, fetch) == search_response()
    all_connections.assert_not_called()
    atomic.assert_not_called()
    fetch.assert_called_once_with()
