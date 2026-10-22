import gzip
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from rdmo_gnd.index import IMPORTER_VERSION, SCHEMA_VERSION, IndexErrorBase
from rdmo_gnd.index.database import build_index, open_index
from rdmo_gnd.index.search import GNDIndex
from rdmo_gnd.index.update import (
    atomic_json,
    download,
    fetch_metadata,
    prune,
    publish,
    snapshot_metadata,
    update_index,
    update_lock,
    validate_import,
)

FIXTURE = Path(__file__).parent / 'fixtures/marc/subjects.xml'
FILENAME = 'authorities-gnd-sachbegriff_dnbmarc_20261013.mrc.xml.gz'


def test_remote_metadata_uses_exact_dated_filename_and_count():
    manifest = f'{"a" * 64}  {FILENAME}'
    listing = f'<a href="{FILENAME}">{FILENAME}</a> 2026-10-20 46M 208435 Datensaetze / GND'
    result = snapshot_metadata(listing, manifest)
    assert result['expected_records'] == 208435
    assert result['source_snapshot_date'] == '2026-10-13'
    assert result['source_url'].endswith(FILENAME)
    with pytest.raises(IndexErrorBase, match='record count'):
        snapshot_metadata('', manifest)
    with pytest.raises(IndexErrorBase, match='checksum'):
        snapshot_metadata(listing, 'bad manifest')


@pytest.mark.parametrize(
    'changes',
    [
        {'records_parsed': 208434},
        {'concepts_imported': 100},
        {'concepts_imported': 200000, 'records_skipped': 8435},
    ],
)
def test_validation_rejects_incomplete_imports(changes):
    metadata = {
        'expected_records': 208435,
        'records_parsed': 208435,
        'concepts_imported': 208435,
        'records_filtered': 0,
        'records_skipped': 0,
    }
    with pytest.raises(IndexErrorBase):
        validate_import({**metadata, **changes})


def test_validation_compares_previous_generation():
    metadata = {
        'expected_records': 208435,
        'records_parsed': 208435,
        'concepts_imported': 208435,
        'records_filtered': 0,
        'records_skipped': 0,
    }
    validate_import(metadata)
    with pytest.raises(IndexErrorBase, match='10 percent'):
        validate_import(metadata, {'concepts_imported': 300000})


def test_atomic_publication_old_readers_finish_and_new_readers_switch(tmp_path):
    versions = tmp_path / 'versions'
    versions.mkdir()
    old = versions / 'old.sqlite'
    build_index(FIXTURE, old)
    first = publish(tmp_path, old)
    with open_index(tmp_path / 'current.sqlite') as old_db:
        replacement = versions / 'replacement.sqlite'
        build_index(FIXTURE, replacement)
        with sqlite3.connect(replacement) as connection:
            connection.execute("UPDATE concept SET preferred_name = 'New name' WHERE gnd_id = '4008186-2'")
        second = publish(tmp_path, replacement)
        assert GNDIndex(tmp_path / 'current.sqlite').get('4008186-2')['preferredName'] == 'New name'
        assert old_db['concept'].get('4008186-2')['preferred_name'] == 'Brennstoff'
        assert first != second
    prune(tmp_path)
    assert first.exists()
    assert second.exists()


def test_failed_symlink_switch_leaves_current_active(tmp_path, mocker):
    (tmp_path / 'versions').mkdir()
    old = tmp_path / 'versions/old.sqlite'
    build_index(FIXTURE, old)
    publish(tmp_path, old)
    target = (tmp_path / 'current.sqlite').resolve()
    staging = tmp_path / 'versions/new.sqlite'
    build_index(FIXTURE, staging)
    real_replace = os.replace

    def fail_pointer(source, destination):
        if Path(destination).name == 'current.sqlite':
            raise PermissionError('simulated permission failure')
        real_replace(source, destination)

    mocker.patch('rdmo_gnd.index.update.os.replace', side_effect=fail_pointer)
    with pytest.raises(PermissionError):
        publish(tmp_path, staging)
    assert (tmp_path / 'current.sqlite').resolve() == target
    assert GNDIndex(target).get('4008186-2')


