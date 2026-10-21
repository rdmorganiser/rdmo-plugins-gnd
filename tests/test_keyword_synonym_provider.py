from xml.etree import ElementTree

import pytest

from rdmo_gnd.client import GNDClient, GNDKeywordSynonymClient

from .helpers import record, response, search_response, subject_record


def test_synonym_search_uses_lobid_name_fields_and_displays_the_preferred_name(
    settings, synonym_provider, http_get,
):
    settings.GND_PROVIDER_URL = 'https://example.org/gnd'
    settings.GND_PROVIDER_HEADERS = {'User-Agent': 'Synonym tests', 'Accept-Language': 'en'}
    settings.GND_REQUEST_TIMEOUT = (2, 8)
    item = {**subject_record('7707637-0', 'Brennstofftablette'), 'variantName': ['Fuel pellet']}
    http_get.return_value = response({'totalItems': 1, 'member': [item]})

    first = synonym_provider.get_options(None, search=' fuel pellet ')
    assert synonym_provider.get_options(None, search='fuel pellet') == first
    assert first[0]['id'] == '7707637-0'
    label = ElementTree.fromstring(f'<span>{first[0]["text"]}</span>')
    assert ''.join(label.itertext()) == 'Brennstofftablette [7707637-0]'
    assert synonym_provider.get_search(first[0]['text']) == 'Brennstofftablette'
    http_get.assert_called_once_with(
        'https://example.org/gnd/search',
        params={
            'q': 'preferredName:"fuel pellet" OR variantName:"fuel pellet"',
            'filter': 'type:SubjectHeading', 'format': 'json',
        },
        headers={'User-Agent': 'Synonym tests', 'Accept-Language': 'en'}, timeout=(2, 8),
    )


@pytest.mark.parametrize('query,escaped', [
    ('Fuel "pellet"', r'Fuel \"pellet\"'),
    (r'fuel\pellet', r'fuel\\pellet'),
    ('fuel" OR type:Person OR preferredName:"', r'fuel\" OR type:Person OR preferredName:\"'),
])
def test_search_input_cannot_escape_the_name_fields(query, escaped, http_get):
    http_get.return_value = response({'totalItems': 0, 'member': []})
    GNDKeywordSynonymClient().search(query)
    params = http_get.call_args.kwargs['params']
    assert params['q'] == f'preferredName:"{escaped}" OR variantName:"{escaped}"'
    assert params['filter'] == 'type:SubjectHeading'


@pytest.mark.parametrize('entity_type', [
    'DifferentiatedPerson', 'CorporateBody', 'PlaceOrGeographicName', 'Work', 'ConferenceOrEvent',
])
def test_search_discards_non_subjects_even_if_the_service_ignores_the_filter(
    synonym_provider, http_get, entity_type,
):
    excluded = subject_record('118540238', 'Excluded record', entity_type)
    http_get.return_value = response({'totalItems': 2, 'member': [excluded, subject_record()]})
    options = synonym_provider.get_options(None, search='fuel')
    assert [option['id'] for option in options] == ['4008186-2']


@pytest.mark.parametrize('types', [
    ['DifferentiatedPerson'], ['CorporateBody'], ['PlaceOrGeographicName'], ['Work'],
    None, [], 'SubjectHeading', [None], [{}],
])
def test_identifier_lookup_rejects_out_of_scope_records_cached_by_the_general_client(
    synonym_provider, http_get, types,
):
    item = {**record(), 'type': types}
    http_get.return_value = response(item)
    assert GNDClient().get_record(item['gndIdentifier']) == item
    assert synonym_provider.get_options(None, search=item['gndIdentifier']) == []
    assert synonym_provider.get_options(None, search=item['id']) == []
    http_get.assert_called_once()


@pytest.mark.parametrize('entity_type', [
    'SubjectHeadingSensoStricto', 'SoftwareProduct', 'NomenclatureInBiologyOrChemistry', 'Language', 'GroupOfPersons',
])
def test_subject_subtypes_can_be_restored_by_identifier_and_url_despite_the_minimum_length(
    settings, synonym_provider, http_get, entity_type,
):
    settings.GND_SEARCH_MIN_LEN = 100
    item = subject_record(entity_type=entity_type)
    http_get.return_value = response(item)
    options = synonym_provider.get_options(None, search=item['gndIdentifier'])
    assert [option['id'] for option in options] == ['4008186-2']
    assert synonym_provider.get_options(None, search=item['id']) == options
    http_get.assert_called_once()


def test_synonym_searches_do_not_reuse_or_replace_unrestricted_search_results(synonym_provider, http_get):
    http_get.side_effect = [response(search_response()), response({'totalItems': 1, 'member': [subject_record()]})]
    assert GNDClient().search('fuel') == search_response()
    options = synonym_provider.get_options(None, search='fuel')
    assert [option['id'] for option in options] == ['4008186-2']
    assert GNDClient().search('fuel') == search_response()
    assert synonym_provider.get_options(None, search='fuel') == options
    assert http_get.call_count == 2


def test_empty_synonym_results_are_cached(synonym_provider, http_get):
    http_get.return_value = response({'totalItems': 0, 'member': []})
    assert synonym_provider.get_options(None, search='unknown') == []
    assert synonym_provider.get_options(None, search='unknown') == []
    http_get.assert_called_once()


@pytest.mark.parametrize('failed_response', [response({}, status=503), response({'member': []})])
def test_synonym_request_failures_return_no_options_and_are_not_cached(synonym_provider, http_get, failed_response):
    http_get.side_effect = [failed_response, response({'totalItems': 1, 'member': [subject_record()]})]
    assert synonym_provider.get_options(None, search='fuel') == []
    options = synonym_provider.get_options(None, search='fuel')
    assert [option['id'] for option in options] == ['4008186-2']
    assert synonym_provider.get_options(None, search='fuel') == options
    assert http_get.call_count == 2
