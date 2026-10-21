import re

DEFAULT_USER_AGENT = 'rdmo-plugins-gnd; https://github.com/rdmorganiser/rdmo-plugins-gnd'

# Include the entire SubjectHeading family, even when inferred parent types are absent.
SUBJECT_TYPES = frozenset({
    'SubjectHeading', 'SubjectHeadingSensoStricto', 'CharactersOrMorphemes',
    'EthnographicName', 'FictiveTerm', 'GroupOfPersons', 'HistoricSingleEventOrEra',
    'Language', 'MeansOfTransportWithIndividualName', 'NomenclatureInBiologyOrChemistry',
    'ProductNameOrBrandName', 'SoftwareProduct',
})


def get_identifier(value):
    """Recognize bare GND identifiers and canonical GND record URLs."""
    match = re.fullmatch(
        r'(?:https?://d-nb\.info/gnd/)?([0-9]+-[0-9Xx]|[0-9]{8,9}[0-9Xx])/?', value.strip()
    )
    return match[1].upper() if match else None


def is_record(data):
    return isinstance(data, dict) and all(
        isinstance(data.get(field), str) and data[field] for field in ('id', 'gndIdentifier', 'preferredName')
    )


def is_subject_record(data):
    if not is_record(data):
        return False
    types = data.get('type')
    return (
        isinstance(types, list) and all(isinstance(value, str) for value in types)
        and bool(SUBJECT_TYPES.intersection(types))
    )


def is_search_response(data):
    if not isinstance(data, dict):
        return False
    total = data.get('totalItems')
    members = data.get('member')
    return (
        type(total) is int and total >= 0 and isinstance(members, list)
        and len(members) <= total and all(is_record(item) for item in members)
    )
