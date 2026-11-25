import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from database import IsolatedDatabase
from backend import db, queries
from backend.api import create_app
from backend.evidence import EvidenceTools, EvidenceContext, INPUT_BYTES, encoded, model_observation
from backend.imports import parse_logs
from backend.investigator import Investigator, CONCISE_REPORT_SCHEMA, validate_report
from backend.selection import select_blocks, excerpt, provenance
from backend.signatures import detect
from test_provider_contract import FixtureTools, REPORT, success

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/workspace.json').read_text())


class SelectionTests(unittest.TestCase):
    def test_specific_and_multiple_signatures_are_symptoms(self):
        cases = {'NullPointerException': 'null_reference', 'TypeError': 'type_error', 'OutOfMemoryError: Java heap space': 'heap_exhaustion',
                 'GC overhead limit exceeded': 'gc_pressure', 'Downstream request timed out': 'dependency_timeout',
                 'Database query timeout': 'database_timeout', 'Service unavailable': 'service_unavailable',
                 'CrashLoopBackOff': 'crash_loop', 'readiness probe failed': 'readiness_failure',
                 'pod api terminated': 'pod_termination', 'no such table: accounts': 'missing_schema',
                 'deadlock detected': 'deadlock', 'TLS handshake failed': 'tls_failure', 'EAI_AGAIN': 'dns_failure',
                 'ECONNREFUSED': 'connection_refused', 'ECONNRESET': 'connection_reset', 'some observation': 'unknown'}
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertIn(expected, detect({'message': message}))
        self.assertEqual(set(detect({'message': 'NullPointerException and service unavailable'})), {'null_reference', 'service_unavailable', 'mixed'})
        self.assertNotIn('memory_leak', detect({'message': 'OutOfMemoryError'}))

    def test_multiline_redaction_oversize_and_exact_ranges(self):
        text = '2026-11-07T10:00:00Z ERROR Traceback (most recent call last):\n  File "app.py", line 42\nValueError: invalid account\nDuring handling of the above exception:\nTypeError: unexpected None\n  token=private-test-value'
        rows, summary = parse_logs(text, 'billing')
        self.assertEqual(len(rows), 1)
        self.assertNotIn('private-test-value', rows[0]['original'])
        self.assertIn('During handling', rows[0]['message'])
        source = {**rows[0], 'message': 'Exception in thread main\n' + 'frame content\n' * 2000 + 'Caused by: NullPointerException at Billing.java:42'}
        selected = excerpt(model_observation(source), 1800)
        self.assertLessEqual(len(encoded(selected)), 1800)
        self.assertIn('_excerpt', selected)
        meta = provenance([selected], {source['id']: source})[0]
        ranges = meta['fields']['message']['ranges']
        pieces = [source['message'][a:b] for a, b in ranges]
        self.assertEqual(selected['message'], '\n[... omitted ...]\n'.join(pieces))
        self.assertIn('NullPointerException', selected['message'])

    def test_context_rules_preserve_conflicts_and_do_not_invent_ids(self):
        rows = deepcopy(FIXTURE['observations'])
        chosen, metadata = select_blocks(rows, 'billing null exception')
        ids = {row['id'] for row in chosen}
        self.assertTrue({'exception', 'root', 'catalog', 'billing-first', 'billing-second', 'success'} <= ids)
        self.assertTrue(any(item['reason'] == 'same_trace_id' for item in metadata['sources']['catalog']['selection']))
        untimed = [{'id': str(i), 'kind': 'log', 'service': 'a', 'timestamp': None, 'message': 'observation'} for i in range(20)]
        untimed[10]['message'] = 'NullPointerException'
        _, meta = select_blocks(untimed)
        near = [key for key, value in meta['sources'].items() if any(reason['anchor_id'] == '10' and reason['reason'] == 'adjacent_5_context_only' for reason in value['selection'])]
        self.assertEqual(set(near), {str(i) for i in range(5, 16)})
        self.assertEqual(meta['sources']['10']['occurrences'], 1)
        self.assertTrue(all(not row.get('trace_id') for row in untimed))

    def test_recorded_flow_has_only_parent_edges_and_request_fallback(self):
        rows = [row for row in FIXTURE['observations'] if row.get('trace_id') == 'trace-a']
        flow = queries.recorded_flow(rows, scope_label='trace-a')
        self.assertEqual({(edge['source'], edge['target']) for edge in flow['edges']}, {('root', 'catalog'), ('root', 'billing-first'), ('root', 'billing-second')})
        self.assertTrue(next(node for node in flow['nodes'] if node['id'] == 'orphan')['missing_parent'])
        events = queries.recorded_flow([row for row in FIXTURE['observations'] if row.get('request_id') == 'request-only'], scope_label='request-only')
        self.assertEqual(events['mode'], 'events')
        self.assertEqual([row['id'] for row in events['events']], ['request-context', 'memory'])
        self.assertEqual(events['edges'], [])

    def test_concise_limits_and_excerpt_citations(self):
        row = FIXTURE['observations'][5]
        tools = SimpleNamespace(groq=True, supplied={'exception'}, seen={'exception': row}, services={'billing', 'gateway'})
        valid = {**REPORT, 'assessment': 'supported', 'summary': 'Billing authorization raised a null reference exception.',
                 'origin': 'billing', 'affected_services': ['billing'], 'evidence_ids': ['exception'],
                 'supporting_evidence': [{'finding': 'Authorization dereferenced an absent invoice.', 'evidence_ids': ['exception']}]}
        self.assertEqual(validate_report(valid, tools, concise=True), valid)
        for update in ({'summary': 'word ' * 251}, {'recommendations': ['one', 'two', 'three']},
                       {'limitations': ['one', 'two', 'three']}, {'origin': 'gateway'},
                       {'supporting_evidence': [{'finding': 'Failure', 'evidence_ids': ['invented']}]}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_report({**valid, **update}, tools, concise=True)
        historical = deepcopy(FIXTURE['historical_report'])
        self.assertEqual(validate_report(historical, tools), FIXTURE['historical_report'])


class GraphCorrectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_correction_and_no_unbounded_provider_retry(self):
        requests = []
        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            report = {**REPORT, 'summary': 'word ' * 251} if len(requests) == 1 else REPORT
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(report)}, 'finish_reason': 'stop'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with patch('backend.investigator.GROQ_KEY', 'mock-key'):
                report, usage = await Investigator(client).groq(FixtureTools(), lambda *args: None, snapshot_only=True)
        self.assertEqual(len(requests), 2)
        self.assertEqual(report, REPORT)
        self.assertEqual([item['accepted'] for item in usage['report_attempts']], [False, True])
        self.assertTrue(all(len(encoded(request)) <= INPUT_BYTES for request in requests))
        self.assertEqual(usage['stage'], 'completed')


class WorkspaceDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.database = IsolatedDatabase(cls.directory.name).start()
        db.init()
        with db.connection() as conn, conn.pipeline():
            for owner in ('workspace-a', 'workspace-b'):
                conn.execute('INSERT INTO users VALUES(%s,%s,%s,%s,%s)', (owner, owner, owner, 'hash', db.now()))
                conn.execute('INSERT INTO collections VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)', (owner, owner, 'Fixture', '', None, 'paste', db.now(), owner, json.dumps({'services': ['gateway', 'catalog', 'billing', 'database', 'worker'], 'observations': 271, 'kinds': ['log','span']})))
            for row in FIXTURE['observations'] + [{**FIXTURE['observations'][5], 'id': 'repeat-' + str(i)} for i in range(260)]:
                conn.execute('INSERT INTO evidence(id,user_id,kind,service,timestamp,payload,collection_id) VALUES(%s,%s,%s,%s,%s,%s,%s)', (row['id'], 'workspace-a', row['kind'], row['service'], row.get('timestamp'), json.dumps(row), 'workspace-a'))
            conn.execute('INSERT INTO evidence(id,user_id,kind,service,timestamp,payload,collection_id) VALUES(%s,%s,%s,%s,%s,%s,%s)', ('private-b', 'workspace-b', 'log', 'billing', db.now(), json.dumps({'message':'NullPointerException', 'level':'error'}), 'workspace-b'))
        cls.before = db.all_rows('SELECT id,payload,timestamp FROM evidence ORDER BY id')

    @classmethod
    def tearDownClass(cls):
        cls.database.stop()
        cls.directory.cleanup()

    def test_histogram_totals_across_pages_and_unknown_time_disclosure(self):
        scope = {'owner':'workspace-a', 'collection_id':'workspace-a', 'filters':queries.Filters(kind='log')}
        page = queries.page(**scope, limit=20)
        chart = queries.histogram(**scope)
        self.assertEqual(page['total'], 266)
        self.assertEqual(len(page['evidence']), 20)
        self.assertEqual(sum(bucket['count'] for bucket in chart['buckets']), 264)
        self.assertEqual(chart['unknown_timestamps'], 2)
        selected = queries.page(**scope, selected='unknown-time', limit=20)
        self.assertIn('unknown-time', [row['id'] for row in selected['evidence']])
        self.assertGreater(selected['offset'], 0)
        explicit_page = queries.page(**scope, selected='unknown-time', limit=20, offset=0, locate=False)
        self.assertEqual(explicit_page['offset'], 0)
        self.assertTrue(explicit_page['selected_in_filter'])
        bucket = chart['buckets'][0]
        for bucket in chart['buckets']:
            narrowed = queries.page(**{**scope, 'filters':queries.Filters(kind='log',start_time=bucket['start'],end_time=bucket['end'])})
            self.assertEqual(narrowed['total'], bucket['count'])

    def test_filters_signatures_full_request_and_preservation(self):
        scope = {'owner':'workspace-a', 'collection_id':'workspace-a'}
        self.assertEqual(queries.page(**scope, filters=queries.Filters(error_type='heap_exhaustion'))['total'], 1)
        self.assertEqual(queries.page(**scope, filters=queries.Filters(query='Billing.java:42'))['total'], 261)
        self.assertEqual(queries.page(**scope, filters=queries.Filters(request_id='request-only'))['total'], 2)
        flow = queries.request_flow(**scope, filters=queries.Filters(trace_id='trace-a',level='error',service='billing'))
        self.assertIn('catalog', [node['service'] for node in flow['nodes']])
        self.assertEqual(len(flow['edges']), 3)
        self.assertEqual(queries.page('workspace-b',collection_id='workspace-a')['total'], 0)
        overview = queries.summary(**scope)
        self.assertEqual(overview['counts']['observations'], 271)
        self.assertEqual(overview['counts']['error_logs'], 263)
        self.assertEqual(db.all_rows('SELECT id,payload,timestamp FROM evidence ORDER BY id'), self.before)
        for row in FIXTURE['observations']:
            stored = db.all_rows('SELECT signature FROM evidence_error_tags WHERE evidence_id=%s AND version=%s', (row['id'], 'signatures-v2'))
            self.assertEqual({item['signature'] for item in stored}, set(detect(row)))

    def test_scoped_endpoints_and_bad_filters(self):
        user = {'id':'workspace-a', 'role':'regular', 'email_verified':True}
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),base_url='http://test') as client:
                with patch('backend.auth.current', return_value=user):
                    with patch('backend.db.connection', wraps=db.connection) as opened:
                        dashboard = await client.get('/api/dashboard')
                        self.assertEqual(dashboard.status_code, 200, dashboard.text)
                        self.assertEqual(opened.call_count, 1)
                        self.assertRegex(dashboard.headers['server-timing'], r'auth;dur=\d+\.\d, db;dur=\d+\.\d, total;dur=\d+\.\d')
                    for suffix in ('logs','histogram','timeline','flow','analysis'):
                        result = await client.get('/api/collections/workspace-b/' + suffix)
                        self.assertEqual(result.status_code, 404)
                        result = await client.get('/api/collections/workspace-a/' + suffix + '?start_time=invalid')
                        self.assertEqual(result.status_code, 400)
                    timeline = (await client.get('/api/collections/workspace-a/timeline?trace_id=trace-a')).json()
                    root = next(row for row in timeline['evidence'] if row['id'] == 'root')
                    self.assertEqual(root['status'], 'error')
                    self.assertEqual(root['duration_ms'], 104)
                    for endpoint in ('dashboard','errors','investigations','logs'):
                        result = await client.get('/api/' + endpoint)
                        self.assertEqual(result.status_code, 200, result.text)
                        self.assertNotIn('private-b', result.text)
                        denied = await client.get('/api/' + endpoint + '?collection_id=workspace-b')
                        self.assertEqual(denied.status_code, 404)
                        self.assertNotIn('private-b', denied.text)
                    for suffix in ('logs','histogram','timeline','flow','analysis'):
                        result = await client.get('/api/collections/workspace-a/' + suffix + '?level=error&level=info')
                        self.assertEqual(result.status_code, 400)
        asyncio.run(check())

    def test_naive_legacy_timestamp_stays_unknown_without_changing_source(self):
        row = {'message': 'legacy timestamp', 'level': 'WARNING', 'parent_span_id': 'legacy-parent'}
        with db.connection() as conn:
            conn.execute('INSERT INTO evidence(id,user_id,kind,service,timestamp,payload,collection_id) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                         ('legacy-time', 'workspace-b', 'log', 'billing', '2026-11-07T10:00:00', json.dumps(row), 'workspace-b'))
        try:
            indexed = db.one("SELECT observed_at,parent_id,level FROM evidence_index WHERE evidence_id='legacy-time'")
            self.assertIsNone(indexed['observed_at'])
            self.assertEqual(indexed['parent_id'], 'legacy-parent')
            self.assertEqual(indexed['level'], 'warning')
            saved = db.one("SELECT * FROM evidence WHERE id='legacy-time'")
            self.assertEqual(saved['timestamp'], '2026-11-07T10:00:00')
            self.assertEqual(json.loads(saved['payload']), row)
            self.assertEqual(db.public_evidence(saved)['parent_id'], 'legacy-parent')
        finally:
            with db.connection() as conn:
                conn.execute("DELETE FROM evidence_error_tags WHERE evidence_id='legacy-time'")
                conn.execute("DELETE FROM evidence_index WHERE evidence_id='legacy-time'")
                conn.execute("DELETE FROM evidence_arrivals WHERE evidence_id='legacy-time'")
                conn.execute("DELETE FROM evidence WHERE id='legacy-time'")
