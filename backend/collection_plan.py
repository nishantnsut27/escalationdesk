"""Sequential enrichment within one owner's shared acquisition allowance."""
import json
import time
from starlette.exceptions import HTTPException
from . import db
from .evidence_scope import create_composite
from .sentry_imports import create_import, parameters


def advance(conn, attempt, episode, primary):
    if attempt.get('scope_collection_id'):
        return attempt['scope_collection_id']
    plan = (episode.get('collection_plan') or {}).get('enrichment')
    if not plan:
        return primary['collection_id']
    def primary_only(reason):
        conn.execute('UPDATE automatic_attempts SET error=%s,scope_collection_id=%s WHERE id=%s', (reason, primary['collection_id'], attempt['id']))
        return primary['collection_id']
    source = conn.execute("SELECT * FROM sentry_connections WHERE id=%s AND user_id=%s AND provider='sentry' AND state='connected'", (plan['connection_id'], episode['user_id'])).fetchone()
    if not source:
        # Do not invent an empty successful import or rewrite the primary snapshot.
        return primary_only('Selected Sentry enrichment is unavailable; primary evidence retained.')
    linked = conn.execute('SELECT s.* FROM automatic_attempt_imports h JOIN sentry_imports s ON s.id=h.import_id '
                          'WHERE h.attempt_id=%s AND s.connection_id=%s ORDER BY s.created_at DESC,s.id DESC LIMIT 1', (attempt['id'], source['id'])).fetchone()
    if linked and linked['state'] in {'queued', 'running', 'rate_limited'}:
        return None
    summary = json.loads(conn.execute('SELECT summary FROM collections WHERE id=%s', (primary['collection_id'],)).fetchone()['summary'])
    deadline = summary.get('acquisition_started_at', time.time()) + 300
    if not linked:
        if summary['observations'] >= 5000 or summary['input_bytes'] >= 10*1024*1024 or time.time() >= deadline:
            return primary_only('Shared acquisition budget exhausted before enrichment.')
        primary_options = json.loads(primary['parameters'])
        options = parameters({'projects': plan['project_ids'], 'datasets': ['errors','logs','spans'],
            'start': primary_options['start'], 'end': primary_options['end'], 'title': episode['title']+' context',
            'symptom': 'Additional provider context; relationships require recorded identity.'}, dict(source))
        options.update(_max_records=5000-summary['observations'], _max_bytes=10*1024*1024-summary['input_bytes'],
                       _deadline=deadline, _max_pages=max(0,150-sum(v.get('pages',0) for v in summary['datasets'].values())),
                       _existing_services=summary['services'], _source_service=primary_options.get('_source_service'),
                       _source_environment=primary_options.get('_source_environment'))
        # A monitor without an explicit service scope cannot justify project-wide collection.
        if not options['_source_service']:
            return primary_only('Service identity is missing; Sentry enrichment was not collected.')
        mappings = (episode.get('collection_plan') or {}).get('mappings', {})
        for field, option in (('services', '_source_service'), ('environments', '_source_environment')):
            value = options[option]
            aliases = mappings.get(field, {})
            canonical = aliases.get('datadog:'+str(value), value)
            matches = [k.split(':', 1)[1] for k, v in aliases.items() if k.startswith('sentry:') and v == canonical]
            if len(matches) > 1:
                return primary_only('Enrichment identity mapping is ambiguous; primary evidence retained.')
            if matches:
                options[option] = matches[0]
        try:
            job, _ = create_import(episode['user_id'], dict(source), options, transaction=conn, scope_key=attempt['id']+':enrichment')
        except HTTPException as exc:
            if exc.status_code in {409,429}:
                return None
            raise
        conn.execute('INSERT INTO automatic_attempt_imports(attempt_id,import_id) VALUES(%s,%s) ON CONFLICT DO NOTHING', (attempt['id'], job['id']))
        return None
    if linked['state'] != 'completed':
        conn.execute('UPDATE automatic_attempts SET error=%s WHERE id=%s', ('Sentry enrichment '+linked['state']+'; retained context may be incomplete.', attempt['id']))
    identifier = create_composite(episode['user_id'], [primary['collection_id'], linked['collection_id']], episode['title'],
                                  'Combined provider evidence for the observed monitor episode.',
                                  mappings=(episode.get('collection_plan') or {}).get('mappings'), transaction=conn)
    conn.execute('UPDATE automatic_attempts SET scope_collection_id=%s WHERE id=%s', (identifier, attempt['id']))
    return identifier