def test_update_lock_rejects_second_publisher(tmp_path):
    with update_lock(tmp_path), pytest.raises(IndexErrorBase, match='already running'), update_lock(tmp_path):
        pass


def test_prune_retains_current_previous_and_recent_generations(tmp_path):
    (tmp_path / 'versions').mkdir()
    generations = []
    for i in range(4):
        staging = tmp_path / 'versions/staging.sqlite'
        build_index(FIXTURE, staging)
        generations.append(publish(tmp_path, staging))
        stamp = time.time() - 48 * 60 * 60 + i
        os.utime(generations[-1], (stamp, stamp))
    prune(tmp_path)
    assert not generations[0].exists()
    assert not generations[1].exists()
    assert generations[2].exists()
    assert generations[3].exists()


def response_context(mocker, *, content=b'', status=200, headers=None):
    response = mocker.MagicMock()
    response.__enter__.return_value = response
    response.status_code = status
    response.content = content
    response.text = content.decode(errors='replace')
    response.headers = headers or {}
    response.iter_content.return_value = [content]
    return response


def test_conditional_metadata_uses_cached_text_on_304(mocker):
    session = mocker.Mock()
    session.get.return_value = response_context(mocker, status=304)
    cached = {'text': 'cached manifest', 'etag': 'example'}
    assert fetch_metadata(session, 'https://example.org', cached) == cached
    assert session.get.call_args.kwargs['headers'] == {'If-None-Match': 'example'}


def test_download_hash_verification_and_reuse(tmp_path, mocker):
    content = gzip.compress(FIXTURE.read_bytes())
    session = mocker.Mock()
    session.get.return_value = response_context(mocker, content=content)
    source = {'source_url': 'https://example.org/' + FILENAME, 'source_sha256': hashlib.sha256(content).hexdigest()}
    target = download(session, source, tmp_path)
    assert target.read_bytes() == content
    assert download(session, source, tmp_path) == target
    session.get.assert_called_once()
    target.write_bytes(b'corrupted')
    session.get.return_value = response_context(mocker, content=b'bad')
    with pytest.raises(IndexErrorBase, match='SHA-256'):
        download(session, source, tmp_path)
    assert not list(tmp_path.glob('.download*'))


@pytest.fixture
def mocked_remote(tmp_path, mocker):
    content = gzip.compress(FIXTURE.read_bytes())
    source = {
        'source_url': 'https://example.org/' + FILENAME,
        'source_sha256': hashlib.sha256(content).hexdigest(),
        'source_snapshot_date': '2026-10-13',
        'expected_records': 5,
    }
    mocker.patch('rdmo_gnd.index.update.fetch_metadata', return_value={'text': ''})
    mocker.patch('rdmo_gnd.index.update.snapshot_metadata', return_value=source)
    session = mocker.MagicMock()
    session.__enter__.return_value = session
    session.get.return_value = response_context(mocker, content=content)
    mocker.patch('rdmo_gnd.index.update.requests.Session', return_value=session)
    return source, session


def test_failed_validation_preserves_active_index(tmp_path, mocked_remote):
    (tmp_path / 'versions').mkdir()
    old = tmp_path / 'versions/staging.sqlite'
    build_index(FIXTURE, old)
    generation = publish(tmp_path, old)
    with pytest.raises(IndexErrorBase, match='Too few'):
        update_index(tmp_path)
    assert (tmp_path / 'current.sqlite').resolve() == generation
    state = json.loads((tmp_path / 'state.json').read_text())
    assert state['last_error']['type'] == 'IndexErrorBase'
    assert not list((tmp_path / 'versions').glob('.building*'))


