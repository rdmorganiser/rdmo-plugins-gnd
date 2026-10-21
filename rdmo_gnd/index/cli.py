"""Standalone administration without importing Django or RDMO."""

import argparse
import json
import sys
from pathlib import Path


def parser():
    result = argparse.ArgumentParser(description='Build, update, and search a local GND subject-heading index.')
    commands = result.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build', help='Build a new index from a local XML or XML.gz file.')
    build.add_argument('--source', required=True, type=Path)
    build.add_argument('--output', required=True, type=Path)
    update = commands.add_parser('update', help='Check DNB and publish a validated snapshot if needed.')
    update.add_argument('--force', action='store_true', help='Rebuild unchanged data; all validation still applies.')
    status = commands.add_parser('status', help='Show active database metadata and update state.')
    status.add_argument('--json', action='store_true')
    search = commands.add_parser('search', help='Search the active index.')
    search.add_argument('query')
    search.add_argument('--limit', type=int, default=20)
    for command in (update, status, search):
        command.add_argument('--directory', type=Path, default=Path('/var/lib/rdmo-gnd'))
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        from .database import build_index
        from .search import GNDIndex
        if args.command == 'build':
            data = build_index(args.source, args.output)
        elif args.command == 'update':
            from .update import update_index
            data = update_index(args.directory, force=args.force)
        elif args.command == 'search':
            data = GNDIndex(args.directory / 'current.sqlite').search(args.query, limit=args.limit)
        else:
            from .update import load_state
            data = {'active': GNDIndex(args.directory / 'current.sqlite').metadata(),
                    'state': load_state(args.directory)}
        if args.command == 'status' and not args.json:
            print('Active GND index:')
            for name, value in data['active'].items():
                print(f'  {name}: {value}')
            print('Updater:', json.dumps(data['state'], ensure_ascii=False))
        else:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    except Exception as error:
        print(f'rdmo-gnd: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
