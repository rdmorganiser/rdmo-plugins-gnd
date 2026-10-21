import gzip
from pathlib import Path

import pytest

from defusedxml.common import DefusedXmlException

from rdmo_gnd.index.marc import ImportStats, iter_concepts

FIXTURE = Path(__file__).parent / 'fixtures/marc/subjects.xml'


@pytest.fixture
def marc_source(tmp_path):
    path = tmp_path / 'subjects.xml'
    path.write_bytes(FIXTURE.read_bytes())
    return path


def test_extracts_multilingual_subjects_and_canonical_identifier():
    stats = ImportStats()
    records = {record.gnd_id: record for record in iter_concepts(FIXTURE, stats)}
    fuel = records['4008186-2']
    assert fuel.preferred_name == 'Brennstoff'
    assert any(label.value == 'Fuel' and label.source == 'lcsh' and label.language == 'eng'
               and label.relation == 'EQ' for label in fuel.labels)
    assert '040081869' not in records
    assert stats.concepts_imported == stats.records_parsed == 5
    assert stats.records_filtered == stats.records_skipped == 0


def test_gzip_and_sru_wrapper(tmp_path):
    wrapped = b'<root>' + FIXTURE.read_bytes().split(b'?>', 1)[1] + b'</root>'
    path = tmp_path / 'wrapped.xml.gz'
    path.write_bytes(gzip.compress(wrapped))
    assert len(list(iter_concepts(path))) == 5


def test_subdivisions_qualifications_unicode_and_mapping_relations(marc_source):
    marc_source.write_text('''<collection xmlns="http://www.loc.gov/MARC21/slim"><record>
      <datafield tag="024"><subfield code="a">4434275-5</subfield><subfield code="2">gnd</subfield></datafield>
      <datafield tag="075"><subfield code="b">s</subfield><subfield code="2">gndgen</subfield></datafield>
      <datafield tag="150"><subfield code="a">Python</subfield><subfield code="g">Programmiersprache</subfield>
        <subfield code="x">Einführung</subfield><subfield code="y">2020</subfield><subfield code="z">Bonn</subfield>
      </datafield>
      <datafield tag="450"><subfield code="a">Brennstoff</subfield><subfield code="x">Einsparung</subfield></datafield>
      <datafield tag="750"><subfield code="a">Fuel</subfield><subfield code="4">=EQ</subfield>
        <subfield code="2">lcsh</subfield><subfield code="9">L:eng</subfield></datafield>
    </record></collection>''')
    record, = iter_concepts(marc_source)
    assert record.preferred_name == 'Python <Programmiersprache> -- Einführung -- 2020 -- Bonn'
    assert record.labels[1].value == 'Brennstoff -- Einsparung'
    assert record.labels[2].relation == '=EQ'
    assert not any(label.value == 'Brennstoff' for label in record.labels)


@pytest.mark.parametrize('replacement, filtered, skipped', [
    ('<subfield code="b">p</subfield>', 5, 0),
    ('<subfield code="b">s</subfield>', 0, 0),
])
def test_entity_classification(marc_source, replacement, filtered, skipped):
    text = marc_source.read_text().replace('<subfield code="b">s</subfield>', replacement)
    marc_source.write_text(text)
    stats = ImportStats()
    list(iter_concepts(marc_source, stats))
    assert stats.records_filtered == filtered
    assert stats.records_skipped == skipped


def test_missing_identifier_is_counted(marc_source):
    marc_source.write_text(marc_source.read_text().replace('tag="024"', 'tag="999"'))
    stats = ImportStats()
    assert list(iter_concepts(marc_source, stats)) == []
    assert stats.records_skipped == stats.records_parsed == 5


@pytest.mark.parametrize('declaration', [
    '<!DOCTYPE root>',
    '<!DOCTYPE root [<!ENTITY example "value">]>',
    '<!DOCTYPE root SYSTEM "file:///etc/passwd">',
])
def test_xml_rejects_dtd_entities_and_external_references(tmp_path, declaration):
    path = tmp_path / 'unsafe.xml'
    path.write_text(declaration + '<root/>')
    with pytest.raises(DefusedXmlException):
        list(iter_concepts(path))
