"""Validate complete DNB snapshots and atomically publish immutable generations."""

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import requests

from ..utils import DEFAULT_USER_AGENT
from . import IMPORTER_VERSION, SCHEMA_VERSION, IndexErrorBase
from .database import build_index
from .search import GNDIndex

SOURCE_BASE = 'https://data.dnb.de/GND/'
MANIFEST_URL = SOURCE_BASE + '001_Pruefsumme_Checksum.txt'
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
MIN_FREE_BYTES = 2 * 1024**3
TIMEOUT = (5, 60)
logger = logging.getLogger(__name__)
NAME_PATTERN = r'authorities-gnd-sachbegriff_dnbmarc_(\d{8})\.mrc\.xml\.gz'


def now():
    return datetime.now(UTC).isoformat()


def atomic_json(path, data):
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('x') as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_state(directory):
    try:
        data = json.loads((directory / 'state.json').read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def snapshot_metadata(listing, manifest):
    entries = []
    for line in manifest.splitlines():
        match = re.fullmatch(r'\s*([0-9a-fA-F]{64})\s+\*?(' + NAME_PATTERN + r')\s*', line)
        if match:
            entries.append((match[3], match[2], match[1].lower()))
    if not entries:
        raise IndexErrorBase('No dated subject-heading MARC21 XML checksum found.')
    date, filename, checksum = max(entries)
    # The count is attached to this exact dated filename, never to the stable alias.
    description = re.search(r'href="' + re.escape(filename) + r'"[^\n]*', listing)
    text = unescape(re.sub('<[^>]*>', '', description[0])) if description else ''
    count = re.search(r'\b([0-9][0-9.,]*)\s+(?:Datensaetze|Datensätze|records)\b', text, re.IGNORECASE)
    if not count:
        raise IndexErrorBase('Expected record count is missing from the dated DNB directory entry.')
    snapshot_date = datetime.strptime(date, '%Y%m%d').date().isoformat()
    return {
        'source_url': SOURCE_BASE + filename, 'source_sha256': checksum,
        'source_snapshot_date': snapshot_date, 'expected_records': int(re.sub(r'\D', '', count[1])),
    }


def fetch_metadata(session, url, cached):
    headers = {}
    if cached.get('etag'):
        headers['If-None-Match'] = cached['etag']
    elif cached.get('last_modified'):
        headers['If-Modified-Since'] = cached['last_modified']
    with session.get(url, headers=headers, timeout=TIMEOUT) as response:
        if response.status_code == 304 and isinstance(cached.get('text'), str):
            return cached
        response.raise_for_status()
        if response.status_code == 304:
            raise IndexErrorBase('DNB returned 304 without cached metadata.')
        if len(response.content) > 2 * 1024 * 1024:
            raise IndexErrorBase('DNB metadata response is unexpectedly large.')
        return {'text': response.text, 'etag': response.headers.get('ETag'),
                'last_modified': response.headers.get('Last-Modified')}


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(session, source, directory):
    target = directory / Path(source['source_url']).name
    checksum = source['source_sha256']
    if target.is_file() and target.stat().st_size <= MAX_DOWNLOAD_BYTES and sha256(target) == checksum:
        return target
    temporary = directory / f'.download-{uuid.uuid4().hex}.tmp'
    try:
        with session.get(source['source_url'], stream=True, timeout=TIMEOUT) as response:
            response.raise_for_status()
            expected_size = int(response.headers.get('Content-Length', '0'))
            if expected_size > MAX_DOWNLOAD_BYTES:
                raise IndexErrorBase('DNB archive exceeds the download limit.')
            size, digest = 0, hashlib.sha256()
            with temporary.open('xb') as stream:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise IndexErrorBase('DNB archive exceeds the download limit.')
                    stream.write(chunk)
                    digest.update(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if expected_size and size != expected_size:
                raise IndexErrorBase('Incomplete DNB archive download.')
            if digest.hexdigest() != checksum:
                raise IndexErrorBase('DNB archive SHA-256 checksum does not match.')
        os.replace(temporary, target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def validate_import(metadata, previous=None):
    expected, imported = metadata['expected_records'], metadata['concepts_imported']
    if not expected or metadata['records_parsed'] != expected:
        raise IndexErrorBase('Parsed record count differs from the published DNB count.')
    if sum(metadata[key] for key in ('concepts_imported', 'records_filtered', 'records_skipped')) != expected:
        raise IndexErrorBase('Import outcome counts are inconsistent.')
    if imported < 100_000 or imported < expected * 0.99:
        raise IndexErrorBase('Too few subject headings imported; inspect the import counters.')
    if previous and imported < previous['concepts_imported'] * 0.9:
        raise IndexErrorBase('Subject-heading count fell by more than 10 percent.')


def validate_search(path):
    index = GNDIndex(path)
    if not index.get('4008186-2'):
        raise IndexErrorBase('Known GND identifier is missing from the new index.')
    for query in ('Brennstoff', 'fuel'):
        if '4008186-2' not in {record['gndIdentifier'] for record in index.search(query)}:
            raise IndexErrorBase(f'Multilingual smoke test failed for {query!r}.')


def publish(directory, staging):
    generation = directory / 'versions' / f'gnd-{uuid.uuid4().hex}.sqlite'
    os.chmod(staging, 0o640)
    with staging.open('rb') as stream:
        os.fsync(stream.fileno())
    os.replace(staging, generation)
    temporary = directory / f'.current-{uuid.uuid4().hex}'
    try:
        temporary.symlink_to(generation.relative_to(directory))
        os.replace(temporary, directory / 'current.sqlite')
        # Persist both the new generation and the pointer across a machine restart.
        for path in (generation.parent, directory):
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return generation


def prune(directory):
    active = (directory / 'current.sqlite').resolve(strict=True)
    versions = sorted((directory / 'versions').glob('gnd-*.sqlite'), key=lambda p: p.stat().st_mtime, reverse=True)
    retained = {active, *[path for path in versions if path != active][:1]}
    for path in versions:
        if path not in retained and time.time() - path.stat().st_mtime > 24 * 60 * 60:
            path.unlink()


@contextmanager
def update_lock(directory):
    with (directory / '.update.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise IndexErrorBase('Another GND updater is already running.') from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def current_metadata(directory):
    try:
        return GNDIndex(directory / 'current.sqlite').metadata()
    except (OSError, ValueError, sqlite3.Error):
        return None


def update_index(directory, force=False):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o750)
    with update_lock(directory):
        return _update_index(directory, force)


def _update_index(directory, force):
    for name in ('versions', 'downloads'):
        (directory / name).mkdir(exist_ok=True, mode=0o750)
    state = load_state(directory)
    state['last_checked'] = now()
    staging = directory / 'versions' / f'.building-{uuid.uuid4().hex}.sqlite'
    try:
        with requests.Session() as session:
            session.headers['User-Agent'] = DEFAULT_USER_AGENT
            remote = state.setdefault('remote', {})
            remote['listing'] = fetch_metadata(session, SOURCE_BASE, remote.get('listing', {}))
            remote['manifest'] = fetch_metadata(session, MANIFEST_URL, remote.get('manifest', {}))
            source = snapshot_metadata(remote['listing']['text'], remote['manifest']['text'])
            previous = current_metadata(directory)
            if (not force and previous and previous.get('source_sha256') == source['source_sha256']
                    and previous.get('schema_version') == SCHEMA_VERSION
                    and previous.get('importer_version') == IMPORTER_VERSION):
                # Metadata alone does not establish that an active database is usable.
                try:
                    validate_search(directory / 'current.sqlite')
                except (OSError, ValueError, sqlite3.Error):
                    pass  # Rebuild an unusable database even when DNB metadata is unchanged.
                else:
                    state['last_error'] = None
                    atomic_json(directory / 'state.json', state)
                    return {'changed': False, **previous}
            if shutil.disk_usage(directory).free < MIN_FREE_BYTES:
                raise IndexErrorBase('At least 2 GiB free disk space is required to build a production index.')
            archive = download(session, source, directory / 'downloads')
        metadata = build_index(archive, staging, source)
        state['last_import'] = {key: metadata[key] for key in (
            'expected_records', 'records_parsed', 'concepts_imported', 'records_filtered', 'records_skipped',
            'skipped_reasons',
        )}
        validate_import(metadata, previous)
        validate_search(staging)
        generation = publish(directory, staging)
        state.update(last_error=None, last_successful_build=metadata['built_at'], generation=generation.name)
        atomic_json(directory / 'state.json', state)
        prune(directory)
        return {'changed': True, **metadata}
    except Exception as error:
        state['last_error'] = {'at': now(), 'type': type(error).__name__, 'message': str(error)}
        try:
            atomic_json(directory / 'state.json', state)
        except OSError:
            logger.exception('Could not record GND updater failure in state.json.')
        raise
    finally:
        staging.unlink(missing_ok=True)
