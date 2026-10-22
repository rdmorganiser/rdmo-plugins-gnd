# Local subject-heading search

`GNDLocalKeywordsProvider` (`gnd_local_keywords`) searches an immutable SQLite FTS5
index alongside the three online providers. It reuses the existing provider's
identifier handling, saved-label normalization, escaped HTML, and link target.
Autocomplete and mappings configured with `lookup='local'` make no HTTP requests.
There is no automatic online fallback from local autocomplete.

The optional component uses Python 3.12+, `sqlite-utils>=4.2.1,<5`, and
`defusedxml>=0.7.1,<1`. SQLite must provide FTS5 and be at least 3.35. Ubuntu 24.04
with Python 3.12 meets these requirements. No Django models, migrations, database
routers, search server, or frontend changes are required. The command-line tools
do not initialize Django or RDMO.

## Install and bootstrap on Ubuntu

Build the index once per VM. Each RDMO environment also needs the `local` extra.
The updater owns the directory; the web processes only need group read access.
Use an administrator shell for the system installation commands below and replace
`www-data` with the account running your RDMO workers if necessary.

```bash
sudo apt install python3.12-venv
sudo useradd --system --home-dir /var/lib/rdmo-gnd --shell /usr/sbin/nologin rdmo-gnd
sudo install -d -o root -g root -m 0755 /opt/rdmo-gnd
sudo python3.12 -m venv /opt/rdmo-gnd/venv
sudo /opt/rdmo-gnd/venv/bin/pip install 'rdmo-plugins-gnd[local]'
sudo install -d -o rdmo-gnd -g rdmo-gnd -m 0750 /var/lib/rdmo-gnd
sudo usermod -aG rdmo-gnd www-data
sudo -u rdmo-gnd sh -c 'umask 027; /opt/rdmo-gnd/venv/bin/rdmo-gnd update'
/opt/rdmo-gnd/venv/bin/rdmo-gnd status --json
```

Run `pip install 'rdmo-plugins-gnd[local]'` in **each RDMO virtual environment**.
Register the provider in `config/settings/local.py`:

```python
GND_INDEX_PATH = '/var/lib/rdmo-gnd/current.sqlite'
GND_INDEX_MAX_AGE_DAYS = 30  # warning only, based on the source snapshot date

OPTIONSET_PROVIDERS += [
    ('gnd_local_keywords', _('GND Local Keywords Provider'),
     'rdmo_gnd.providers.GNDLocalKeywordsProvider'),
]
```

Keep `rdmo_gnd` in `INSTALLED_APPS`. Run `python manage.py check`, then restart
RDMO workers to load the new registration and supplementary group membership.
Select **GND Local Keywords Provider** in the OptionSet management interface.
[The example catalog](../xml/questions-gnd.xml) includes a separate question,
attribute, and OptionSet for it. Test `fuel`, `Brennstoff`, and `fuel ce`, save a
selection, and reload it. Later index publications do not require worker restarts.

Missing/unreadable/incompatible indexes produce an empty option list and a logged
warning. System checks report missing dependencies/settings as `rdmo_gnd.E002`,
index availability as `rdmo_gnd.W001`, and stale/unknown source dates as
`rdmo_gnd.W002`. Staleness does not disable search. The checks read the auxiliary
index only; they do not use the RDMO database, download, or build anything.

## Attribute synchronization

Existing `GND_PROVIDER_MAP` entries continue to use the existing lobid client and
HTTP cache. Explicitly opt in for keyword attributes:

```python
GND_PROVIDER_MAP += [{
    'gndIdentifier': 'https://example.org/domain/keywords/gnd',
    'preferredName': 'https://example.org/domain/keywords/name',
    'lookup': 'local',
}]
```

| `lookup` | Resolution |
| --- | --- |
| `lobid` (default) | Always use the existing lobid client, including its optional cache. |
| `local` | Exact local identifier lookup only; never HTTP, even on failure. |
| `auto` | Use local data if the identifier exists and all mapped paths are supported; otherwise lobid. |

The local representation supports `id`, `gndIdentifier`, `preferredName`,
`variantName`, and numeric paths such as `variantName/0`. English cross-vocabulary
labels are searchable but are not exposed as GND variants. Local data does not
claim complete lobid metadata: `definition`, `type`, and other unsupported paths
require lobid under `auto`. A supported field or array index with no value does
**not** trigger fallback. Missing paths and failed lookups preserve target values.
The `lookup` entry is configuration and is never mapped to an attribute.

## Updating and rollback

Install [the service](deployment/rdmo-gnd-update.service) and
[the timer](deployment/rdmo-gnd-update.timer):

```bash
sudo install -m 0644 docs/deployment/rdmo-gnd-update.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rdmo-gnd-update.timer
systemctl list-timers rdmo-gnd-update.timer
journalctl -u rdmo-gnd-update.service
```

The timer checks at 00:15 and 12:15 with a randomized delay up to ten minutes.
`Persistent=true` catches missed runs. It checks for new snapshots rather than
repeatedly downloading the same file. Run an immediate check with
`sudo systemctl start rdmo-gnd-update.service`.

```text
/var/lib/rdmo-gnd/
├── current.sqlite -> versions/gnd-<generation>.sqlite
├── versions/
├── downloads/
├── .update.lock
└── state.json
```

