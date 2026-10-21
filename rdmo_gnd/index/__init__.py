"""Django-independent tools for immutable local GND indexes (optional extra)."""

SCHEMA_VERSION = 1
IMPORTER_VERSION = 1


class IndexErrorBase(ValueError):
    """An unsupported, invalid, or incomplete index or source dataset."""
