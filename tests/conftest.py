import pytest

from django.core.cache import caches

from rdmo_gnd.providers import GNDKeywordsProvider, GNDProvider

from .helpers import response, search_response


@pytest.fixture(autouse=True)
def clear_cache():
    caches['default'].clear()
    yield
    caches['default'].clear()


@pytest.fixture(autouse=True)
def http_get(mocker):
    """Keep tests independent of public GND services."""
    return mocker.patch('rdmo_gnd.client.requests.get', return_value=response(search_response()))


@pytest.fixture
def provider():
    return GNDProvider('gnd', 'GND', 'rdmo_gnd.providers.GNDProvider')


@pytest.fixture
def keyword_provider():
    return GNDKeywordsProvider('gnd_keywords', 'GND Keywords', 'rdmo_gnd.providers.GNDKeywordsProvider')
