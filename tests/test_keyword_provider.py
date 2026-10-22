from xml.etree import ElementTree

import pytest

from django.contrib.sites.models import Site

from rest_framework.test import APIRequestFactory, force_authenticate

from rdmo.domain.models import Attribute
from rdmo.options.models import OptionSet
from rdmo.projects.models import Project, Value
from rdmo.projects.viewsets import ProjectViewSet
from rdmo.questions.models import Catalog, Page, Question, Section

from rdmo_gnd.providers import GNDKeywordSynonymProvider, GNDProvider, GNDSRUKeywordsProvider

from .helpers import response, sru_response, subject_record, xml_response


@pytest.mark.parametrize('search', [None, '', '  ', 'f', ' fu ', 'fu [<a href="example">id</a>]'])
@pytest.mark.parametrize('provider_fixture', ['keyword_provider', 'synonym_provider'])
def test_blank_and_short_keyword_searches_make_no_requests(settings, request, provider_fixture, http_get, search):
    settings.GND_SEARCH_MIN_LEN = 3
    assert request.getfixturevalue(provider_fixture).get_options(None, search=search) == []
    http_get.assert_not_called()


def test_keyword_labels_are_escaped_and_restored_with_configured_links(settings, keyword_provider, http_get):
    item = subject_record('4434275-5', 'Python <Programmiersprache> & "tools"', 'SoftwareProduct')
    http_get.return_value = sru_response([item])
    settings.GND_PROVIDER_LINK_TARGET = '_self'
    options = keyword_provider.get_options(None, search='Python')
    assert options[0]['id'] == '4434275-5'
    text = options[0]['text']
    rendered = ElementTree.fromstring(f'<span>{text}</span>')
    assert [element.tag for element in rendered.iter()] == ['span', 'a']
    assert ''.join(rendered.itertext()) == f'{item["preferredName"]} [4434275-5]'
    assert rendered.find('a').attrib == {
        'href': item['id'],
        'target': '_self',
        'rel': 'noopener noreferrer',
    }
    assert keyword_provider.get_search(text) == item['preferredName']

    assert keyword_provider.get_options(None, search=text) == options
    for call in http_get.call_args_list[-2:]:
        assert '"Python <Programmiersprache> & \\"tools\\""' in call.kwargs['params']['query']


def test_keyword_identifier_lookup_uses_sru_and_cached_labels_can_be_rendered_again(
    settings,
    keyword_provider,
    http_get,
):
    settings.GND_SEARCH_MIN_LEN = 100
    http_get.return_value = sru_response([subject_record()])
    first = keyword_provider.get_options(None, search='4008186-2')
    settings.GND_PROVIDER_LINK_TARGET = None
    second = keyword_provider.get_options(None, search='https://d-nb.info/gnd/4008186-2')
    assert first[0]['id'] == second[0]['id'] == '4008186-2'
    assert ElementTree.fromstring(f'<span>{second[0]["text"]}</span>').find('a').attrib == {
        'href': subject_record()['id'],
    }
    http_get.assert_called_once()
    assert http_get.call_args.kwargs['params']['query'] == 'dnb.nid = "4008186-2" and dnb.bbg = Ts*'


def test_keyword_upstream_failure_returns_empty_options(keyword_provider, http_get, caplog):
    http_get.return_value = xml_response('<error/>', status=503)
    assert keyword_provider.get_options(None, search='fuel') == []
    http_get.assert_called_once()
    assert http_get.call_args.args == ('https://services.dnb.de/sru/authorities',)
    assert 'HTTP 503' in caplog.text


@pytest.mark.django_db
@pytest.mark.parametrize(
    'provider_name,provider_class',
    [
        ('GNDSRUKeywordsProvider', GNDSRUKeywordsProvider),
        ('GNDKeywordSynonymProvider', GNDKeywordSynonymProvider),
    ],
)
def test_rdmo_options_api_and_saved_values_support_the_additional_provider(
    settings,
    http_get,
    django_user_model,
    provider_name,
    provider_class,
):
    provider_key = 'gnd_keyword_synonyms' if provider_class is GNDKeywordSynonymProvider else 'gnd_sru_keywords'
    settings.OPTIONSET_PROVIDERS = [
        ('gnd', 'GND Provider', 'rdmo_gnd.providers.GNDProvider'),
        (provider_key, 'GND Keywords Provider', f'rdmo_gnd.providers.{provider_name}'),
    ]
    optionset = OptionSet.objects.create(uri_path='test-gnd', provider_key='gnd')
    assert isinstance(optionset.provider, GNDProvider)
    catalog = Catalog.objects.create(uri_path='test-gnd')
    section = Section.objects.create(uri_path='test-gnd')
    page = Page.objects.create(uri_path='test-gnd')
    attribute = Attribute.objects.create(key='test-gnd')
    question = Question.objects.create(uri_path='test-gnd', attribute=attribute, widget_type='autocomplete')
    catalog.sections.add(section)
    section.pages.add(page)
    page.questions.add(question)
    question.optionsets.add(optionset)
    project = Project.objects.create(title='GND keywords', catalog=catalog)
    existing = Value.objects.create(
        project=project,
        attribute=attribute,
        external_id='118540238',
        text='Existing person label',
    )

    optionset.provider_key = provider_key
    optionset.save()
    optionset.refresh_from_db()
    assert isinstance(optionset.provider, provider_class)
    assert optionset.has_search
    assert optionset.has_refresh
    is_synonym_provider = provider_class is GNDKeywordSynonymProvider
    http_get.return_value = (
        response({'totalItems': 1, 'member': [subject_record()]})
        if is_synonym_provider
        else sru_response([subject_record()])
    )
    user = django_user_model.objects.create_user(username='gnd-admin', is_superuser=True, is_staff=True)
    request = APIRequestFactory().get('/options/', {'optionset': optionset.pk, 'search': 'Brennstoff'})
    request.site = Site.objects.get(pk=settings.SITE_ID)
    force_authenticate(request, user=user)
    result = ProjectViewSet.as_view({'get': 'options'}, detail=True)(request, pk=project.pk)
    assert result.status_code == 200
    assert len(result.data) == 1
    option = result.data[0]
    assert option['id'] == '4008186-2'
    assert 'Brennstoff' in option['text']

    selection = Value.objects.create(
        project=project,
        attribute=attribute,
        collection_index=1,
        external_id=option['id'],
        text=option['text'],
    )
    selection.refresh_from_db()
    http_get.return_value = response(subject_record()) if is_synonym_provider else sru_response([subject_record()])
    assert optionset.provider.get_options(project, search=selection.external_id) == [
        {
            'id': selection.external_id,
            'text': selection.text,
        }
    ]
    existing.refresh_from_db()
    assert (existing.external_id, existing.text) == ('118540238', 'Existing person label')
