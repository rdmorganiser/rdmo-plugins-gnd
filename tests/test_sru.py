from pathlib import Path

import pytest

from django.core.cache import caches
from django.db import DatabaseError

import requests

from rdmo_gnd.client import GNDClient
from rdmo_gnd.config import DEFAULT_USER_AGENT
from rdmo_gnd.sru import GNDSRUClient

from .helpers import sru_response, subject_record, xml_response


@pytest.mark.parametrize(
    'term,filename,identifier,label',
    [
        ('fuel', 'fuel.xml', '4008186-2', 'Brennstoff'),
        ('field work', 'field-work.xml', '4016674-0', 'Feldforschung'),
    ],
)
def test_english_terms_use_multilingual_subject_search(http_get, term, filename, identifier, label):
    content = (Path(__file__).parent / 'fixtures' / 'sru' / filename).read_bytes()
    http_get.side_effect = [sru_response(), xml_response(content)]
    data = GNDSRUClient().search(term)
    assert any(item['gndIdentifier'] == identifier and item['preferredName'] == label for item in data['member'])
    assert [call.kwargs['params']['query'] for call in http_get.call_args_list] == [
        f'dnb.an = "{term}" and dnb.bbg = Ts*',
        f'dnb.sw = "{term}" and dnb.bbg = Ts*',
    ]


def test_preferred_names_precede_synonym_matches_and_results_are_deduplicated_and_limited(http_get):
    preferred = [subject_record(f'400000{index}-0', f'Preferred {index}') for index in range(3)]
    synonyms = [preferred[1], *[subject_record(f'400001{index}-0', f'Synonym {index}') for index in range(9)]]
    http_get.side_effect = [sru_response(preferred), sru_response(synonyms)]
    data = GNDSRUClient().search('example')
    assert data == {'totalItems': 10, 'member': preferred + synonyms[1:8]}
    assert all(call.kwargs['params']['maximumRecords'] == 10 for call in http_get.call_args_list)


def test_requests_and_cache_use_sru_endpoint_and_only_the_configured_user_agent(settings, http_get):
    settings.GND_PROVIDER_URL = 'https://example.org/private-lobid'
    settings.GND_PROVIDER_HEADERS = {'User-Agent': 'GND tests', 'Authorization': 'private-lobid-token'}
    settings.GND_REQUEST_TIMEOUT = (2, 8)
    http_get.return_value = sru_response([subject_record()])
    first = GNDSRUClient().search('  Brennstoff  ')
    second = GNDSRUClient().search('Brennstoff')
    assert first == second == {'totalItems': 1, 'member': [subject_record()]}
    assert http_get.call_count == 2
    for call in http_get.call_args_list:
        assert call.args == ('https://services.dnb.de/sru/authorities',)
        assert call.kwargs['headers'] == {'User-Agent': 'GND tests'}
        assert call.kwargs['timeout'] == (2, 8)
        assert call.kwargs['params']['recordSchema'] == 'RDFxml'
        assert call.kwargs['params']['operation'] == 'searchRetrieve'
        assert call.kwargs['params']['version'] == '1.1'


def test_sru_cache_is_independent_of_lobid_and_ignores_lobid_only_header_changes(settings, http_get):
    GNDClient().search('Brennstoff')
    http_get.return_value = sru_response([subject_record()])
    assert GNDSRUClient().search('Brennstoff')['member'] == [subject_record()]
    settings.GND_PROVIDER_HEADERS = {'Authorization': 'different-lobid-token'}
    assert GNDSRUClient().search('Brennstoff')['member'] == [subject_record()]
    assert http_get.call_count == 3
    settings.GND_PROVIDER_HEADERS = {'User-Agent': 'different deployment'}
    GNDSRUClient().search('Brennstoff')
    assert http_get.call_count == 5


def test_default_sru_user_agent(http_get):
    http_get.return_value = sru_response()
    GNDSRUClient().search('unknown')
    assert http_get.call_args.kwargs['headers'] == {'User-Agent': DEFAULT_USER_AGENT}


@pytest.mark.parametrize('term', ['', '  ', 'f', ' fu '])
def test_short_queries_do_not_access_cache_or_network(settings, mocker, http_get, term):
    settings.GND_SEARCH_MIN_LEN = 3
    get = mocker.patch.object(caches['default'], 'get')
    assert GNDSRUClient().search(term) is None
    get.assert_not_called()
    http_get.assert_not_called()


