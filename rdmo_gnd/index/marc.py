"""Stream GND subject headings from MARC21 XML, including SRU envelopes."""

import gzip
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from defusedxml.ElementTree import iterparse

from ..utils import get_identifier

MARC = '{http://www.loc.gov/MARC21/slim}'


def display_text(value):
    return ' '.join(unicodedata.normalize('NFC', value).split())


def normalize(value):
    return display_text(value).casefold()


@dataclass(frozen=True)
class GNDLabel:
    value: str
    kind: str
    language: str = ''
    source: str = ''
    relation: str = ''


@dataclass
class GNDConcept:
    gnd_id: str
    preferred_name: str
    labels: list[GNDLabel]
    modified_at: str = ''


@dataclass
class ImportStats:
    records_parsed: int = 0
    concepts_imported: int = 0
    records_filtered: int = 0
    records_skipped: int = 0
    expected_records: int | None = None
    # Reasons are useful when a new export format causes validation to fail.
    skipped_reasons: dict[str, int] = field(default_factory=dict)

    def skip(self, reason):
        self.records_skipped += 1
        self.skipped_reasons[reason] = self.skipped_reasons.get(reason, 0) + 1


def subfields(element, code):
    return [display_text(item.text or '') for item in element.findall(f'{MARC}subfield[@code="{code}"]')]


def first(element, code):
    return next(iter(subfields(element, code)), '')


def heading(element):
    """Keep qualifications and subdivisions attached to their complete heading."""
    parts = []
    for item in element.findall(f'{MARC}subfield'):
        code, value = item.get('code'), display_text(item.text or '')
        if not value:
            continue
        if code in ('a', 'b'):
            parts.append(value)
        elif code == 'g':
            parts.append(f'<{value}>')
        elif code in ('v', 'x', 'y', 'z'):
            parts.append(f'-- {value}')
    return ' '.join(parts) if first(element, 'a') else ''


def parse_record(record, stats):
    stats.records_parsed += 1
    fields = record.findall(f'{MARC}datafield')
    if not any(f.get('tag') == '075' and first(f, '2') == 'gndgen' and first(f, 'b') == 's' for f in fields):
        stats.records_filtered += 1
        return None
    identifier = next((first(f, 'a') for f in fields if f.get('tag') == '024' and first(f, '2') == 'gnd'), '')
    identifier = get_identifier(identifier)
    name = next((heading(f) for f in fields if f.get('tag') == '150'), '')
    if not identifier or not name:
        stats.skip('missing_identifier_or_heading')
        return None
    labels = [GNDLabel(name, 'preferred', 'de', 'gnd')]
    for element in fields:
        tag, value = element.get('tag'), heading(element)
        if not value or tag not in ('450', '750'):
            continue
        language = next((value[2:] for value in subfields(element, '9') if value.startswith('L:')), '')
        labels.append(GNDLabel(
            value, 'variant' if tag == '450' else 'equivalent', language,
            first(element, '2') or ('gnd' if tag == '450' else ''), first(element, '4'),
        ))
    stats.concepts_imported += 1
    return GNDConcept(
        identifier, name, list(dict.fromkeys(labels)),
        record.findtext(f'{MARC}controlfield[@tag="005"]', default=''),
    )


def iter_concepts(source, stats=None):
    stats = stats if stats is not None else ImportStats()
    path = Path(source)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rb') as stream:
        stack = []
        for event, element in iterparse(
            stream, events=('start', 'end'), forbid_dtd=True, forbid_entities=True, forbid_external=True,
        ):
            if event == 'start':
                stack.append(element)
                continue
            if element.tag == f'{MARC}record':
                concept = parse_record(element, stats)
                if concept is not None:
                    yield concept
                if len(stack) > 1:
                    stack[-2].remove(element)
                element.clear()
            elif not any(parent.tag == f'{MARC}record' for parent in stack[:-1]):
                # Also discard completed SRU wrappers, rather than retaining empty records.
                if len(stack) > 1:
                    stack[-2].remove(element)
                element.clear()
            stack.pop()