def test_success_unchanged_and_force_validate_before_publication(tmp_path, mocked_remote, mocker):
    _source, session = mocked_remote
    # Small fixtures bypass only the production count floor, not the real build and publication.
    validation = mocker.patch('rdmo_gnd.index.update.validate_import')
    result = update_index(tmp_path)
    assert result['changed'] is True
    validation.assert_called_once()
    generation = (tmp_path / 'current.sqlite').resolve()
    assert update_index(tmp_path)['changed'] is False
    assert session.get.call_count == 1
    assert (tmp_path / 'current.sqlite').resolve() == generation
    assert update_index(tmp_path, force=True)['changed'] is True
    assert validation.call_count == 2
    assert session.get.call_count == 1  # Verified cached archive is reused on force.
    assert (tmp_path / 'current.sqlite').resolve() != generation
    assert GNDIndex(tmp_path / 'current.sqlite').metadata()['schema_version'] == SCHEMA_VERSION
    assert GNDIndex(tmp_path / 'current.sqlite').metadata()['importer_version'] == IMPORTER_VERSION


def test_metadata_state_does_not_prevent_rebuilding_missing_current(tmp_path, mocked_remote, mocker):
    mocker.patch('rdmo_gnd.index.update.validate_import')
    update_index(tmp_path)
    (tmp_path / 'current.sqlite').unlink()
    assert update_index(tmp_path)['changed'] is True


def test_atomic_state_file_is_valid_json(tmp_path):
    atomic_json(tmp_path / 'state.json', {'last_checked': 'now'})
    assert json.loads((tmp_path / 'state.json').read_text()) == {'last_checked': 'now'}


def test_corrupt_archive_and_failed_download_preserve_active(tmp_path, mocked_remote, mocker):
    import requests

    _source, session = mocked_remote
    mocker.patch('rdmo_gnd.index.update.validate_import')
    update_index(tmp_path)
    generation = (tmp_path / 'current.sqlite').resolve()
    (cached,) = (tmp_path / 'downloads').glob('*.gz')
    cached.write_bytes(b'damaged archive')
    session.get.side_effect = requests.Timeout('simulated timeout')
    with pytest.raises(requests.Timeout):
        update_index(tmp_path, force=True)
    assert (tmp_path / 'current.sqlite').resolve() == generation


@pytest.mark.parametrize('key,version', [('schema_version', 1), ('importer_version', 1), ('schema_version', 999)])
def test_incompatible_generation_rebuilds_from_cached_source(tmp_path, mocked_remote, mocker, key, version):
    mocker.patch('rdmo_gnd.index.update.validate_import')
    update_index(tmp_path)
    active = (tmp_path / 'current.sqlite').resolve()
    # Deliberately damage a test-only artifact; published deployment files stay immutable.
    with sqlite3.connect(active) as connection:
        connection.execute('UPDATE metadata SET value = ? WHERE key = ?', (str(version), key))
    assert update_index(tmp_path)['changed'] is True
    assert GNDIndex(tmp_path / 'current.sqlite').get('4008186-2')
    _source, session = mocked_remote
    assert session.get.call_count == 1


def test_disk_limit_preserves_current(tmp_path, mocked_remote, mocker):
    mocker.patch('rdmo_gnd.index.update.shutil.disk_usage', return_value=mocker.Mock(free=0))
    with pytest.raises(IndexErrorBase, match='free disk'):
        update_index(tmp_path)
    assert not (tmp_path / 'current.sqlite').exists()


def test_state_write_interruption_after_publish_leaves_usable_active(tmp_path, mocked_remote, mocker):
    mocker.patch('rdmo_gnd.index.update.validate_import')
    mocker.patch('rdmo_gnd.index.update.atomic_json', side_effect=PermissionError('simulated state write failure'))
    with pytest.raises(PermissionError):
        update_index(tmp_path)
    assert GNDIndex(tmp_path / 'current.sqlite').get('4008186-2')
