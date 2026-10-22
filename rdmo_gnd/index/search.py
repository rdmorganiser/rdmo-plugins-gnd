"""Read-only local search and exact GND identifier lookup."""

import re

from sqlite_utils.db import NotFoundError

from ..utils import get_identifier
from .database import open_index, read_metadata
from .marc import normalize

SEARCH_SQL = """
WITH hits AS MATERIALIZED (
    SELECT l.gnd_id, l.id, c.preferred_name, c.normalized AS preferred_normalized,
        CASE
            WHEN l.normalized = :query AND l.kind = 'preferred' THEN 0
            WHEN l.normalized = :query THEN 1
            WHEN substr(l.normalized, 1, length(:query)) = :query AND l.kind = 'preferred' THEN 2
            WHEN substr(l.normalized, 1, length(:query)) = :query THEN 3
            ELSE 4
        END AS tier,
        bm25(label_fts) AS score
    FROM label_fts
    JOIN label l ON l.id = label_fts.rowid
    JOIN concept c ON c.gnd_id = l.gnd_id
    WHERE label_fts MATCH :fts
), ranked AS (
    SELECT *, row_number() OVER (PARTITION BY gnd_id ORDER BY tier, score, id) AS position
    FROM hits
)
SELECT gnd_id, preferred_name FROM ranked WHERE position = 1
ORDER BY tier, score, preferred_normalized, gnd_id LIMIT :limit
"""


def fts_query(query):
    if len(query) > 200:
        return None
    # Tokenize before quoting so that typed FTS operators are ordinary search terms.
    query = normalize(query)
    tokens = re.findall(r'[^\W_]+', query, re.UNICODE)
    if not tokens or len(tokens) > 12:
        return None
    return ' AND '.join('"' + token + '"' + ('*' if i == len(tokens) - 1 else '') for i, token in enumerate(tokens))


def as_record(concept):
    return {
        'id': f'https://d-nb.info/gnd/{concept["gnd_id"]}',
        'gndIdentifier': concept['gnd_id'],
        'preferredName': concept['preferred_name'],
    }


class GNDIndex:
    def __init__(self, path):
        self.path = path

    def search(self, query, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Search limit must be between 1 and 100.')
        identifier = get_identifier(query)
        if identifier:
            record = self.get(identifier)
            return [record] if record else []
        expression = fts_query(query)
        if expression is None:
            return []
        with open_index(self.path) as db:
            return [
                as_record(row)
                for row in db.query(
                    SEARCH_SQL,
                    {
                        'query': normalize(query),
                        'fts': expression,
                        'limit': limit,
                    },
                )
            ]

    def get(self, identifier):
        identifier = get_identifier(identifier)
        if identifier is None:
            return None
        with open_index(self.path) as db:
            try:
                concept = db['concept'].get(identifier)
            except NotFoundError:
                return None
            record = as_record(concept)
            variants = [
                row['value']
                for row in db.query(
                    "SELECT DISTINCT value FROM label WHERE gnd_id = :id AND kind = 'variant' ORDER BY value",
                    {'id': identifier},
                )
            ]
            if variants:
                record['variantName'] = variants
            return record

    def metadata(self):
        with open_index(self.path) as db:
            return read_metadata(db)
