"""Django-independent tools for immutable local GND indexes (optional extra)."""

SCHEMA_VERSION = 2
IMPORTER_VERSION = 2


class IndexErrorBase(ValueError):
    """An unsupported, invalid, or incomplete index or source dataset."""
