from django.core.checks import register

from .config import load_settings


@register()
def check_gnd_settings(app_configs, **kwargs):
    return load_settings()[1]


@register()
def check_local_index(app_configs, **kwargs):
    import sqlite3
    from datetime import UTC, datetime
    from pathlib import Path

    from django.conf import settings
    from django.core.checks import Error, Warning

    path = getattr(settings, 'GND_INDEX_PATH', None)
    if path is None:
        return []
    if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
        return [Error('GND_INDEX_PATH must be an absolute path.', id='rdmo_gnd.E002')]
    max_age = getattr(settings, 'GND_INDEX_MAX_AGE_DAYS', 30)
    if type(max_age) is not int or max_age < 1:
        return [Error('GND_INDEX_MAX_AGE_DAYS must be a positive integer.', id='rdmo_gnd.E002')]
    try:
        import defusedxml  # noqa: F401

        from .index.search import GNDIndex
        metadata = GNDIndex(path).metadata()
        # Exercise FTS5 as well as schema compatibility without an expensive integrity scan.
        GNDIndex(path).search('Brennstoff', limit=1)
    except ImportError:
        return [Error('Install rdmo-plugins-gnd[local] to use GND_INDEX_PATH.', id='rdmo_gnd.E002')]
    except (OSError, sqlite3.Error, ValueError) as error:
        return [Warning(f'Local GND index unavailable: {error}', id='rdmo_gnd.W001')]
    date = metadata.get('source_snapshot_date')
    try:
        age = (datetime.now(UTC).date() - datetime.fromisoformat(date).date()).days
    except (TypeError, ValueError):
        return [Warning('Local GND index has no valid source snapshot date.', id='rdmo_gnd.W002')]
    if age > max_age:
        return [Warning(f'Local GND snapshot is {age} days old (limit {max_age}).', id='rdmo_gnd.W002')]
    return []
