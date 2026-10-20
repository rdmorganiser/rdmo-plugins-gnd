"""Live subject-heading search using DNB's SRU service and RDF/XML records."""

from unicodedata import normalize
from xml.etree import ElementTree

from .client import GNDHTTPClient
from .utils import get_identifier, is_search_response

SRU_URL = 'https://services.dnb.de/sru/authorities'
RESULT_LIMIT = 10
NAMESPACES = {
    'sru': 'http://www.loc.gov/zing/srw/',
    'diag': 'http://www.loc.gov/zing/srw/diagnostic/',
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
}
GND_NAMESPACES = ('https://d-nb.info/standards/elementset/gnd#', 'http://d-nb.info/standards/elementset/gnd#')
RDF_ABOUT = f'{{{NAMESPACES["rdf"]}}}about'
RDF_RESOURCE = f'{{{NAMESPACES["rdf"]}}}resource'

# DNB's RDF records carry the specific type, rather than its inferred parents.
# Include the entire SubjectHeading family, not only SubjectHeadingSensoStricto.
SUBJECT_TYPES = frozenset({
    'SubjectHeading', 'SubjectHeadingSensoStricto', 'CharactersOrMorphemes',
    'EthnographicName', 'FictiveTerm', 'GroupOfPersons', 'HistoricSingleEventOrEra',
    'Language', 'MeansOfTransportWithIndividualName', 'NomenclatureInBiologyOrChemistry',
    'ProductNameOrBrandName', 'SoftwareProduct',
})


class _SRUTreeBuilder(ElementTree.TreeBuilder):
    def doctype(self, name, pubid, system):
        # SRU/RDF needs no DTDs or entity declarations.
        raise ValueError('Unexpected XML document type')


def _gnd_text(element, name):
    for namespace in GND_NAMESPACES:
        value = element.findtext(f'{{{namespace}}}{name}')
        if value is not None:
            return value
    return None


def _parse_record(record):
    if record.findtext('sru:recordSchema', namespaces=NAMESPACES) != 'RDFxml':
        raise ValueError('Unexpected record schema')
    descriptions = record.findall('sru:recordData/rdf:RDF/rdf:Description', NAMESPACES)
    resources = [item for item in descriptions if get_identifier(item.get(RDF_ABOUT, '')) is not None]
    if len(resources) != 1:
        raise ValueError('Expected one GND resource per record')
    resource = resources[0]
    identifier = _gnd_text(resource, 'gndIdentifier')
    if not identifier or get_identifier(identifier) != get_identifier(resource.get(RDF_ABOUT)):
        raise ValueError('Missing or inconsistent GND identifier')
    identifier = get_identifier(identifier)

    types = {
        uri.removeprefix(namespace)
        for element in resource.findall('rdf:type', NAMESPACES)
        for uri in (element.get(RDF_RESOURCE, ''),)
        for namespace in GND_NAMESPACES if uri.startswith(namespace)
    }
    if not types:
        raise ValueError('Missing GND entity type')
    if not SUBJECT_TYPES.intersection(types):
        return None

    name = _gnd_text(resource, 'preferredNameForTheSubjectHeading')
    if not name or not name.strip():
        raise ValueError('Missing preferred subject name')
    return {
        'id': f'https://d-nb.info/gnd/{identifier}',
        'gndIdentifier': identifier,
        'preferredName': normalize('NFC', name),
        'type': sorted(types),
    }


def _parse_response(content):
    try:
        root = ElementTree.fromstring(content, parser=ElementTree.XMLParser(target=_SRUTreeBuilder()))
    except ElementTree.ParseError as error:
        raise ValueError('Invalid XML') from error
    if root.tag != f'{{{NAMESPACES["sru"]}}}searchRetrieveResponse':
        raise ValueError('Unexpected SRU response')
    if root.find('.//diag:diagnostic', NAMESPACES) is not None:
        raise ValueError('SRU diagnostic response')
    total = int(root.findtext('sru:numberOfRecords', default='', namespaces=NAMESPACES))
    records = root.findall('sru:records/sru:record', NAMESPACES)
    if total < len(records) or (total > 0 and not records):
        raise ValueError('Inconsistent SRU record count')
    members = []
    for record in records:
        item = _parse_record(record)
        if item is not None:
            members.append(item)
    return {'totalItems': total, 'member': members}


def _is_subject_response(data):
    if not is_search_response(data):
        return False
    return all(
        isinstance(item.get('type'), list)
        and all(isinstance(value, str) for value in item['type'])
        and SUBJECT_TYPES.intersection(item['type'])
        and get_identifier(item['gndIdentifier']) == item['gndIdentifier']
        and item['id'] == f'https://d-nb.info/gnd/{item["gndIdentifier"]}'
        for item in data['member']
    )


def _cql_literal(value):
    # Quoting alone does not neutralize CQL wildcard and anchor characters.
    return '"' + ''.join(f'\\{char}' if char in '\\"*?^' else char for char in value) + '"'


class GNDSRUClient(GNDHTTPClient):
    """Return subject headings in the same record shape as the Lobid client."""

    parse_error_message = 'invalid SRU response'

    def __init__(self):
        super().__init__()
        self.url = SRU_URL
        # Other headers may be specific to a deployment's Lobid endpoint.
        self.headers = {'User-Agent': self.headers['User-Agent']}

    def search(self, query):
        query = query.strip()
        if len(query) < self.min_len:
            return None
        members = {}
        for index in ('an', 'sw'):
            data = self._query(index, query)
            if data is None:
                return None
            for item in data['member']:
                members.setdefault(item['gndIdentifier'], item)
        items = list(members.values())[:RESULT_LIMIT]
        # This count describes the merged options, not the global SRU hit count.
        return {'totalItems': len(items), 'member': items}

    def get_record(self, identifier):
        identifier = get_identifier(identifier)
        if identifier is None:
            return None
        data = self._query('nid', identifier)
        return data['member'][0] if data is not None and data['member'] else None

    def _query(self, index, value):
        is_identifier = index == 'nid'
        limit = 1 if is_identifier else RESULT_LIMIT

        def validate(data):
            return (
                _is_subject_response(data) and len(data['member']) <= limit
                and (not is_identifier or all(item['gndIdentifier'] == value for item in data['member']))
            )

        params = {
            'version': '1.1', 'operation': 'searchRetrieve', 'recordSchema': 'RDFxml',
            'maximumRecords': limit,
            'query': f'dnb.{index} = {_cql_literal(value)} and dnb.bbg = Ts*',
        }
        kind = 'sru-record' if is_identifier else 'sru-search'
        return self._get(kind, '', params, validate)

    def _parse_response(self, response):
        return _parse_response(response.content)