@pytest.mark.parametrize(
    'term,literal',
    [
        ('"fuel" or dnb.bbg=Tp*', r'"\"fuel\" or dnb.bbg=Tp\*"'),
        ('path\\term', r'"path\\term"'),
        ('a*?^', r'"a\*\?\^"'),
        ('Research &amp; Development', '"Research &amp; Development"'),
        ('Étude', '"Étude"'),
    ],
)
def test_search_text_is_a_cql_literal(http_get, term, literal):
    http_get.return_value = sru_response()
    assert GNDSRUClient().search(term) == {'totalItems': 0, 'member': []}
    assert [call.kwargs['params']['query'] for call in http_get.call_args_list] == [
        f'dnb.an = {literal} and dnb.bbg = Ts*',
        f'dnb.sw = {literal} and dnb.bbg = Ts*',
    ]


@pytest.mark.parametrize(
    'entity_type',
    [
        'SubjectHeading',
        'SubjectHeadingSensoStricto',
        'CharactersOrMorphemes',
        'EthnographicName',
        'FictiveTerm',
        'GroupOfPersons',
        'HistoricSingleEventOrEra',
        'Language',
        'MeansOfTransportWithIndividualName',
        'NomenclatureInBiologyOrChemistry',
        'ProductNameOrBrandName',
        'SoftwareProduct',
    ],
)
def test_all_subject_heading_types_are_available(http_get, entity_type):
    item = subject_record(entity_type=entity_type)
    http_get.return_value = sru_response([item])
    assert GNDSRUClient().search('example')['member'] == [item]


@pytest.mark.parametrize(
    'entity_type',
    [
        'Person',
        'DifferentiatedPerson',
        'CorporateBody',
        'PlaceOrGeographicName',
        'Work',
    ],
)
def test_out_of_scope_records_are_filtered_for_searches_and_identifiers(http_get, entity_type):
    http_get.return_value = sru_response([subject_record(entity_type=entity_type)])
    client = GNDSRUClient()
    assert client.search('example') == {'totalItems': 0, 'member': []}
    assert client.get_record('4008186-2') is None


@pytest.mark.parametrize(
    'namespace',
    [
        'http://d-nb.info/standards/elementset/gnd#',
        'https://d-nb.info/standards/elementset/gnd#',
    ],
)
def test_rdf_namespaces_and_unicode_labels_are_normalized(http_get, namespace):
    item = subject_record(name='Grüner Baumpython', entity_type='NomenclatureInBiologyOrChemistry')
    http_get.return_value = sru_response([item], gnd_namespace=namespace)
    result = GNDSRUClient().get_record(item['id'])
    assert result == {**item, 'preferredName': 'Grüner Baumpython'}


def test_identifier_lookup_bypasses_keyword_length_and_reuses_its_cache(settings, http_get):
    settings.GND_SEARCH_MIN_LEN = 100
    http_get.return_value = sru_response([subject_record()])
    for identifier in ('4008186-2', 'http://d-nb.info/gnd/4008186-2', 'https://d-nb.info/gnd/4008186-2/'):
        assert GNDSRUClient().get_record(identifier) == subject_record()
    http_get.assert_called_once()
    assert http_get.call_args.kwargs['params']['query'] == 'dnb.nid = "4008186-2" and dnb.bbg = Ts*'
    assert http_get.call_args.kwargs['params']['maximumRecords'] == 1


@pytest.mark.parametrize('identifier', ['', '../search', 'https://example.org/4008186-2', 'Brennstoff'])
def test_invalid_identifiers_do_not_make_requests(http_get, identifier):
    assert GNDSRUClient().get_record(identifier) is None
    http_get.assert_not_called()


def test_wrong_identifier_is_not_returned_or_cached(http_get):
    http_get.return_value = sru_response([subject_record('4016674-0', 'Feldforschung')])
    assert GNDSRUClient().get_record('4008186-2') is None
    http_get.return_value = sru_response([subject_record()])
    assert GNDSRUClient().get_record('4008186-2') == subject_record()
    assert http_get.call_count == 2


def test_empty_search_and_missing_identifier_responses_are_cached(http_get):
    http_get.return_value = sru_response()
    for _ in range(2):
        assert GNDSRUClient().search('unknown') == {'totalItems': 0, 'member': []}
        assert GNDSRUClient().get_record('4008186-2') is None
    assert http_get.call_count == 3


def test_disabled_cache_bypasses_cached_sru_results(settings, mocker, http_get):
    http_get.return_value = sru_response()
    GNDSRUClient().search('unknown')
    settings.GND_CACHE_CONFIG = {'timeout': 0}
    get = mocker.patch.object(caches['default'], 'get')
    set_ = mocker.patch.object(caches['default'], 'set')
    GNDSRUClient().search('unknown')
    get.assert_not_called()
    set_.assert_not_called()
    assert http_get.call_count == 4


