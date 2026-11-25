"""Deterministic inspection of retained inputs and events; never executes a model."""
import json
from . import db
from .evidence_contracts import digest
from .evidence_scope import rows


def inspect(investigation, *, include_sources=False):
    manifest = investigation.get('execution_manifest')
    if not manifest:
        return {'recorded': False, 'integrity': 'not_recorded', 'note': 'This historical investigation has no execution journal.'}
    saved = db.all_rows('SELECT sequence,kind,created_at::text,payload,digest FROM investigation_events '
        'WHERE investigation_id=%s AND user_id=%s ORDER BY sequence LIMIT 1001', (investigation['id'], investigation['user_id']))
    failures, pending = [], set()
    if len(saved) > 1000:
        failures.append('Recording exceeds the supported event bound.')
    for number, event in enumerate(saved, 1):
        if event['sequence'] != number or digest(event['payload']) != event['digest']:
            failures.append('Event sequence or content digest mismatch.')
        attempt = event['payload'].get('attempt')
        if event['kind'] == 'request_started':
            pending.add(attempt)
        elif event['kind'] in {'request_completed', 'request_failed', 'request_invalid'}:
            pending.discard(attempt)
    anchors = [e['payload'].get('digest') for e in saved if e['kind'] == 'manifest_created']
    if anchors != [digest(manifest)]:
        failures.append('Manifest digest is missing or does not match.')
    original = [db.public_evidence(r) for r in rows(investigation['user_id'],
        collection_id=investigation.get('collection_id'), run_id=investigation.get('run_id'), ids=manifest.get('source_hashes', {}))]
    actual = {r['id']: digest(r) for r in original}
    if actual != manifest.get('source_hashes'):
        failures.append('An original evidence record is missing or differs from the recorded digest.')
    validations = [e['payload'] for e in saved if e['kind'] == 'report_validated']
    if investigation['state'] == 'completed':
        report = json.loads(investigation['report'])
        if not validations or validations[-1].get('report') != report:
            failures.append('Saved report does not match the final validated report.')
    result = {'recorded': True, 'integrity': 'verified' if not failures else 'failed', 'failures': failures,
        'manifest': manifest, 'events': saved, 'uncertain_requests': sorted(pending),
        'state': investigation['state'], 'checkpoint_stage': (investigation.get('execution_state') or {}).get('stage'),
        'note': 'Recorded playback. Structural integrity is not semantic accuracy. Interrupted requests are never resent; a new investigation requires an explicit action.'}
    if include_sources:
        result['evidence'] = original
    return result
