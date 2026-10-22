import sqlite3
from pathlib import Path
from xml.etree.ElementTree import ParseError

import pytest

from rdmo_gnd.index import IndexErrorBase
from rdmo_gnd.index.database import build_index, open_index
from rdmo_gnd.index.search import GNDIndex

FIXTURE = Path(__file__).parent / 'fixtures/marc/subjects.xml'


@pytest.mark.parametrize(
    'query, expected',
    [
        ('Brennstoff', '4008186-2'),
        ('fuel', '4008186-2'),
        ('FUEL', '4008186-2'),
        ('fuel ce', '4008195-3'),
        ('fuel cell', '4008195-3'),
        ('brenn', '4008186-2'),
        ('Heizöl', '4235008-6'),
        ('heizol', '4235008-6'),
        ('4008186-2', '4008186-2'),
        ('https://d-nb.info/gnd/4008186-2', '4008186-2'),
    ],
)
def test_multilingual_exact_prefix_unicode_and_identifier_search(local_index, query, expected):
    assert expected in {record['gndIdentifier'] for record in local_index.search(query)}


def test_ranking_and_deduplication_precede_limit(local_index):
    results = local_index.search('fuel', limit=2)
    assert {record['gndIdentifier'] for record in results} == {'4008186-2', '4132517-5'}
    assert local_index.search('Brennstoff')[0]['gndIdentifier'] == '4008186-2'
    assert len(local_index.search('fuel')) == len({item['gndIdentifier'] for item in local_index.search('fuel')})


@pytest.mark.parametrize('query', ['', '   ', '"', '(', '*', '" OR NOT ()', 'a' * 201, 'a ' * 13])
def test_unsafe_or_excessive_queries_are_harmless(local_index, query):
    assert local_index.search(query) == []


def test_get_and_missing_variant_values(local_index):
    fuel = local_index.get('4008186-2')
    assert fuel['preferredName'] == 'Brennstoff'
    assert fuel['id'] == 'https://d-nb.info/gnd/4008186-2'
    assert local_index.get('9999999-0') is None
    assert local_index.get('not-an-id') is None


def test_reader_is_readonly_and_does_not_create_missing_database(tmp_path, local_index):
    with open_index(local_index.path) as db, pytest.raises(sqlite3.OperationalError):
        db.execute('DELETE FROM concept')
    missing = tmp_path / 'missing.sqlite'
    with pytest.raises(FileNotFoundError):
        GNDIndex(missing).get('4008186-2')
    assert not missing.exists()


def test_build_refuses_overwrite(local_index):
    before = local_index.path.read_bytes()
    with pytest.raises(FileExistsError):
        build_index(FIXTURE, local_index.path)
    assert local_index.path.read_bytes() == before


def test_duplicate_identifiers_and_malformed_sources_remove_staging(tmp_path):
    xml = FIXTURE.read_text()
    duplicate = xml.replace('</collection>', '') + xml[xml.index('  <record ') :]
    for name, text in [('duplicate', duplicate), ('malformed', '<broken>')]:
        source = tmp_path / f'{name}.xml'
        output = tmp_path / f'{name}.sqlite'
        source.write_text(text)
        with pytest.raises((sqlite3.IntegrityError, ParseError)):
            build_index(source, output)
        assert not output.exists()


def test_empty_subject_set_rejected(tmp_path):
    source = tmp_path / 'empty.xml'
    source.write_text('<collection/>')
    with pytest.raises(IndexErrorBase, match='No subject'):
        build_index(source, tmp_path / 'empty.sqlite')


def test_unsupported_schema_is_rejected(local_index):
    with sqlite3.connect(local_index.path) as connection:
        connection.execute("UPDATE metadata SET value = '999' WHERE key = 'schema_version'")
    with pytest.raises(IndexErrorBase, match='schema'):
        local_index.get('4008186-2')


@pytest.mark.parametrize('query', ['Straße', 'straße', 'STRASSE', 'strasse', 'STRASS', 'straß'])
def test_casefolded_candidates_and_ranking_preserve_display_text(tmp_path, query):
    source = tmp_path / 'casefold.xml'
    # Keep the fixture's real MARC layout and exercise the importer, not hand-built SQL tables.
    source.write_text(FIXTURE.read_text().replace('Brennstoff', 'Straße'))
    path = tmp_path / 'casefold.sqlite'
    build_index(source, path)
    result = GNDIndex(path).search(query)
    assert result[0]['gndIdentifier'] == '4008186-2'
    assert result[0]['preferredName'] == 'Straße'


def test_version_one_artifacts_require_rebuilding(local_index):
    with sqlite3.connect(local_index.path) as connection:
        connection.execute("UPDATE metadata SET value = '1' WHERE key = 'schema_version'")
    with pytest.raises(IndexErrorBase, match='rebuild'):
        local_index.search('fuel')