@pytest.mark.parametrize('operation', ['get', 'set'])
def test_cache_errors_preserve_sru_results(mocker, http_get, caplog, operation):
    mocker.patch.object(caches['default'], operation, side_effect=DatabaseError('cache unavailable'))
    http_get.return_value = sru_response([subject_record()])
    assert GNDSRUClient().search('Brennstoff')['member'] == [subject_record()]
    assert 'GND cache' in caplog.text


def test_non_subject_cache_entry_is_replaced(mocker, http_get, caplog):
    mocker.patch.object(
        caches['default'],
        'get',
        return_value={
            'totalItems': 1,
            'member': [subject_record(entity_type='Person')],
        },
    )
    http_get.return_value = sru_response([subject_record()])
    assert GNDSRUClient().search('Brennstoff')['member'] == [subject_record()]
    assert 'Invalid cached GND sru-search response' in caplog.text


@pytest.mark.parametrize(
    'payload',
    [
        b'not XML',
        b'<html>Service unavailable</html>',
        sru_response(total=-1).content,
        sru_response([subject_record()], total=0).content,
        sru_response(total=1).content,
        sru_response([subject_record()]).content.replace(b'RDFxml', b'MARC21-xml'),
        sru_response([{**subject_record(), 'gndIdentifier': '4016674-0'}]).content,
        sru_response([{**subject_record(), 'id': 'https://example.org/4008186-2'}]).content,
        sru_response([{**subject_record(), 'preferredName': ''}]).content,
        sru_response([{**subject_record(), 'type': []}]).content,
        b'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"/>',
        b'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">'
        b'<numberOfRecords>0</numberOfRecords>'
        b'<diagnostics><diagnostic xmlns="http://www.loc.gov/zing/srw/diagnostic/">'
        b'<message>private diagnostic detail</message></diagnostic></diagnostics></searchRetrieveResponse>',
        b'<!DOCTYPE searchRetrieveResponse [<!ENTITY example "private entity">]>' + sru_response().content,
    ],
    ids=[
        'malformed',
        'html',
        'negative-count',
        'inconsistent-count',
        'missing-records',
        'wrong-schema',
        'inconsistent-id',
        'external-url',
        'empty-label',
        'missing-type',
        'missing-count',
        'diagnostic',
        'dtd',
    ],
)
def test_invalid_sru_responses_are_logged_and_not_cached(http_get, caplog, payload):
    http_get.return_value = xml_response(payload)
    http_get.return_value.headers['X-Request-ID'] = 'sru-failure-id'
    assert GNDSRUClient().search('private search term') is None
    assert 'invalid SRU response' in caplog.text
    assert 'sru-failure-id' in caplog.text
    assert 'private' not in caplog.text
    http_get.return_value = sru_response([subject_record()])
    assert GNDSRUClient().search('private search term')['member'] == [subject_record()]
    assert http_get.call_count == 3


def test_second_query_failure_returns_no_partial_results_and_can_recover(http_get):
    http_get.side_effect = [sru_response([subject_record()]), xml_response('<broken'), sru_response()]
    assert GNDSRUClient().search('Brennstoff') is None
    assert GNDSRUClient().search('Brennstoff')['member'] == [subject_record()]
    assert http_get.call_count == 3


@pytest.mark.parametrize('failure', [requests.exceptions.Timeout, requests.exceptions.ConnectionError])
def test_sru_network_failures_are_not_cached(http_get, caplog, failure):
    http_get.side_effect = failure('private error detail')
    assert GNDSRUClient().search('fuel') is None
    assert failure.__name__ in caplog.text
    assert 'private' not in caplog.text
    http_get.side_effect = None
    http_get.return_value = sru_response([subject_record()])
    assert GNDSRUClient().search('fuel')['member'] == [subject_record()]
    assert http_get.call_count == 3


def test_sru_http_failures_are_not_cached(http_get, caplog):
    http_get.return_value = xml_response('<error>private error detail</error>', status=503)
    assert GNDSRUClient().search('fuel') is None
    assert 'HTTP 503' in caplog.text
    assert 'private' not in caplog.text
    http_get.return_value = sru_response([subject_record()])
    assert GNDSRUClient().search('fuel')['member'] == [subject_record()]


def test_global_params_do_not_override_sru_subject_search(settings, http_get):
    settings.GND_PARAMS = {'q': 'person', 'filter': 'type:Person', 'query': 'person'}
    http_get.return_value = sru_response([subject_record()])
    GNDSRUClient().search('fuel')
    for call in http_get.call_args_list:
        params = call.kwargs['params']
        assert 'q' not in params
        assert 'filter' not in params
        assert 'dnb.bbg = Ts*' in params['query']
