"""One ownership-preserving scope for original and composite evidence snapshots."""
import json
from contextlib import nullcontext
from . import db
from .evidence_contracts import digest


def predicate(*, collection_id=None, run_id=None, alias='e'):
    if alias != 'e':
        raise ValueError('Unsupported evidence alias')
    if collection_id:
        return ('(e.collection_id=%s OR EXISTS (SELECT 1 FROM collection_members cm '
                'WHERE cm.collection_id=%s AND cm.evidence_id=e.id AND cm.user_id=e.user_id))',
                [collection_id, collection_id])
    return 'e.run_id=%s', [run_id]


def rows(owner, *, collection_id=None, run_id=None, ids=None):
    clause, args = predicate(collection_id=collection_id, run_id=run_id)
    if ids is not None:
        clause += ' AND e.id=ANY(%s)'
        args.append(list(ids))
    return db.all_rows('SELECT e.* FROM evidence e JOIN evidence_index i ON i.evidence_id=e.id '
        'WHERE e.user_id=%s AND ' + clause + ' ORDER BY i.observed_at NULLS LAST,i.source_line,e.id', [owner, *args])


def validate_mappings(mappings):
    if mappings is None:
        return {}
    if not isinstance(mappings, dict) or set(mappings) - {'services', 'environments'}:
        raise ValueError('Unsupported correlation mapping.')
    for field, aliases in mappings.items():
        if not isinstance(aliases, dict) or len(aliases) > 32 or any(not isinstance(k, str) or not isinstance(v, str)
                or not 1 <= len(k) <= 160 or not 1 <= len(v) <= 80 or ':' not in k
                or k.split(':', 1)[0] not in {'sentry', 'datadog', 'manual'} or not k.split(':', 1)[1].strip() or not v.strip() for k,v in aliases.items()):
            raise ValueError('Use at most 32 explicit provider:name aliases per mapping.')
    return mappings


def create_composite(owner, collection_ids, title, symptom='', *, mappings=None, transaction=None):
    if (not isinstance(collection_ids, list) or not 2 <= len(collection_ids) <= 5
            or any(not isinstance(i, str) for i in collection_ids) or len(set(collection_ids)) != len(collection_ids)):
        raise ValueError('Select two to five distinct completed snapshots.')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or not isinstance(symptom, str) or len(symptom) > 500:
        raise ValueError('Use a title of 1-120 characters and a symptom of at most 500 characters.')
    mappings = validate_mappings(mappings)
    with (nullcontext(transaction) if transaction is not None else db.connection()) as conn:
        db.lock(conn)
        sources = conn.execute('SELECT * FROM collections WHERE user_id=%s AND id=ANY(%s)', (owner, collection_ids)).fetchall()
        if len(sources) != len(collection_ids):
            raise ValueError('A selected snapshot is unavailable.')
        if conn.execute("SELECT 1 FROM sentry_imports WHERE user_id=%s AND collection_id=ANY(%s) AND state IN ('queued','running','rate_limited')", (owner, collection_ids)).fetchone():
            raise ValueError('Wait for all imports to finish.')
        if conn.execute("SELECT 1 FROM runs WHERE user_id=%s AND collection_id=ANY(%s) AND state IN ('starting','running','resetting')", (owner, collection_ids)).fetchone():
            raise ValueError('Wait for evidence collection to finish.')
        members = conn.execute('SELECT DISTINCT e.* FROM evidence e WHERE e.user_id=%s AND '
            '(e.collection_id=ANY(%s) OR EXISTS (SELECT 1 FROM collection_members cm WHERE cm.collection_id=ANY(%s) '
            'AND cm.evidence_id=e.id AND cm.user_id=e.user_id)) ORDER BY e.id LIMIT 5001', (owner, collection_ids, collection_ids)).fetchall()
        if not members or len(members) > 5000 or sum(len(r['payload'].encode()) for r in members) > 10 * 1024 * 1024:
            raise ValueError('Combined evidence must contain 1-5000 observations within 10 MiB.')
        services = sorted({r['service'] for r in members})
        if len(services) > 32:
            raise ValueError('Combined evidence exceeds 32 services.')
        fingerprint = 'composite:' + digest({'sources': sorted(collection_ids), 'ids': [r['id'] for r in members], 'symptom': symptom, 'mappings': mappings})
        previous = conn.execute('SELECT id FROM collections WHERE user_id=%s AND fingerprint=%s', (owner, fingerprint)).fetchone()
        if previous:
            return previous['id']
        identifier = db.uid()
        summary = {'observations': len(members), 'services': services, 'kinds': sorted({r['kind'] for r in members}),
                   'source_collections': sorted(collection_ids), 'frozen': False, 'mapping_version': 'explicit-aliases-v1', 'mappings': mappings,
                   'coverage': 'Frozen combined snapshots. Shared timestamps or raw IDs do not establish cross-provider causality.',
                   'datasets': {r['id'] + ':' + key: value for r in sources for key, value in json.loads(r['summary']).get('datasets', {}).items()}}
        conn.execute('INSERT INTO collections(id,user_id,title,symptom,source_type,created_at,fingerprint,summary) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                     (identifier, owner, title.strip(), symptom, 'composite', db.now(), fingerprint, json.dumps(summary)))
        for row in members:
            conn.execute('INSERT INTO collection_members VALUES(%s,%s,%s,%s,%s::jsonb)',
                         (identifier, row['id'], owner, 'context', json.dumps({'source_collection': row['collection_id'], 'reason': 'owner_selected_snapshot'})))
        summary['frozen'] = True
        conn.execute('UPDATE collections SET summary=%s WHERE id=%s', (json.dumps(summary), identifier))
        return identifier
