"""Install and exercise built distributions without importing the source checkout."""

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path


def smoke_test(distributions):
    (wheel,) = distributions.glob('*.whl')
    (source,) = distributions.glob('*.tar.gz')
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert 'rdmo_gnd/index/cli.py' in names
        assert 'rdmo_gnd/_version.py' in names
        for legal in ('LICENSE',):
            assert any(name.endswith('/licenses/' + legal) for name in names), legal
    with tarfile.open(source) as archive:
        names = archive.getnames()
        for legal in ('LICENSE',):
            assert any(name.endswith('/' + legal) for name in names), legal

    with tempfile.TemporaryDirectory(prefix='gnd-wheel-') as temporary:
        directory = Path(temporary)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in ('PYTHONPATH', 'PYTHONHOME', 'DJANGO_SETTINGS_MODULE')
        }
        venv.create(directory / 'venv', with_pip=True)
        python = directory / 'venv/bin/python'
        command = directory / 'venv/bin/rdmo-gnd'

        def run(*args):
            result = subprocess.run(args, cwd=directory, env=environment, check=True, capture_output=True, text=True)
            return result.stdout

        run(str(python), '-m', 'pip', 'install', '--disable-pip-version-check', str(wheel.resolve()) + '[local]')
        run(
            str(python),
            '-c',
            """
import importlib.metadata
import importlib.util
import pathlib
import sys
import rdmo_gnd
assert pathlib.Path(rdmo_gnd.__file__).is_relative_to(sys.prefix)
assert rdmo_gnd.VERSION == rdmo_gnd.__version__ == importlib.metadata.version('rdmo-plugins-gnd')
assert importlib.util.find_spec('django') is None
assert importlib.util.find_spec('rdmo') is None
""",
        )
        run(str(command), '--help')
        fixture = Path(__file__).parent / 'fixtures/marc/subjects.xml'
        source_copy = directory / 'subjects.xml'
        source_copy.write_bytes(fixture.read_bytes())
        metadata = json.loads(
            run(str(command), 'build', '--source', str(source_copy), '--output', str(directory / 'current.sqlite'))
        )
        assert metadata['concepts_imported'] == 5
        assert metadata['schema_version'] == metadata['importer_version'] == 2
        results = json.loads(run(str(command), 'search', 'fuel ce', '--directory', str(directory)))
        assert results[0]['gndIdentifier'] == '4008195-3'
        status = json.loads(run(str(command), 'status', '--directory', str(directory), '--json'))
        assert status['active']['concepts_imported'] == 5
        print('Installed-wheel imports, versions, legal files, and standalone build/search/status passed.')


if __name__ == '__main__':
    smoke_test(Path(sys.argv[1]).resolve())
