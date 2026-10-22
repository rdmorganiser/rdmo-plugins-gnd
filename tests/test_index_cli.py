import json
import os
import subprocess
import sys
from pathlib import Path

from rdmo_gnd.index.cli import main

FIXTURE = Path(__file__).parent / 'fixtures/marc/subjects.xml'


def test_build_search_and_status_commands(tmp_path, capsys):
    output = tmp_path / 'current.sqlite'
    assert main(['build', '--source', str(FIXTURE), '--output', str(output)]) == 0
    assert json.loads(capsys.readouterr().out)['concepts_imported'] == 5
    assert main(['search', 'fuel ce', '--directory', str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)[0]['gndIdentifier'] == '4008195-3'
    assert main(['status', '--directory', str(tmp_path), '--json']) == 0
    assert json.loads(capsys.readouterr().out)['active']['concepts_imported'] == 5
    assert main(['build', '--source', str(FIXTURE), '--output', str(output)]) == 1
    assert 'rdmo-gnd:' in capsys.readouterr().err


def test_cli_does_not_import_django_or_rdmo(tmp_path):
    script = """
import sys
from rdmo_gnd.index.cli import main
assert main(['build', '--source', sys.argv[1], '--output', sys.argv[2]]) == 0
assert not any(name == 'django' or name.startswith('django.') or name == 'rdmo' or name.startswith('rdmo.')
               for name in sys.modules)
"""
    env = {key: value for key, value in os.environ.items() if key != 'DJANGO_SETTINGS_MODULE'}
    result = subprocess.run(
        [sys.executable, '-c', script, str(FIXTURE), str(tmp_path / 'gnd.sqlite')],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
