"""Observed quality attributes, not a fabricated reliability percentage."""
from collections import defaultdict
from .evidence_contracts import digest, timestamp

VERSION = 'evidence-quality-v1'


def assess(rows, *, start=None, end=None):
    quality, identities = {}, defaultdict(list)
    start, end = timestamp(start), timestamp(end)
    for row in rows:
        at = timestamp(row.get('timestamp'))
        content = {k: v for k, v in row.items() if k not in {'id', 'collection_id', 'run_id', 'provenance', 'ingested_at'}}
        key = (row.get('source_connection_id'), row.get('source_dataset'), row.get('source_id'))
        item = {'version': VERSION, 'timestamp': 'known' if at else 'unknown',
                'stale': bool(at and ((start and at < start) or (end and at > end))),
                'truncated': bool(row.get('source_truncated') or row.get('_excerpt')),
                'content_digest': digest(content), 'duplicate_ids': [], 'conflicting_ids': [],
                'provenance': 'retained' if row.get('source_id') or row.get('original') else 'unspecified'}
        quality[row['id']] = item
        if key[2]:
            identities[key].append(row['id'])
    for members in identities.values():
        for identifier in members:
            own = quality[identifier]
            for other in members:
                if other != identifier:
                    field = 'duplicate_ids' if own['content_digest'] == quality[other]['content_digest'] else 'conflicting_ids'
                    own[field].append(other)
    return quality
