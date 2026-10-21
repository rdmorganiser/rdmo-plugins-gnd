"""Build and validate generated SQLite databases using sqlite-utils."""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlite_utils import Database

from . import IMPORTER_VERSION, SCHEMA_VERSION, IndexErrorBase
from .marc import ImportStats, iter_concepts, normalize


def check_sqlite():
    if sqlite3.sqlite_version_info < (3, 35, 0):
        raise IndexErrorBase('SQLite 3.35 or newer is required.')


@contextmanager
def open_index(path):
    check_sqlite()
    resolved = Path(path).resolve(strict=True)
    connection = sqlite3.connect(f'{resolved.as_uri()}?mode=ro&immutable=1', uri=True)
    with Database(connection) as db:
        metadata = read_metadata(db)
        if metadata.get('schema_version') != SCHEMA_VERSION:
            raise IndexErrorBase('Unsupported GND index schema; rebuild the index.')
        yield db


def read_metadata(db):
    return {row['key']: json.loads(row['value']) for row in db['metadata'].rows}


def validate_database(db):
    if list(db.query('PRAGMA integrity_check')) != [{'integrity_check': 'ok'}]:
        raise IndexErrorBase('SQLite integrity check failed.')
    if list(db.query('PRAGMA foreign_key_check')):
        raise IndexErrorBase('Foreign key check failed.')
    db.execute("INSERT INTO label_fts(label_fts, rank) VALUES('integrity-check', 1)")


def build_index(source, output, metadata=None):
    """Build a new artifact; never overwrite a published or existing database."""
    check_sqlite()
    output = Path(output)
    # Exclusive creation prevents accidental replacement, even when two builders race.
    with output.open('xb'):
        pass
    stats = ImportStats(expected_records=(metadata or {}).get('expected_records'))
    try:
        with Database(output) as db:
            db.execute('PRAGMA foreign_keys = ON')
            db.execute('PRAGMA temp_store = FILE')
            db['concept'].create({
                'gnd_id': str, 'preferred_name': str, 'normalized': str, 'modified_at': str,
            }, pk='gnd_id', not_null={'preferred_name', 'normalized'})
            db['label'].create({
                'id': int, 'gnd_id': str, 'value': str, 'normalized': str,
                'kind': str, 'language': str, 'source': str, 'relation': str,
            }, pk='id', foreign_keys=[('gnd_id', 'concept', 'gnd_id')], not_null={'gnd_id', 'value', 'kind'})
            db['metadata'].create({'key': str, 'value': str}, pk='key')
            batch = []
            for concept in iter_concepts(source, stats):
                batch.append(concept)
                if len(batch) == 1000:
                    insert_batch(db, batch)
                    batch.clear()
            if batch:
                insert_batch(db, batch)
            if not stats.concepts_imported:
                raise IndexErrorBase('No subject headings found in the source.')
            db['label'].create_index(['gnd_id'])
            db['label'].create_index(['normalized'])
            db['label'].enable_fts(
                ['value'], fts_version='FTS5', tokenize='unicode61 remove_diacritics 2', create_triggers=False,
            )
            db['label'].optimize()
            values = {
                **(metadata or {}), **asdict(stats), 'schema_version': SCHEMA_VERSION,
                'importer_version': IMPORTER_VERSION, 'built_at': datetime.now(UTC).isoformat(),
            }
            db['metadata'].insert_all([{'key': key, 'value': json.dumps(value)} for key, value in values.items()])
            validate_database(db)
            db.conn.commit()
        return values
    except BaseException:
        output.unlink(missing_ok=True)
        Path(f'{output}-journal').unlink(missing_ok=True)
        raise


def insert_batch(db, concepts):
    with db.atomic():
        db['concept'].insert_all([{
            'gnd_id': c.gnd_id, 'preferred_name': c.preferred_name,
            'normalized': normalize(c.preferred_name), 'modified_at': c.modified_at,
        } for c in concepts])
        db['label'].insert_all([{
            'gnd_id': c.gnd_id, 'value': label.value, 'normalized': normalize(label.value),
            'kind': label.kind, 'language': label.language, 'source': label.source, 'relation': label.relation,
        } for c in concepts for label in c.labels])
