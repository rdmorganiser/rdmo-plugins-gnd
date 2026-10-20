from xml.etree import ElementTree

import pytest

from django.core.cache import caches

from .helpers import record, response


@pytest.mark.parametrize('search', [None, '', '  ', 'm', ' mo ', 'mo [<a href="example">id</a>]'])
def test_short_and_blank_searches_do_not_access_cache_or_lobid(settings, mocker, provider, search, http_get):
    settings.GND_SEARCH_MIN_LEN = 3
    get = mocker.patch.object(caches['default'], 'get')
    assert provider.get_options(None, search=search) == []
    get.assert_not_called()
    http_get.assert_not_called()


def test_default_minimum_preserves_one_character_searches(provider, http_get):
    assert provider.get_options(None, search='m')
    http_get.assert_called_once()


def test_saved_label_is_normalized_without_changing_query_syntax(provider, http_get):
    search = provider.get_text(record())
    options = provider.get_options(None, search=search)
    assert options == [{'id': '2129905-5', 'text': search}]
    assert http_get.call_args.kwargs['params']['q'] == record()['preferredName']


@pytest.mark.parametrize('preferred_name', [
    'Research & Development', '<b>Example label</b>', 'Literal &amp; expression',
    "L'étude \"example\"", 'x &lt; y',
])
def test_label_text_is_escaped_and_restored_for_search(provider, preferred_name, http_get):
    occupation = '<i>Chemistry & "physics"</i>'
    item = {
        **record(), 'preferredName': preferred_name,
        'professionOrOccupation': [{'label': occupation}],
    }
    http_get.return_value = response({'totalItems': 1, 'member': [item]})
    options = provider.get_options(None, search='example')
    text = options[0]['text']
    label = ElementTree.fromstring(f'<span>{text}</span>')
    assert [element.tag for element in label.iter()] == ['span', 'a']
    assert ''.join(label.itertext()) == f'{preferred_name} | {occupation} [{item["gndIdentifier"]}]'
    assert provider.get_search(text) == preferred_name

    provider.get_options(None, search=text)
    assert http_get.call_args.kwargs['params']['q'] == preferred_name


@pytest.mark.parametrize('search', [
    'Research & Development', 'Research &amp; Development', 'x &lt; y', 'Literal &#39; expression',
])
def test_typed_search_preserves_literal_entities(provider, search, http_get):
    provider.get_options(None, search=search)
    assert http_get.call_args.kwargs['params']['q'] == search


@pytest.mark.parametrize('search', ['Zeolite', '2129905-5'])
def test_link_target_is_rendered_from_settings_without_changing_cached_results(settings, provider, search, http_get):
    if search == '2129905-5':
        http_get.return_value = response(record())

    first = provider.get_options(None, search=search)
    first_link = ElementTree.fromstring(f'<span>{first[0]["text"]}</span>').find('a')
    assert first_link.attrib == {
        'href': record()['id'], 'target': '_blank', 'rel': 'noopener noreferrer',
    }
    assert first_link.text == record()['gndIdentifier']

    settings.GND_PROVIDER_LINK_TARGET = '_self'
    second = provider.get_options(None, search=search)
    second_link = ElementTree.fromstring(f'<span>{second[0]["text"]}</span>').find('a')
    assert second_link.get('target') == '_self'

    settings.GND_PROVIDER_LINK_TARGET = None
    third = provider.get_options(None, search=search)
    third_link = ElementTree.fromstring(f'<span>{third[0]["text"]}</span>').find('a')
    assert third_link.attrib == {'href': record()['id']}
    assert first[0]['id'] == second[0]['id'] == third[0]['id'] == record()['gndIdentifier']
    assert provider.get_search(first[0]['text']) == provider.get_search(third[0]['text']) == record()['preferredName']
    http_get.assert_called_once()


@pytest.mark.parametrize('occupations', [
    None, [], [{}], [None], {'label': 'occupation'}, [{'label': None}], [{'label': 1}],
])
def test_optional_occupations(provider, occupations):
    text = provider.get_text({**record(), 'professionOrOccupation': occupations})
    label = ElementTree.fromstring(f'<span>{text}</span>')
    assert ''.join(label.itertext()) == f'{record()["preferredName"]} [{record()["gndIdentifier"]}]'
    assert provider.get_search(text) == record()['preferredName']


def test_saved_occupation_labels(provider):
    item = {**record(), 'professionOrOccupation': [{'label': 'Chemist'}]}
    occupation_label = provider.get_text(item)
    assert ' | Chemist [' in occupation_label
    assert provider.get_search(occupation_label) == record()['preferredName']


def test_identifier_restoration_bypasses_keyword_length_and_uses_record_cache(settings, provider, http_get):
    settings.GND_SEARCH_MIN_LEN = 20
    http_get.return_value = response(record())
    for identifier in ('2129905-5', record()['id']):
        assert provider.get_options(None, search=identifier) == [{
            'id': '2129905-5', 'text': provider.get_text(record()),
        }]
    http_get.assert_called_once()


def test_upstream_failure_returns_empty_options(provider, http_get, caplog):
    http_get.return_value = response({'status': 500, 'requestId': 'test-failure'}, status=500)
    assert provider.get_options(None, search='Zeolite') == []
    assert 'HTTP 500' in caplog.text
