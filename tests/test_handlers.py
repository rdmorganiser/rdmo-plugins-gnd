from types import SimpleNamespace

import pytest

from rdmo_gnd.handlers import gnd_handler

from .helpers import record, response

ATTRIBUTE_MAP = {
    'gndIdentifier': 'https://example.org/domain/gnd',
    'preferredName': 'https://example.org/domain/name',
    'missingField': 'https://example.org/domain/absent',
}


@pytest.fixture
def gnd_value(settings, http_get):
    settings.GND_PROVIDER_MAP = [ATTRIBUTE_MAP]
    http_get.return_value = response(record())
    return SimpleNamespace(
        external_id='2129905-5', attribute=SimpleNamespace(uri=ATTRIBUTE_MAP['gndIdentifier']),
        project=object(), set_prefix='1', set_index=2,
    )


@pytest.fixture
def update_value(mocker):
    return mocker.patch('rdmo_gnd.handlers.Value.objects.update_or_create')


@pytest.fixture
def get_attribute(mocker):
    return mocker.patch('rdmo_gnd.handlers.Attribute.objects.get')


def test_cached_record_updates_each_project_independently(gnd_value, update_value, get_attribute, http_get):
    first_project = gnd_value.project
    gnd_handler(None, None, instance=gnd_value)
    gnd_value.project = object()
    gnd_handler(None, None, instance=gnd_value)
    http_get.assert_called_once()
    assert update_value.call_count == 2
    assert update_value.call_args_list[0].kwargs['project'] is first_project
    update_value.assert_called_with(
        project=gnd_value.project, snapshot=None, attribute=get_attribute.return_value,
        set_prefix='1', set_index=2, defaults={'text': record()['preferredName']},
    )
    get_attribute.assert_called_with(uri=ATTRIBUTE_MAP['preferredName'])


def test_identifier_restoration_and_mapped_fields_share_record_cache(
    provider, gnd_value, update_value, get_attribute, http_get,
):
    provider.get_options(None, search=gnd_value.external_id)
    gnd_handler(None, None, instance=gnd_value)
    http_get.assert_called_once()
    update_value.assert_called_once()
    get_attribute.assert_called_once_with(uri=ATTRIBUTE_MAP['preferredName'])


def test_failed_record_lookup_does_not_update_fields(gnd_value, update_value, get_attribute, http_get, caplog):
    http_get.return_value = response({'status': 500}, status=500)
    gnd_handler(None, None, instance=gnd_value)
    assert 'HTTP 500' in caplog.text
    update_value.assert_not_called()
    get_attribute.assert_not_called()


@pytest.mark.parametrize('change', ['raw', 'no_identifier', 'other_attribute', 'no_mapping'])
def test_irrelevant_changes_do_not_make_requests(settings, change, gnd_value, update_value, get_attribute, http_get):
    if change == 'no_identifier':
        gnd_value.external_id = ''
    elif change == 'other_attribute':
        gnd_value.attribute.uri = 'https://example.org/domain/other'
    elif change == 'no_mapping':
        settings.GND_PROVIDER_MAP = []
    gnd_handler(None, None, instance=gnd_value, raw=change == 'raw')
    http_get.assert_not_called()
    update_value.assert_not_called()
    get_attribute.assert_not_called()
