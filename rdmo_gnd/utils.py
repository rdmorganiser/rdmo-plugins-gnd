import re


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


def is_search_response(data):
    if not isinstance(data, dict):
        return False
    total = data.get('totalItems')
    members = data.get('member')
    return (
        type(total) is int and total >= 0 and isinstance(members, list)
        and len(members) <= total and all(is_record(item) for item in members)
    )
