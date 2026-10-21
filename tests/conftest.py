import pytest

from django.core.cache import caches

from rdmo_gnd.providers import GNDKeywordSynonymProvider, GNDProvider, GNDSRUKeywordsProvider

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
    return GNDSRUKeywordsProvider('gnd_sru_keywords', 'GND SRU Keywords', 'rdmo_gnd.providers.GNDSRUKeywordsProvider')


@pytest.fixture
def synonym_provider():
    return GNDKeywordSynonymProvider(
        'gnd_keyword_synonyms', 'GND Keyword Synonyms', 'rdmo_gnd.providers.GNDKeywordSynonymProvider',
    )


@pytest.fixture
def local_index(tmp_path, settings):
    from pathlib import Path

    from rdmo_gnd.index.database import build_index
    from rdmo_gnd.index.search import GNDIndex

    path = tmp_path / 'gnd.sqlite'
    build_index(Path(__file__).parent / 'fixtures/marc/subjects.xml', path)
    settings.GND_INDEX_PATH = path
    return GNDIndex(path)
