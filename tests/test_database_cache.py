from datetime import timedelta
from types import SimpleNamespace

import pytest

from django.contrib.sites.models import Site
from django.core.cache import caches
from django.core.cache.backends.db import DatabaseCache
from django.db import connection, connections, transaction
from django.utils import timezone

from rdmo_gnd.client import GNDClient
from rdmo_gnd.providers import GNDProvider

from .helpers import response, search_response, sru_response, subject_record

pytestmark = pytest.mark.django_db(transaction=True, databases=['default', 'cache'])


@pytest.fixture(autouse=True)
def database_cache(settings, transactional_db):
    settings.GND_CACHE_CONFIG = {'alias': 'gnd'}
    # Cache tables are outside Django's model-based flush. Clear both aliases so
    # router changes cannot leave entries behind for the next test.
    for alias in ('default', 'cache'):
        with connections[alias].cursor() as cursor:
            cursor.execute('DELETE FROM test_gnd_cache')
    yield
    for alias in ('default', 'cache'):
        with connections[alias].cursor() as cursor:
            cursor.execute('DELETE FROM test_gnd_cache')


def assert_cache_failure_preserves_transaction(cache, mocker, caplog, operation='read', using='default'):
    # Real SQL errors abort PostgreSQL transactions; merely raising a mocked
    # DatabaseError does not reproduce the connection state we must recover.
    mocker.patch('rdmo_gnd.cache.caches', {'gnd': cache})
    if operation == 'write':
        mocker.patch.object(cache, 'get', return_value=None)
    with transaction.atomic(using=using):
        Site.objects.using(using).create(domain='before.example', name='before')
        assert GNDClient().search('Zeolite') == search_response()
        Site.objects.using(using).create(domain='after.example', name='after')
    assert f'Could not {operation} GND cache' in caplog.text
    assert Site.objects.using(using).filter(domain__in=['before.example', 'after.example']).count() == 2


def test_sites_and_independent_cache_instances_share_one_database_entry(settings, mocker, provider, http_get):
    settings.MULTISITE = True
    settings.SITE_ID = 1
    first = provider.get_options(
        SimpleNamespace(pk=1),
        search='Zeolite',
        user=SimpleNamespace(pk=1),
        site=SimpleNamespace(pk=1),
    )
    second_cache = DatabaseCache('test_gnd_cache', settings.CACHES['gnd'])
    second_provider = GNDProvider('gnd', 'GND', 'rdmo_gnd.providers.GNDProvider')
    settings.SITE_ID = 2
    mocker.patch('rdmo_gnd.cache.caches', {'gnd': second_cache})
    second = second_provider.get_options(
        SimpleNamespace(pk=2),
        search='Zeolite',
        user=SimpleNamespace(pk=2),
        site=SimpleNamespace(pk=2),
    )
    assert first == second
    http_get.assert_called_once()
    with connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*) FROM test_gnd_cache')
        assert cursor.fetchone()[0] == 1


def test_keyword_queries_share_normalized_database_cache_entries_between_sites(
    settings,
    mocker,
    keyword_provider,
    http_get,
):
    http_get.return_value = sru_response([subject_record()])
    settings.SITE_ID = 1
    first = keyword_provider.get_options(SimpleNamespace(pk=1), search='fuel', site=SimpleNamespace(pk=1))
    settings.SITE_ID = 2
    second_cache = DatabaseCache('test_gnd_cache', settings.CACHES['gnd'])
    mocker.patch('rdmo_gnd.cache.caches', {'gnd': second_cache})
    second = keyword_provider.get_options(SimpleNamespace(pk=2), search='fuel', site=SimpleNamespace(pk=2))
    assert first == second
    assert first[0]['id'] == '4008186-2'
    assert http_get.call_count == 2
    with connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*) FROM test_gnd_cache')
        assert cursor.fetchone()[0] == 2


def test_sites_with_different_headers_do_not_share_results(settings, provider, http_get):
    settings.MULTISITE = True
    settings.SITE_ID = 1
    settings.GND_PROVIDER_HEADERS = {'Accept-Language': 'de'}
    provider.get_options(None, search='Zeolite')
    settings.SITE_ID = 2
    settings.GND_PROVIDER_HEADERS = {'Accept-Language': 'en'}
    provider.get_options(None, search='Zeolite')
    assert http_get.call_count == 2


def test_expired_database_entry_is_refetched(provider, http_get):
    provider.get_options(None, search='Zeolite')
    with connection.cursor() as cursor:
        cursor.execute('UPDATE test_gnd_cache SET expires = %s', [timezone.now() - timedelta(seconds=1)])
    provider.get_options(None, search='Zeolite')
    assert http_get.call_count == 2


@pytest.mark.parametrize('operation', ['read', 'write'])
def test_sql_failure_preserves_outer_transaction_and_writes(settings, mocker, operation, http_get, caplog):
    broken = DatabaseCache('gnd_missing_cache_table', settings.CACHES['gnd'])
    assert_cache_failure_preserves_transaction(broken, mocker, caplog, operation=operation)
    http_get.assert_called_once()


def test_lobid_request_is_outside_cache_savepoints(http_get):
    def fetch(*args, **kwargs):
        assert connection.savepoint_ids == []
        return response(search_response())

    http_get.side_effect = fetch
    with transaction.atomic():
        assert GNDClient().search('Zeolite') == search_response()
    http_get.assert_called_once()


class CacheRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == 'django_cache':
            return 'cache'

    def db_for_write(self, model, **hints):
        if model._meta.app_label == 'django_cache':
            return 'cache'


class SplitCacheRouter(CacheRouter):
    def db_for_read(self, model, **hints):
        if model._meta.app_label == 'django_cache':
            return 'default'


def test_routed_sql_failure_preserves_non_default_transaction(settings, mocker, http_get, caplog):
    settings.DATABASE_ROUTERS = ['tests.test_database_cache.CacheRouter']
    broken = DatabaseCache('gnd_missing_cache_table', settings.CACHES['gnd'])
    assert_cache_failure_preserves_transaction(broken, mocker, caplog, using='cache')
    http_get.assert_called_once()


def test_expired_entry_cleanup_failure_protects_read_and_write_transactions(settings, mocker, http_get, caplog):
    GNDClient().search('Zeolite')
    with connection.cursor() as cursor:
        cursor.execute('UPDATE test_gnd_cache SET expires = %s', [timezone.now() - timedelta(seconds=1)])
    settings.DATABASE_ROUTERS = ['tests.test_database_cache.SplitCacheRouter']

    def fail_cleanup(keys):
        assert keys
        with connections['cache'].cursor() as cursor:
            cursor.execute('SELECT * FROM gnd_missing_cache_table')

    with transaction.atomic(), transaction.atomic(using='cache'):
        for alias in ('default', 'cache'):
            Site.objects.using(alias).create(domain='before-cleanup.example', name='before')
        cleanup = mocker.patch.object(caches['gnd'], '_base_delete_many', side_effect=fail_cleanup)
        assert GNDClient().search('Zeolite') == search_response()
        cleanup.assert_called_once()
        for alias in ('default', 'cache'):
            Site.objects.using(alias).create(domain='after-cleanup.example', name='after')
    assert 'Could not read GND cache' in caplog.text
    assert http_get.call_count == 2
    for alias in ('default', 'cache'):
        assert (
            Site.objects.using(alias)
            .filter(
                domain__in=['before-cleanup.example', 'after-cleanup.example'],
            )
            .count()
            == 2
        )
