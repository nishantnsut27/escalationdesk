"""Task-owned loopback preview: disposable test schema, recorded data, mocked AI."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
for key in ('DESK_PUBLIC_URL', 'SENTRY_CLIENT_ID', 'SENTRY_CLIENT_SECRET', 'SENTRY_APP_SLUG',
            'OPENROUTER_API_KEY', 'GROQ_PROXY_URL', 'JEV_PROXY_URL', 'RESEND_API_KEY', 'SENTRY_DSN'):
    os.environ[key] = ''
FIXTURE_PORT = int(os.environ.get('DESK_FIXTURE_PORT', '8031'))
os.environ.update(DESK_PORT=str(FIXTURE_PORT), DESK_ENABLE_LAB='0', GROQ_API_KEY='mock-preview-key',
                  DESK_DATA_DIR=str(ROOT / '.cache/redesign/preview-data'))

import asyncio
from contextlib import asynccontextmanager
import json
import re
import tempfile
import httpx
import uvicorn
from unittest.mock import patch
from cryptography.fernet import Fernet
from database import IsolatedDatabase
from backend import auth, config, db, demo, email_service
from backend.api import create_app
from backend.investigator import Investigator
from backend.evidence import model_observation
from backend.selection import excerpt, provenance, select_blocks
from backend.sentry_client import SentryClient
from starlette.responses import JSONResponse
from starlette.routing import Route

MODEL_CALLS = []

FIXTURE = json.loads((ROOT / 'tests/fixtures/workspace.json').read_text(encoding='utf-8'))

def seed():
    owner = 'preview-owner'
    rows = FIXTURE['observations'] + [{**FIXTURE['observations'][5], 'id': 'repeat-' + str(i)} for i in range(120)]
    rows = [{**row, 'original': json.dumps(row), 'source_line': index + 1, 'source_end_line': index + 1} for index, row in enumerate(rows)]
    rows.extend([{'id': 'cycle-a', 'kind': 'span', 'service': 'api', 'trace_id': 'trace-cycle', 'span_id': 'cycle-a', 'parent_id': 'cycle-b', 'name': 'recorded cyclic parent', 'status': 'error', 'timestamp': None}, {'id': 'cycle-b', 'kind': 'span', 'service': 'worker', 'trace_id': 'trace-cycle', 'span_id': 'cycle-b', 'parent_id': 'cycle-a', 'name': 'recorded cyclic child', 'status': 'ok', 'timestamp': None}, {'id': 'long-message', 'kind': 'log', 'service': 'worker', 'level': 'warning', 'timestamp': None, 'message': 'Synthetic operational example: retained event detail. ' * 20}, {'id': 'metric-sample', 'kind': 'metric', 'service': 'worker', 'timestamp': None, 'name': 'memory sample', 'value': 128}])
    rows.append({'id': 'oversized', 'kind': 'log', 'service': 'billing', 'level': 'error', 'timestamp': None,
                 'message': 'NullPointerException: invoice is null', 'stack_trace': 'at Billing.authorize(Billing.java:42)\n' * 700,
                 'original': 'token=[REDACTED]\n' + 'at Billing.authorize(Billing.java:42)\n' * 700})
    summary = {'observations': len(rows), 'services': sorted({r['service'] for r in rows}), 'kinds': ['log', 'span'],
               'input_lines': len(rows), 'input_bytes': 35000, 'duplicates': 0, 'conflicting_ids': 0,
               'malformed_records': 0, 'redactions': 1, 'missing_timestamps': 3, 'missing_services': 0,
               'invalid_fields': 0, 'coverage': 'Recorded acceptance fixtures; no production telemetry.',
               'redaction_note': 'The example token is redacted. This preview uses test data only.'}
    report = {'assessment': 'supported', 'origin': 'billing', 'affected_services': ['billing', 'gateway'],
              'summary': 'Billing authorization raised a null-reference exception. The recorded checkout span ended in an error.',
              'evidence_ids': ['exception', 'root'], 'hypotheses': [],
              'supporting_evidence': [{'finding': 'Authorization encountered an absent invoice at Billing.java:42.', 'evidence_ids': ['exception']},
                                      {'finding': 'The checkout span for the same trace recorded an error over 104 ms.', 'evidence_ids': ['root']}],
              'recommendations': ['Inspect invoice loading for this trace.', 'Compare the successful billing request with the failing authorization.'],
              'limitations': ['The evidence does not establish why the invoice was absent.', 'This is a recorded acceptance fixture, not a live investigation.']}
    originals = {row['id']: row for row in rows}
    _, selection = select_blocks(rows)
    supplied = [{'provider': 'recorded-fixture', 'attempt': 1, 'phase': 'report',
                 'excerpts': provenance([excerpt(model_observation(originals[i])) for i in ['exception', 'root']], originals, selection)}]
    with db.connection() as conn, conn.pipeline():
        for user_id, email, verified in [(owner, 'preview@tracedesk.test', True), ('preview-unverified', 'verify@tracedesk.test', False)]:
            conn.execute('INSERT INTO users VALUES(%s,%s,%s,%s,%s)', (user_id, 'Preview fixtures', email, auth.hasher.hash('TraceDeskPreview!26'), db.now()))
            if verified:
                conn.execute('INSERT INTO account_security VALUES(%s,%s)', (user_id, db.now()))
        conn.execute('INSERT INTO collections VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)', ('preview-evidence', owner, 'Checkout failure · recorded fixture', 'Authorization failed during checkout.', None, 'paste', db.now(), 'preview', json.dumps(summary)))
        for row in rows:
            conn.execute('INSERT INTO evidence(id,user_id,kind,service,timestamp,payload,collection_id) VALUES(%s,%s,%s,%s,%s,%s,%s)', (row['id'], owner, row['kind'], row['service'], row.get('timestamp'), json.dumps(row), 'preview-evidence'))
        conn.execute('INSERT INTO investigations(id,collection_id,user_id,engine,state,stage,created_at,completed_at,report,usage,supplied_excerpts) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                     ('preview-report', 'preview-evidence', owner, 'groq', 'completed', 'completed', db.now(), db.now(), json.dumps(report), json.dumps({'report_format': 'concise-v1', 'requests': 0, 'model': 'recorded-fixture', 'supplied_evidence_ids': ['exception', 'root']}), json.dumps(supplied)))

def respond(request):
    assert request.url.host == 'api.groq.com', 'Only mocked Groq requests belong in this fixture.'
    MODEL_CALLS.append({'host': request.url.host, 'method': request.method})
    payload = json.loads(request.content)
    assert len(request.content) <= 10 * 1024
    if 'tools' in payload:
        return httpx.Response(200, json={'choices': [{'message': {'content': 'READY'}, 'finish_reason': 'stop'}]})
    schema = payload.get('response_format', {}).get('json_schema', {}).get('schema', {})
    if 'relationship' in schema.get('properties', {}):
        result = {'summary': 'Mocked preview review using saved evidence.', 'relationship': 'cannot_assess', 'explanations': [], 'questions': [], 'limitations': ['No live model was called.']}
    else:
        result = {'assessment': 'inconclusive', 'origin': 'unknown', 'affected_services': [],
                  'summary': 'This is a mocked preview response. No live model was called.', 'evidence_ids': [], 'hypotheses': [],
                  'supporting_evidence': [], 'recommendations': ['Inspect the saved observations.'], 'limitations': ['Preview fixture response.']}
        data = json.loads(payload['messages'][-1]['content'].split('\nOperational data: ', 1)[1].split('\nAdvisory initial triage: ', 1)[0])
        table = data['evidence']['tables'].get('log', {})
        rows = [dict(zip(table['columns'], values)) for values in table.get('rows', [])]
        source = next((row for row in rows if row.get('level') == 'error'), None)
        if source:
            result.update(evidence_ids=[source['id']], affected_services=[source['service']],
                          supporting_evidence=[{'finding': 'The supplied observation records an error-level log entry.', 'evidence_ids': [source['id']]}])
    return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}, 'finish_reason': 'stop'}], 'usage': {'total_tokens': 0}})

@asynccontextmanager
async def lifespan(app):
    with (tempfile.TemporaryDirectory(prefix='tracedesk-preview-') as directory, IsolatedDatabase(directory),
          patch.dict(os.environ, {'DESK_EMAIL_KEY': Fernet.generate_key().decode()}),
          patch.object(email_service, 'configured', return_value=True),
          patch.object(config, 'PUBLIC_URL', f'http://127.0.0.1:{FIXTURE_PORT}')):
        db.init()
        demo.install()
        seed()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            app.state.investigator = Investigator(client)
            app.state.investigator.start()
            app.state.sentry = SentryClient(client)
            app.state.demo_error = None
            print(f'PREVIEW_READY http://127.0.0.1:{FIXTURE_PORT} - isolated test data and mocked providers', flush=True)
            try:
                yield
            finally:
                await app.state.investigator.close()

def fixture_status(request):
    return JSONResponse({'fixture': True, 'provider_calls': len(MODEL_CALLS), 'models': 'mocked',
                         'email_delivery': 'captured', 'fixture_version': 2})

def fixture_mail(request):
    """Test-only mailbox; the fixture never starts an email delivery worker."""
    row = db.one('SELECT j.payload FROM email_jobs j JOIN users u ON u.id=j.user_id '
                 'WHERE u.email=%s AND j.kind=%s AND j.payload IS NOT NULL ORDER BY j.created_at DESC LIMIT 1',
                 (request.query_params.get('email'), request.query_params.get('purpose')))
    if not row:
        return JSONResponse({'error': 'No captured fixture email.'}, status_code=404)
    payload = json.loads(email_service.encryption().decrypt(row['payload'].encode()))
    link = re.search(r'/(?:account)?#auth/(?:verify|reset)/[A-Za-z0-9_-]+', payload['text'])
    return JSONResponse({'path': link.group(0) if link else None})

def fixture_reset_auth_attempts(request):
    # Separate browser acceptance cases share one loopback IP. The backend suite
    # exercises the real rate limit; isolate the multi-step account case here.
    auth.attempts.clear()
    return JSONResponse({'reset': True})

app = create_app()
app.router.routes.insert(0, Route('/__fixture/status', fixture_status))
app.router.routes.insert(0, Route('/__fixture/mail', fixture_mail))
app.router.routes.insert(0, Route('/__fixture/reset-auth-attempts', fixture_reset_auth_attempts, methods=['POST']))
app.router.lifespan_context = lifespan
if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=FIXTURE_PORT, log_level='warning')
