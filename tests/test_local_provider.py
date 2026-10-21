from types import SimpleNamespace

import pytest

from rdmo_gnd.checks import check_local_index
from rdmo_gnd.handlers import gnd_handler
from rdmo_gnd.lookups import get_gnd_record
from rdmo_gnd.providers import GNDLocalKeywordsProvider

from .helpers import record, response


def test_provider_formats_search_ids_and_saved_labels(local_index, http_get):
    provider = GNDLocalKeywordsProvider(
        'gnd_local_keywords', 'Local GND', 'rdmo_gnd.providers.GNDLocalKeywordsProvider',
    )
    option = next(item for item in provider.get_options(None, search='fuel') if item['id'] == '4008186-2')
    assert 'Brennstoff' in option['text']
    assert 'https://d-nb.info/gnd/4008186-2' in option['text']
    assert provider.get_options(None, search=option['text'])[0] == option
    assert provider.get_options(None, search='4008186-2') == [option]
    assert provider.get_options(None, search='2129905-5') == []
    http_get.assert_not_called()


def test_provider_minimum_length_and_missing_index(settings, local_index, http_get, caplog):
    settings.GND_SEARCH_MIN_LEN = 30
    provider = GNDLocalKeywordsProvider('local', 'Local', '')
    assert provider.get_options(None, search='fuel') == []
    assert provider.get_options(None, search='4008186-2')
    settings.GND_INDEX_PATH = '/missing/gnd.sqlite'
    assert provider.get_options(None, search='4008186-2') == []
    assert 'unavailable' in caplog.text
    http_get.assert_not_called()


@pytest.mark.parametrize('source, paths, external', [
    ('lobid', (), True),
    ('local', ('definition',), False),
    ('auto', ('preferredName',), False),
    ('auto', ('variantName/999',), False),
    ('auto', ('definition',), True),
    ('auto', ('type/0',), True),
    ('auto', ('preferredName/*',), True),
])
def test_resolver_policies_and_capability_checks(local_index, http_get, source, paths, external):
    http_get.return_value = response(record())
    data = get_gnd_record('4008186-2', source=source, required_paths=paths)
    assert data is not None
    assert http_get.called is external


def test_default_policy_preserves_lobid_even_for_local_hit(local_index, http_get):
    http_get.return_value = response(record())
    get_gnd_record('4008186-2')
    http_get.assert_called_once()


@pytest.mark.parametrize('source, external', [('local', False), ('auto', True)])
def test_missing_local_record_policy(local_index, http_get, source, external):
    http_get.return_value = response(record())
    get_gnd_record('2129905-5', source=source)
    assert http_get.called is external


def test_invalid_identifier_never_requests_external_service(http_get):
    assert get_gnd_record('../search?q=oops') is None
    http_get.assert_not_called()


def test_full_keyword_selection_updates_mapping_without_http(local_index, settings, mocker, http_get):
    settings.GND_PROVIDER_MAP = [{
        'gndIdentifier': 'https://example.org/gnd', 'preferredName': 'https://example.org/name',
        'variantName/999': 'https://example.org/missing', 'lookup': 'local',
    }]
    attribute = mocker.patch('rdmo_gnd.handlers.Attribute.objects.get')
    update = mocker.patch('rdmo_gnd.handlers.Value.objects.update_or_create')
    instance = SimpleNamespace(
        external_id='4008186-2', attribute=SimpleNamespace(uri='https://example.org/gnd'),
        project=object(), set_prefix='1', set_index=2,
    )
    gnd_handler(None, None, instance=instance)
    update.assert_called_once_with(
        project=instance.project, snapshot=None, attribute=attribute.return_value,
        set_prefix='1', set_index=2, defaults={'text': 'Brennstoff'},
    )
    attribute.assert_called_once_with(uri='https://example.org/name')
    http_get.assert_not_called()


def test_checks_warn_about_source_age_and_never_disable_search(local_index, http_get):
    messages = check_local_index(None)
    assert [message.id for message in messages] == ['rdmo_gnd.W002']
    assert local_index.search('fuel')
    http_get.assert_not_called()


def test_checks_missing_bad_and_disabled_configuration(settings, tmp_path):
    settings.GND_INDEX_PATH = None
    assert check_local_index(None) == []
    settings.GND_INDEX_PATH = 'relative.sqlite'
    assert check_local_index(None)[0].id == 'rdmo_gnd.E002'
    settings.GND_INDEX_PATH = tmp_path / 'absent.sqlite'
    assert check_local_index(None)[0].id == 'rdmo_gnd.W001'


@pytest.mark.django_db
def test_registered_local_provider_project_api_and_saved_selection(local_index, settings, django_user_model, http_get):
    from django.contrib.sites.models import Site

    from rest_framework.test import APIRequestFactory, force_authenticate

    from rdmo.domain.models import Attribute
    from rdmo.options.models import OptionSet
    from rdmo.projects.models import Project, Value
    from rdmo.projects.viewsets import ProjectViewSet
    from rdmo.questions.models import Catalog, Page, Question, Section

    settings.OPTIONSET_PROVIDERS = [
        ('gnd_local_keywords', 'Local GND', 'rdmo_gnd.providers.GNDLocalKeywordsProvider'),
    ]
    optionset = OptionSet.objects.create(uri_path='local', provider_key='gnd_local_keywords')
    attribute = Attribute.objects.create(key='local-keyword')
    catalog = Catalog.objects.create(uri_path='local')
    section = Section.objects.create(uri_path='local')
    page = Page.objects.create(uri_path='local')
    question = Question.objects.create(uri_path='local', attribute=attribute, widget_type='autocomplete')
    catalog.sections.add(section)
    section.pages.add(page)
    page.questions.add(question)
    question.optionsets.add(optionset)
    project = Project.objects.create(title='Local keyword test', catalog=catalog)
    user = django_user_model.objects.create_user(username='local-admin', is_superuser=True, is_staff=True)
    request = APIRequestFactory().get('/options/', {'optionset': optionset.pk, 'search': 'fuel ce'})
    request.site = Site.objects.get(pk=settings.SITE_ID)
    force_authenticate(request, user=user)
    result = ProjectViewSet.as_view({'get': 'options'}, detail=True)(request, pk=project.pk)
    assert result.status_code == 200
    option = result.data[0]
    assert option['id'] == '4008195-3'
    selected = Value.objects.create(project=project, attribute=attribute, external_id=option['id'], text=option['text'])
    selected.refresh_from_db()
    assert optionset.provider.get_options(project, search=selected.external_id) == [{
        'id': selected.external_id, 'text': selected.text,
    }]
    http_get.assert_not_called()