The updater matches the dated subject-heading MARC21-XML archive in the
[DNB directory](https://data.dnb.de/GND/) to its
[SHA-256 manifest](https://data.dnb.de/GND/001_Pruefsumme_Checksum.txt). It uses
conditional requests for metadata, downloads only changed data, and rechecks the
hash of cached archives. Missing expected record counts fail the update.
`--force` rebuilds unchanged data but **never bypasses validation**.

Before publication it checks the checksum, complete gzip/XML parsing, exact
published record count, import outcomes, at least 100,000 concepts, at least 99%
retention, no count decrease over 10% from the previous version, unique GND IDs,
foreign keys, SQLite and FTS5 integrity, and German/English smoke searches.
Unexpected changes require investigation; the previous generation stays active.
The production download limit is 256 MiB, with a minimum of 2 GiB free disk before
building. A nonblocking file lock prevents concurrent publishers.

A fully closed and validated database is moved into `versions/` with mode 0640.
An atomic symlink switch publishes it. Readers resolve that link per operation
and close their read-only `mode=ro&immutable=1` connection afterward. Never edit a
published database in place. The current and previous generations are retained;
other generations are removed only after 24 hours. Cached archives can be removed
when no updater is running if disk reclamation is needed.

`status` distinguishes `last_checked`, `source_snapshot_date`, and `built_at`.
`state.json` records updater outcomes; the active symlink and database metadata
are authoritative if a process exits after publication but before updating state.
Failures return a nonzero status and appear in journald. Snapshot age is not the
same as the time of the last check.

To roll back, stop the updater, select a retained generation, and atomically
replace the link. As `rdmo-gnd` in `/var/lib/rdmo-gnd`:

```bash
ln -s versions/<previous-generation>.sqlite rollback.sqlite
mv -Tf rollback.sqlite current.sqlite
```

No RDMO restart is needed. Pause the timer while investigating; a subsequent
update may publish the newer snapshot again.

## Data, search, and administration

The importer streams plain XML or gzip archives with `defusedxml`, prohibits
DTDs/entities/external references, and discards processed records. It accepts
MARC21 collections and SRU wrappers, and restricts records to GND `gndgen=s`.
It uses `024 $a` with `$2=gnd`, never the internal `001` identifier.

Preferred `150`, variant `450`, and cross-vocabulary `750` labels retain meaningful
qualifications and subdivisions. `750` vocabulary, language, and relationship
(including `EQ` and `=EQ`) are preserved. Related terms in `550` are not indexed.
Original display labels are normalized to NFC. FTS5 indexes whitespace-normalized,
casefolded labels, and queries use the same normalization before tokenization and
ranking. For example, `Straße`, `STRASSE`, and `strasse` retrieve the same concepts
while display labels retain their original spelling.
English coverage depends on curated mappings; there is no machine translation.
Consult [DNB's data terms](https://data.dnb.de/opendata/) when using or
redistributing data, especially independently distributed vocabulary mappings.

`sqlite-utils` creates ordinary `concept`, `label`, and `metadata` tables and an
external-content `label_fts` table, populated once after bulk insertion. There are
no update triggers. The schema/importer versions are stored in metadata; changes
require rebuilding. No prefix indexes are configured initially; the package's FTS
API supports the required token-prefix searches without extra indexes.

Exact preferred labels rank first, then exact variants/equivalents, preferred
prefixes, other label prefixes, and other token matches. BM25 and stable name/ID
ordering break ties. A materialized SQL query selects the best label per concept
before the result limit. Defaults are 20 unique results, at most 100. Input is
limited to 200 characters and 12 tokens; punctuation-only input returns nothing.
Quoted letter/digit tokens are ANDed, with a prefix on the last token, so `fuel ce`
finds `Fuel cells`. Raw FTS syntax cannot be injected. There is no stemming, fuzzy
matching, application response cache, or HTTP fallback from this provider.

```bash
rdmo-gnd build --source subjects.xml.gz --output /tmp/gnd.sqlite
rdmo-gnd update --directory /var/lib/rdmo-gnd
rdmo-gnd update --directory /var/lib/rdmo-gnd --force
rdmo-gnd status --directory /var/lib/rdmo-gnd --json
rdmo-gnd search 'fuel ce' --directory /var/lib/rdmo-gnd --limit 20
```

`build` refuses existing output files and accepts small fixtures; production size
checks are applied by `update`. The CLI can also be run with
`python -m rdmo_gnd.index.cli`. Schema and source metadata are visible through
`GNDIndex.metadata()`. `GNDIndex.get()` returns a compatible record or `None`;
`GNDIndex.search()` returns compatible records rather than formatted options.

The test suite covers real sqlite-utils/FTS5 operations, XML safety, multilingual
search, ranking/deduplication, mappings, update failures, and readers spanning
publication. Full-snapshot benchmarks are manual and need no Django instance.
Warm searches of three or more characters target p95 below 50 ms; measure on the
deployment VM, including concurrent readers and disk/memory use during import.
Full-GND import, incremental/OAI-PMH updates, fuzzy matching, and translation are
outside this implementation.


### Initial full-snapshot verification

A development run on 21 October 2026 imported all 208,435 records from the
13 October snapshot, with zero filtered or skipped records. Download and build
took 74 seconds, peak resident memory was 46 MiB, and the index was 102 MiB.
On that host (Python 3.14, SQLite 3.53), a mixed set of 100 warm queries had a
13 ms p95; 100 searches with eight concurrent readers completed without errors.
These measurements describe that development host, rather than a deployment
capacity guarantee. The same index was also read successfully using Ubuntu's
Python 3.12/SQLite build. A second update detected unchanged data and did not
fetch the archive or rebuild.


### Version-2 indexes

The normalized full-text index uses schema and importer version **2**. Version-1
artifacts must be rebuilt; they are never migrated or modified in place. Before
first deploying this provider, run `rdmo-gnd update` using the updated package and
verify `schema_version` and `importer_version` in `rdmo-gnd status --json`. An
unchanged archive is reused after checksum verification, but it is rebuilt for the
new version. The provider reports older schemas as unavailable until rebuilt.
