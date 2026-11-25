"""Isolated PostgreSQL integration; every provider response is mocked."""
import json
import asyncio
import time
from datetime import timedelta
from unittest.mock import patch
import unittest
import httpx
from backend import config, db
from backend.datadog_client import DatadogClient
from backend.automation import AutomationWorker
from backend.evidence_scope import create_composite, rows
from backend.investigation_journal import Journal
from backend.evidence_contracts import digest
from backend.investigator import Investigator
from test_provider_contract import REPORT
import test_sentry


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    client = test_sentry.SentryTests.client
    provider = test_sentry.SentryTests.provider
    log = test_sentry.SentryTests.log
    connection = test_sentry.SentryTests.connection

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    def setUp(self):
        test_sentry.SentryTests.setUp(self)
        self.enterContext(patch.object(config, 'ENABLE_DATADOG', True))
        self.app.state.datadog = DatadogClient(self.http)
        self.worker.datadog = self.app.state.datadog
        self.handler = self.datadog

    def datadog(self, request):
        self.calls.append(request)
        path = request.url.path
        if path.endswith('/validate'):
            return httpx.Response(200, json={'valid': True})
        if path.endswith('/query'):
            return httpx.Response(200, json={'series': []})
        if path.endswith('/monitor/12'):
            return httpx.Response(200, json={'id': 12, 'name': 'Pool pressure', 'overall_state': 'Alert'})
        if path.endswith('/monitor'):
            return httpx.Response(200, json=[])
        records = [] if '/spans/' in path else [{'id': 'record', 'attributes': {'timestamp': self.now.isoformat(), 'service': 'payment', 'message': 'Connection acquisition timed out', 'status': 'error', 'tags': ['env:test']}}]
        return httpx.Response(200, json={'data': records, 'meta': {}})

    async def connect(self):
        async with self.client() as client:
            response = await client.post('/api/connections/datadog', json={'site': 'eu1', 'name': 'Fixture Datadog', 'api_key': 'a'*32, 'application_key': 'b'*40})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertNotIn('credentials', response.json())
            return response.json()['id']

    async def test_connection_import_scope_and_legacy_sentry_isolation(self):
        identifier = await self.connect()
        async with self.client('bob') as other:
            self.assertEqual((await other.post(f'/api/connections/datadog/{identifier}/check', json={})).status_code, 404)
        async with self.client() as client:
            self.assertEqual((await client.get('/api/connections/sentry')).json()['connections'], [])
            response = await client.post(f'/api/connections/datadog/{identifier}/imports', json={'datasets': ['logs'], 'start': (self.now-timedelta(minutes=1)).isoformat(), 'end': self.now.isoformat()})
            self.assertEqual(response.status_code, 202, response.text)
            job = db.one('SELECT * FROM sentry_imports WHERE id=%s', (response.json()['id'],))
            await self.worker.run(job)
            self.assertEqual(db.one('SELECT state FROM sentry_imports WHERE id=%s', (job['id'],))['state'], 'completed')
            evidence = rows('alice', collection_id=job['collection_id'])
            self.assertEqual(len(evidence), 1)
            self.assertEqual(rows('bob', collection_id=job['collection_id']), [])
            self.assertEqual(json.loads(evidence[0]['payload'])['source_provider'], 'datadog')

    async def test_monitor_duplicate_recovery_before_trigger_and_shared_attempt(self):
        identifier = await self.connect()
        async with self.client() as client:
            response = await client.put(f'/api/connections/datadog/{identifier}/automation', json={'enabled': True, 'initial_email': False, 'result_email': False, 'monitor_ids': ['12']})
            self.assertEqual(response.status_code, 200, response.text)
            token = response.json()['webhook_secret']
            endpoint = f'/api/connections/datadog/{identifier}/webhook'
            event = {'monitor_id': '12', 'cycle_key': 'cycle1', 'scope': 'service:payment,env:test', 'transition': 'Recovered', 'timestamp': int(time.time()*1000)}
            self.assertEqual((await client.post(endpoint, json=event)).status_code, 401)
            for _ in range(2):
                self.assertEqual((await client.post(endpoint, json=event, headers={'X-TraceDesk-Webhook-Token': token})).status_code, 202)
            worker = AutomationWorker(self.sentry, self.worker, self.app.state.investigator)
            await worker.process_deliveries()
            self.assertEqual(db.one('SELECT COUNT(*) n FROM automatic_deliveries')['n'], 1)
            event['transition'] = 'Triggered'
            await client.post(endpoint, json=event, headers={'X-TraceDesk-Webhook-Token': token})
            await worker.process_deliveries()
            self.assertEqual(db.one('SELECT COUNT(*) n FROM automatic_attempts')['n'], 0)
            event['cycle_key'] = 'cycle2'
            await client.post(endpoint, json=event, headers={'X-TraceDesk-Webhook-Token': token})
            await worker.process_deliveries()
            self.assertEqual(db.one('SELECT COUNT(*) n FROM automatic_attempts')['n'], 1)
            self.assertEqual(db.one('SELECT COUNT(*) n FROM automatic_daily_allowance')['n'], 0)

    async def test_composite_membership_and_journal_are_owned_and_immutable(self):
        for identifier in ('one', 'two'):
            db.execute('INSERT INTO collections VALUES(%s,%s,%s,%s,NULL,%s,%s,%s,%s)', (identifier, 'alice', identifier, '', 'manual', db.now(), identifier, json.dumps({'observations': 1, 'services': ['payment'], 'kinds': ['log']})))
            db.execute("INSERT INTO evidence(id,user_id,collection_id,kind,service,timestamp,payload) VALUES(%s,'alice',%s,'log','payment',%s,%s)", (identifier+'-source', identifier, db.now(), '{"message":"recorded"}'))
        composite = create_composite('alice', ['one', 'two'], 'Combined')
        self.assertEqual(len(rows('alice', collection_id=composite)), 2)
        self.assertEqual(rows('bob', collection_id=composite), [])
        with self.assertRaises(ValueError):
            create_composite('bob', ['one', 'two'], 'Forbidden')
        with self.assertRaises(db.Error):
            db.execute('DELETE FROM collection_members WHERE collection_id=%s', (composite,))
        db.execute("INSERT INTO investigations(id,user_id,collection_id,engine,state,created_at) VALUES('report','alice',%s,'groq','queued',%s)", (composite, db.now()))
        journal = Journal('report', 'alice'); journal.event('test', {'message': {'content': 'retained', 'reasoning': 'private'}})
        self.assertNotIn('private', json.dumps(db.one('SELECT payload FROM investigation_events')['payload']))
        with self.assertRaises(ValueError):
            Journal('report', 'bob').event('test', {})
        with self.assertRaises(db.Error):
            db.execute('DELETE FROM investigation_events')

    async def monitor_handoff(self, *, enrichment=None, scope='service:payment,env:test'):
        identifier = await self.connect()
        async with self.client() as client:
            response = await client.put(f'/api/connections/datadog/{identifier}/automation', json={
                'enabled': True, 'initial_email': False, 'result_email': False, 'monitor_ids': ['12'], 'enrichment': enrichment})
            self.assertEqual(response.status_code, 200, response.text)
            event = {'monitor_id': '12', 'cycle_key': 'cycle1', 'scope': scope, 'transition': 'Triggered', 'timestamp': int(time.time()*1000)}
            await client.post(f'/api/connections/datadog/{identifier}/webhook', json=event,
                headers={'X-TraceDesk-Webhook-Token': response.json()['webhook_secret']})
        auto = AutomationWorker(self.sentry, self.worker, self.app.state.investigator)
        await auto.process_deliveries()
        db.execute('UPDATE automatic_incidents SET collect_after=0')
        auto.advance()
        job = db.one('SELECT s.* FROM sentry_imports s JOIN automatic_attempts a ON a.import_id=s.id')
        self.assertIsNotNone(job)
        await self.worker.run(job)
        return auto, job

    async def test_monitor_enrichment_admission_and_recorded_replay(self):
        self.enterContext(patch.object(config, 'GROQ_KEY', 'fixture'))
        self.enterContext(patch.object(config, 'INVESTIGATION_VERSION', 'v2'))
        sentry = self.connection()
        original = self.handler
        def providers(request):
            if 'sentry.io' in request.url.host:
                response = self.provider(request)
                data = response.json()
                if request.url.params.get('dataset') in {'logs', 'spans'}:
                    for record in data['data']:
                        record.update({'service.name': 'payment', 'environment': 'test'})
                    return httpx.Response(200, json=data)
                return response
            return original(request)
        self.handler = providers
        auto, primary = await self.monitor_handoff(enrichment={'connection_id': sentry['id'], 'project_ids': ['42']})
        auto.advance()
        secondary = db.one("SELECT * FROM sentry_imports WHERE connection_id=%s", (sentry['id'],))
        self.assertIsNotNone(secondary)
        await self.worker.run(secondary)
        auto.advance()
        attempt = db.one('SELECT * FROM automatic_attempts')
        self.assertIsNotNone(attempt['scope_collection_id'])
        evidence = [db.public_evidence(r) for r in rows('alice', collection_id=attempt['scope_collection_id'])]
        self.assertEqual({r.get('source_provider') for r in evidence}, {'sentry', 'datadog'})
        self.assertEqual(db.one('SELECT COUNT(*) n FROM automatic_daily_allowance')['n'], 0)
        def model(request):
            self.assertLessEqual(len(request.content), 10240)
            body = json.loads(request.content)
            value = {'hypotheses': [], 'actions': [], 'stop_reason': 'Evidence is incomplete.'} if body['response_format']['json_schema']['name'] == 'diagnostic_plan' else {**REPORT, 'hypothesis_updates': []}
            return httpx.Response(200, json={'model': 'fixture', 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as http:
            with patch('backend.investigator.GROQ_KEY', 'fixture'):
                await Investigator(http).investigate(attempt['investigation_id'])
        saved = db.one('SELECT * FROM investigations WHERE id=%s', (attempt['investigation_id'],))
        self.assertEqual(saved['state'], 'completed', saved['error'])
        self.assertEqual(db.one('SELECT attempts FROM automatic_daily_allowance')['attempts'], 1)
        call_count = len(self.calls)
        async with self.client() as client:
            response = await client.get(f"/api/investigations/{saved['id']}/replay?export=1")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['integrity'], 'verified', response.text)
            self.assertEqual(len(response.json()['evidence']), len(evidence))
        async with self.client('bob') as client:
            self.assertEqual((await client.get(f"/api/investigations/{saved['id']}/replay")).status_code, 404)
        self.assertEqual(len(self.calls), call_count)

    async def test_monitor_missing_scope_retains_alert_and_queries_no_telemetry(self):
        auto, job = await self.monitor_handoff(scope='env:test')
        summary = json.loads(db.one('SELECT summary FROM collections WHERE id=%s', (job['collection_id'],))['summary'])
        self.assertEqual(summary['observations'], 1)
        self.assertTrue(all(c['state'] == 'unavailable' for c in summary['datasets'].values()))
        auto.advance()
        self.assertIsNotNone(db.one('SELECT investigation_id FROM automatic_attempts')['investigation_id'])

    async def test_malformed_api_inputs_and_automation_reload(self):
        identifier = await self.connect()
        async with self.client() as client:
            for invalid in ([], {}, 5):
                response = await client.post('/api/connections/datadog', json={'site': invalid, 'name': 'Fixture', 'api_key': 'a'*32, 'application_key': 'b'*40})
                self.assertEqual(response.status_code, 400)
                response = await client.post(f'/api/connections/datadog/{identifier}/imports', json={'datasets': [invalid]})
                self.assertEqual(response.status_code, 400)
            response = await client.put(f'/api/connections/datadog/{identifier}/automation', json={'enabled': False, 'initial_email': False, 'result_email': True, 'monitor_ids': ['12'], 'metric_queries': ['avg:system.cpu.user{service:payment}']})
            self.assertEqual(response.status_code, 200, response.text)
            reloaded = (await client.get(f'/api/connections/datadog/{identifier}/automation')).json()
            self.assertEqual(reloaded['configuration']['monitor_ids'], ['12'])
            self.assertNotIn('webhook_secret', reloaded)
            self.assertNotIn('webhook_digest', reloaded)

    async def test_pagination_retains_earlier_warnings_and_nonempty_coverage(self):
        identifier = await self.connect()
        from backend.providers import ProviderPage
        record = {'id': 'warning-record', 'attributes': {'timestamp': self.now.isoformat(), 'service': 'payment', 'message': 'Observed failure', 'status': 'error'}}
        async with self.client() as client:
            response = await client.post(f'/api/connections/datadog/{identifier}/imports', json={
                'datasets': ['logs'], 'start': (self.now-timedelta(minutes=1)).isoformat(), 'end': self.now.isoformat()})
        self.assertEqual(response.status_code, 202, response.text)
        job = db.one('SELECT * FROM sentry_imports WHERE id=%s', (response.json()['id'],))
        with patch.object(self.worker.datadog, 'fetch_page', side_effect=[
                ProviderPage([record], 'next', ['Provider reports incomplete search results.']), ProviderPage([])]):
            await self.worker.run(job)
        self.assertEqual(db.one('SELECT state FROM sentry_imports WHERE id=%s', (job['id'],))['state'], 'partial')
        summary = json.loads(db.one('SELECT summary FROM collections WHERE id=%s', (job['collection_id'],))['summary'])
        self.assertEqual(summary['datasets']['logs']['state'], 'partial')
        self.assertEqual(summary['datasets']['logs']['received'], 1)
        self.assertEqual(summary['datasets']['logs']['warnings'], ['Provider reports incomplete search results.'])

    async def test_malformed_monitor_response_does_not_replace_saved_settings(self):
        identifier = await self.connect()
        async with self.client() as client:
            saved = {'enabled': False, 'initial_email': False, 'result_email': True, 'monitor_ids': ['12']}
            self.assertEqual((await client.put(f'/api/connections/datadog/{identifier}/automation', json=saved)).status_code, 200)
            with patch.object(self.app.state.datadog, 'request', return_value=[]):
                response = await client.put(f'/api/connections/datadog/{identifier}/automation', json={**saved, 'enabled': True})
            self.assertEqual(response.status_code, 400, response.text)
            reloaded = (await client.get(f'/api/connections/datadog/{identifier}/automation')).json()
            self.assertFalse(reloaded['enabled'])
            self.assertEqual(reloaded['configuration']['monitor_ids'], ['12'])
