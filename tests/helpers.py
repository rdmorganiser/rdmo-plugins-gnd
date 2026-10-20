import json
from xml.etree import ElementTree

from requests import Response


def record():
    return {
        'id': 'https://d-nb.info/gnd/2129905-5',
        'gndIdentifier': '2129905-5',
        'preferredName': 'Zeolite (2 : 1985 : Budapest)',
    }


def search_response():
    return {'totalItems': 1, 'member': [record()]}


def response(data, status=200):
    result = Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    result.headers['Content-Type'] = 'application/json'
    return result


def xml_response(content, status=200):
    result = Response()
    result.status_code = status
    result._content = content.encode() if isinstance(content, str) else content
    result.headers['Content-Type'] = 'application/xml'
    return result


def subject_record(identifier='4008186-2', name='Brennstoff', entity_type='SubjectHeadingSensoStricto'):
    return {
        'id': f'https://d-nb.info/gnd/{identifier}', 'gndIdentifier': identifier,
        'preferredName': name, 'type': [entity_type],
    }


def sru_response(items=(), total=None, gnd_namespace='https://d-nb.info/standards/elementset/gnd#'):
    sru = '{http://www.loc.gov/zing/srw/}'
    rdf = '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}'
    root = ElementTree.Element(f'{sru}searchRetrieveResponse')
    ElementTree.SubElement(root, f'{sru}version').text = '1.1'
    ElementTree.SubElement(root, f'{sru}numberOfRecords').text = str(len(items) if total is None else total)
    records = ElementTree.SubElement(root, f'{sru}records')
    for item in items:
        record = ElementTree.SubElement(records, f'{sru}record')
        ElementTree.SubElement(record, f'{sru}recordSchema').text = 'RDFxml'
        ElementTree.SubElement(record, f'{sru}recordPacking').text = 'xml'
        data = ElementTree.SubElement(record, f'{sru}recordData')
        graph = ElementTree.SubElement(data, f'{rdf}RDF')
        resource = ElementTree.SubElement(graph, f'{rdf}Description', {f'{rdf}about': item.get('id', '')})
        for entity_type in item.get('type', []):
            ElementTree.SubElement(resource, f'{rdf}type', {f'{rdf}resource': f'{gnd_namespace}{entity_type}'})
        for key, field in (('gndIdentifier', 'gndIdentifier'), ('preferredName', 'preferredNameForTheSubjectHeading')):
            if key in item:
                ElementTree.SubElement(resource, f'{{{gnd_namespace}}}{field}').text = item[key]
    return xml_response(ElementTree.tostring(root))
