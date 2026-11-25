"""Durable automatic flow with isolated PostgreSQL and mocked external I/O."""
import asyncio
import hashlib
import hmac
import json
import os
import time
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import test_sentry as fixture
from backend import automation, config, db, email_service
from backend.investigator import Investigator
from backend.sentry_imports import create_import, parameters


class AutomationTests(unittest.IsolatedAsyncioTestCase):
    provider = fixture.SentryTests.provider
    log = fixture.SentryTests.log
    connection = fixture.SentryTests.connection
    client = fixture.SentryTests.client

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    def setUp(self):
        fixture.SentryTests.setUp(self)
        self.enterContext(patch.object(config, 'GROQ_KEY', 'fixture'))
        self.enterContext(patch.dict(os.environ, {'RESEND_API_KEY': 'fixture'}))
        self.row = self.connection()
        self.options = {'enabled': True, 'projects': ['42'], 'initial_email': True, 'result_email': True}
        self.issue = {'id': '123', 'project': fixture.PROJECT, 'issueCategory': 'error', 'status': 'unresolved',
            'title': 'Connection acquisition timed out', 'firstSeen': (self.now-timedelta(minutes=2)).isoformat(), 'lastSeen': self.now.isoformat(), 'activity': []}
        original = self.handler
        def handler(request):
            if request.url.path.endswith('/issues/123/'):
                return httpx.Response(200, json=self.issue)
            return original(request)
        self.handler = handler
        self.auto = automation.AutomationWorker(self.sentry, self.worker, self.app.state.investigator)

    async def enable(self):
        async with self.client() as client:
            response = await client.put('/api/connections/sentry/' + self.row['id'] + '/automation', json=self.options)
            self.assertEqual(response.status_code, 200, response.text)

    async def delivery(self, action='created', *, issue=None, nonce=None, forged=False):
        value = {'installation': {'uuid': self.row['installation_id']}, 'action': action, 'data': {'issue': issue or self.issue}}
        if nonce:
            value['actor'] = {'id': nonce}
        raw = json.dumps(value, separators=(',', ':')).encode()
        headers = {'sentry-hook-resource': 'issue', 'sentry-hook-signature': 'forged' if forged else hmac.new(config.SENTRY_CLIENT_SECRET.encode(), raw, hashlib.sha256).hexdigest()}
        async with self.client() as client:
            return await client.post('/api/connections/sentry/webhook', content=raw, headers=headers)

    def episode(self):
        return db.one('SELECT * FROM automatic_incidents ORDER BY episode DESC LIMIT 1')

    def attempt(self):
        return db.one('SELECT * FROM automatic_attempts ORDER BY created_at DESC,id DESC LIMIT 1')

    async def handoff(self):
        await self.enable()
        self.assertEqual((await self.delivery()).status_code, 200)
        await self.auto.process_deliveries()
        db.execute('UPDATE automatic_incidents SET collect_after=0')
        self.auto.advance()
        job = db.one('SELECT * FROM sentry_imports WHERE id=%s', (self.attempt()['import_id'],))
        await self.worker.run(job)
        self.auto.advance()
        return self.attempt()

    def mail(self, kind):
        row = db.one('SELECT * FROM email_jobs WHERE kind=%s ORDER BY created_at DESC LIMIT 1', (kind,))
        return row, json.loads(email_service.encryption().decrypt(row['payload'].encode()))

    async def test_opt_in_signature_project_ownership_demo_csrf_and_verification(self):
        async with self.client() as client:
            default = (await client.get('/api/connections/sentry/' + self.row['id'] + '/automation')).json()
            self.assertFalse(default['enabled'])
            self.assertTrue(default['initial_email'] and default['result_email'])
        self.assertEqual((await self.delivery()).status_code, 403)
        await self.enable()
        self.assertEqual((await self.delivery(forged=True)).status_code, 401)
        wrong = {**self.issue, 'project': {**fixture.PROJECT, 'id': '999'}}
        self.assertEqual((await self.delivery(issue=wrong)).status_code, 403)
        for owner in ('bob', 'demo'):
            async with self.client(owner) as client:
                self.assertIn((await client.put('/api/connections/sentry/' + self.row['id'] + '/automation', json=self.options)).status_code, (403, 404))
        async with self.client(csrf=False) as client:
            self.assertEqual((await client.put('/api/connections/sentry/' + self.row['id'] + '/automation', json=self.options)).status_code, 403)
        db.execute("DELETE FROM account_security WHERE user_id='alice'")
        async with self.client() as client:
            self.assertEqual((await client.put('/api/connections/sentry/' + self.row['id'] + '/automation', json=self.options)).status_code, 403)
        self.assertEqual(db.one('SELECT COUNT(*) n FROM automatic_incidents')['n'], 0)

    async def test_immediate_alert_duplicates_and_collection_delay(self):
        await self.enable()
        self.assertEqual((await self.delivery()).status_code, 200)
        await self.auto.process_deliveries()
        episode = self.episode()
        self.assertEqual(episode['state'], 'queued')
        _, mail = self.mail('incident_alert')
        self.assertIn('Queued', mail['text'])
        self.assertIn('No cause has been assessed', mail['text'])
        self.assertIn('/incidents/' + episode['id'], mail['html'])
        self.assertTrue((await self.delivery()).json()['duplicate'])
        await self.delivery(nonce='different-delivery')
        await self.auto.process_deliveries()
        self.auto.advance()
        self.assertEqual(db.one('SELECT COUNT(*) n FROM automatic_incidents')['n'], 1)
        self.assertEqual(db.one('SELECT COUNT(*) n FROM email_jobs')['n'], 1)
        self.assertEqual(db.one('SELECT COUNT(*) n FROM sentry_imports')['n'], 0)
        self.assertEqual(self.jobs, [])

    async def test_alert_to_saved_cited_result_no_model_on_reads(self):
        self.issue['firstSeen'] = self.now.isoformat()
        attempt = await self.handoff()
        self.assertIsNotNone(attempt['investigation_id'])
        requests = []
        def groq(request):
            requests.append(request)
            self.assertLessEqual(len(request.content), 10240)
            payload = json.loads(request.content)
            if 'response_format' not in payload:
                return httpx.Response(200, json={'choices': [{'message': {'content': 'READY'}}]})
            data = json.loads(payload['messages'][1]['content'].split('Operational data: ')[1])
            # Resolve column order rather than assuming source position.
            ids = [dict(zip(table['columns'], row))['id'] for table in data['evidence']['tables'].values() for row in table['rows']]
            self.assertIn('auto-trigger-' + attempt['import_id'], ids)
            self.assertGreater(len(ids), 1, 'Supported assessment needs collected evidence beyond the issue title.')
            support_ids = [source for source in ids if source != 'auto-trigger-' + attempt['import_id']][:1]
            report = {'assessment': 'supported', 'summary': 'The observed operation could not acquire a connection.', 'origin': 'unknown',
                'affected_services': [], 'evidence_ids': support_ids, 'hypotheses': [], 'supporting_evidence': [{'finding': 'The cited observation reports an acquisition timeout.', 'evidence_ids': support_ids}],
                'recommendations': ['Inspect connection ownership for the failed operation.'], 'limitations': ['The evidence does not establish why capacity was unavailable.']}
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(report)}}]})
        with patch('backend.investigator.GROQ_KEY', 'mock'):
            async with httpx.AsyncClient(transport=httpx.MockTransport(groq)) as client:
                investigator = Investigator(client)
                await investigator.investigate(attempt['investigation_id'])
        self.auto.advance(); self.auto.advance()
        self.assertEqual(self.episode()['state'], 'completed')
        self.assertLessEqual(len(requests), 6)
        self.assertEqual(db.one('SELECT attempts FROM automatic_daily_allowance')['attempts'], 1)
        _, result = self.mail('incident_result')
        saved_report = json.loads(db.one('SELECT report FROM investigations WHERE id=%s', (attempt['investigation_id'],))['report'])
        self.assertIn('Assessed root cause and observed impact', result['text'])
        self.assertIn(saved_report['summary'], result['text'])
        self.assertIn(saved_report['supporting_evidence'][0]['finding'], result['text'])
        self.assertIn(saved_report['recommendations'][0], result['text'])
        self.assertIn(saved_report['limitations'][0], result['text'])
        self.assertIn('/logs?', result['text'])
        count = len(requests)
        async with self.client() as client:
            for path in ('/api/automatic-incidents', '/api/automatic-incidents/' + self.episode()['id'], '/api/investigations/' + attempt['investigation_id']):
                response = await client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(requests), count)
        self.assertEqual(db.one('SELECT COUNT(*) n FROM email_jobs')['n'], 2)
        async with self.client() as client:
            detail = (await client.get('/api/automatic-incidents/' + self.episode()['id'])).json()
            self.assertFalse(detail['retry_allowed'])
            self.assertEqual((await client.post('/api/automatic-incidents/' + self.episode()['id'] + '/retry', json={})).status_code, 409)
        async with self.client('bob') as client:
            self.assertEqual((await client.get('/api/automatic-incidents/' + self.episode()['id'])).status_code, 404)

    async def test_issue_only_evidence_requires_inconclusive_saved_result(self):
        attempt = await self.handoff()
        requests = []
        def groq(request):
            requests.append(request)
            payload = json.loads(request.content)
            if 'response_format' not in payload:
                return httpx.Response(200, json={'choices': [{'message': {'content': 'READY'}}]})
            schema = payload['response_format']['json_schema']['schema']
            self.assertEqual(schema['properties']['assessment']['enum'], ['inconclusive'])
            self.assertEqual(schema['properties']['origin']['enum'], ['unknown'])
            data = json.loads(payload['messages'][1]['content'].split('Operational data: ')[1])
            ids = [dict(zip(table['columns'], row))['id'] for table in data['evidence']['tables'].values() for row in table['rows']]
            self.assertEqual(ids, ['auto-trigger-' + attempt['import_id']])
            report = {'assessment': 'inconclusive', 'summary': 'A connection acquisition timeout was reported. Its cause was not established.',
                'origin': 'unknown', 'affected_services': [], 'evidence_ids': ids, 'hypotheses': [],
                'supporting_evidence': [{'finding': 'The Sentry issue title reports the timeout symptom.', 'evidence_ids': ids}],
                'recommendations': ['Retrieve the original exception and correlated operation.'],
                'limitations': ['Only the triggering issue notification was available in the evidence window.']}
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(report)}}]})
        with patch('backend.investigator.GROQ_KEY', 'mock'):
            async with httpx.AsyncClient(transport=httpx.MockTransport(groq)) as client:
                await Investigator(client).investigate(attempt['investigation_id'])
        self.auto.advance(); self.auto.advance()
        self.assertEqual(self.episode()['state'], 'inconclusive')
        self.assertEqual(len(requests), 2)
        _, result = self.mail('incident_result')
        self.assertIn('Cause not established', result['text'])
        self.assertNotIn('Assessed root cause', result['text'])
        self.assertIn('Only the triggering issue notification', result['text'])

    async def test_genuine_reopen_reordered_transitions_and_repeated_open_events(self):
        await self.enable(); await self.delivery(); await self.auto.process_deliveries()
        first = self.episode()['id']
        self.issue['activity'] = [{'id': 'already-open', 'type': 'set_unresolved', 'dateCreated': self.now.isoformat()}]
        await self.delivery('unresolved'); await self.auto.process_deliveries()
        self.assertEqual(self.episode()['episode'], 1)
        self.issue.update(status='resolved', activity=[{'id': 'close', 'type': 'set_resolved', 'dateCreated': self.now.isoformat()}])
        await self.delivery('resolved'); await self.auto.process_deliveries()
        self.assertEqual(self.episode()['lifecycle'], 'resolved')
        self.issue.update(status='unresolved', substatus='regressed', activity=[{'id': 'open', 'type': 'set_regression', 'dateCreated': (self.now+timedelta(seconds=1)).isoformat()}])
        await self.delivery('unresolved'); await self.auto.process_deliveries()
        self.assertEqual(self.episode()['episode'], 2)
        self.assertNotEqual(self.episode()['id'], first)
        await self.delivery('unresolved', nonce='again'); await self.auto.process_deliveries()
        stale = {**self.issue, 'status': 'resolved'}
        await self.delivery('resolved', issue=stale, nonce='reordered'); await self.auto.process_deliveries()
        self.assertEqual(db.one('SELECT COUNT(*) n FROM automatic_incidents')['n'], 2)
        self.assertEqual(self.episode()['lifecycle'], 'open')
        self.assertEqual(db.one('SELECT COUNT(*) n FROM email_jobs')['n'], 2)

    async def test_daily_limit_deferred_resumption_and_expiry_notice(self):
        await self.enable()
        day = datetime.now(timezone.utc).date()
        db.execute("INSERT INTO automatic_daily_allowance VALUES('alice',%s,5)", (day,))
        await self.delivery(); await self.auto.process_deliveries()
        self.assertEqual(self.episode()['state'], 'deferred')
        self.assertIn('Deferred', self.mail('incident_alert')[1]['text'])
        db.execute('UPDATE automatic_incidents SET collect_after=0')
        self.auto.advance()
        self.assertIsNone(self.attempt()['import_id'])
        db.execute('UPDATE automatic_daily_allowance SET day=%s', (day-timedelta(days=1),))
        self.auto.advance()
        self.assertIsNotNone(self.attempt()['import_id'])
        db.execute('UPDATE automatic_attempts SET resume_until=0')
        self.auto.advance(); self.auto.advance()
        self.assertEqual(self.episode()['state'], 'interrupted')
        self.assertIn('24-hour', self.mail('incident_result')[1]['text'])

    async def test_transactional_reservation_restart_retained_retry_and_disable(self):
        attempt = await self.handoff()
        db.init()
        self.assertEqual(db.one('SELECT state FROM investigations WHERE id=%s', (attempt['investigation_id'],))['state'], 'queued')
        self.assertTrue(automation.start_attempt(attempt['investigation_id']))
        self.assertFalse(automation.start_attempt(attempt['investigation_id']))
        self.assertEqual(db.one('SELECT attempts FROM automatic_daily_allowance')['attempts'], 1)
        db.init()
        self.assertEqual(self.attempt()['state'], 'interrupted')
        self.assertFalse(automation.start_attempt(attempt['investigation_id']))
        self.auto.advance()
        async with self.client() as client:
            retry = await client.post('/api/automatic-incidents/' + self.episode()['id'] + '/retry', json={})
            self.assertEqual(retry.status_code, 202, retry.text)
            self.assertEqual((await client.post('/api/automatic-incidents/' + self.episode()['id'] + '/retry', json={})).status_code, 202)
        self.assertEqual(db.one('SELECT COUNT(*) n FROM automatic_attempts')['n'], 2)
        self.options['enabled'] = False
        await self.enable()
        self.auto.advance(); self.auto.advance()
        self.assertEqual(self.attempt()['state'], 'interrupted')
        self.assertEqual(db.one('SELECT attempts FROM automatic_daily_allowance')['attempts'], 1)

    async def test_notification_preferences_frozen_delivery_and_failure_independence(self):
        attempt = await self.handoff()
        episode = self.episode()
        db.execute("UPDATE investigations SET state='failed',error='Investigation exceeded its five-minute execution deadline.' WHERE id=%s", (attempt['investigation_id'],))
        self.auto.advance(); self.auto.advance()
        row, original = self.mail('incident_result')
        self.assertIn('could not finish', original['text'])
        db.execute("UPDATE sentry_automation SET initial_email=FALSE")
        sent = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(503))) as client:
            worker = email_service.EmailWorker(client)
            await worker.process_one()
            self.assertEqual(sent, [])
            await worker.process_one()
            self.assertEqual(len(sent), 1)
        saved = db.one('SELECT * FROM email_jobs WHERE id=%s', (row['id'],))
        self.assertEqual(saved['payload'], row['payload'])
        self.assertEqual(self.episode()['state'], 'failed')
        self.assertEqual(db.one('SELECT COUNT(*) n FROM investigations')['n'], 1)

    async def test_import_saturation_resumes_and_collection_restart_keeps_old_sources(self):
        await self.enable()
        options = parameters({'projects': ['42'], 'datasets': ['errors'], 'start': (self.now-timedelta(minutes=15)).isoformat(), 'end': self.now.isoformat(), 'title': 'Manual snapshot'}, self.row)
        manual, _ = create_import('alice', self.row, options)
        with patch.object(automation, 'QUEUE_LIMIT', 1):
            await self.delivery(); await self.auto.process_deliveries()
            self.assertEqual(self.episode()['state'], 'deferred')
            db.execute('UPDATE automatic_incidents SET collect_after=0')
            self.auto.advance()
            self.assertIsNone(self.attempt()['import_id'])
            db.execute("UPDATE sentry_imports SET state='cancelled' WHERE id=%s", (manual['id'],))
            self.auto.advance()
        first_import = self.attempt()['import_id']
        old_sources = db.all_rows('SELECT * FROM evidence')
        db.init(); self.auto.advance()
        self.assertNotEqual(self.attempt()['import_id'], first_import)
        async with self.client() as client:
            retained = (await client.get('/api/automatic-incidents/' + self.episode()['id'])).json()['attempts'][0]['imports']
        self.assertEqual({item['id'] for item in retained}, {first_import, self.attempt()['import_id']})
        for source in old_sources:
            self.assertEqual(db.one('SELECT * FROM evidence WHERE id=%s', (source['id'],)), source)
        self.assertEqual(db.one('SELECT COUNT(*) n FROM automatic_daily_allowance')['n'], 0)

    async def test_outage_defers_visible_incident_and_expires_without_provider_replay(self):
        await self.enable(); await self.delivery()
        async def unavailable(*args):
            raise automation.SentryError('Unavailable', 503)
        with patch.object(self.sentry, 'get', unavailable):
            await self.auto.process_deliveries()
            db.execute('UPDATE automatic_incidents SET collect_after=0')
            self.auto.advance()
            self.assertEqual(self.episode()['state'], 'deferred')
            self.assertIsNone(self.attempt()['import_id'])
            db.execute('UPDATE automatic_deliveries SET received_at=%s,next_attempt=0', (time.time()-86401,))
            db.execute('UPDATE automatic_attempts SET resume_until=0')
            await self.auto.process_deliveries()
            self.auto.advance(); self.auto.advance()
        self.assertEqual(self.episode()['state'], 'interrupted')
        self.assertIn('24-hour', self.mail('incident_result')[1]['text'])
        self.assertEqual(db.one('SELECT COUNT(*) n FROM investigations')['n'], 0)

    async def test_inconclusive_email_and_completed_report_survive_restart_before_handoff(self):
        attempt = await self.handoff()
        report = {'assessment': 'inconclusive', 'summary': 'An acquisition failure was reported, but its cause was not established.', 'supporting_evidence': [], 'recommendations': ['Inspect the failed operation.'], 'limitations': ['No correlated metrics were available.']}
        automation.start_attempt(attempt['investigation_id'])
        db.execute("UPDATE investigations SET state='completed',report=%s,completed_at=%s WHERE id=%s", (json.dumps(report), db.now(), attempt['investigation_id']))
        db.init(); self.auto.advance()
        self.assertEqual(self.episode()['state'], 'inconclusive')
        self.assertIn('Cause not established', self.mail('incident_result')[1]['text'])

    async def test_render_failure_does_not_rollback_delivery_or_episode(self):
        await self.enable(); await self.delivery()
        with patch('backend.email_templates.incident', side_effect=ValueError('fixture rendering failure')):
            await self.auto.process_deliveries()
        self.assertEqual(db.one('SELECT state FROM automatic_deliveries')['state'], 'accepted')
        self.assertEqual(self.episode()['state'], 'queued')
        intent = db.one('SELECT * FROM automatic_notifications')
        self.assertEqual(intent['state'], 'pending')
        self.assertEqual(db.one('SELECT COUNT(*) n FROM email_jobs')['n'], 0)
        db.execute('UPDATE automatic_notifications SET next_attempt=0')
        self.auto.process_notifications(); self.auto.process_notifications()
        self.assertEqual(db.one('SELECT COUNT(*) n FROM email_jobs')['n'], 1)
        self.assertEqual(db.one('SELECT COUNT(*) n FROM automatic_daily_allowance')['n'], 0)

    async def test_backlog_rotates_and_one_invalid_attempt_does_not_block_others(self):
        await self.enable(); await self.delivery(); await self.auto.process_deliveries()
        episode, attempt = self.episode(), self.attempt()
        with db.connection() as conn:
            conn.execute("INSERT INTO automatic_incidents(id,user_id,connection_id,issue_id,project_id,project_name,title,episode,trigger,triggered_at,received_at,resume_until,collect_after,lifecycle,state,observation) SELECT 'backlog-' || n,user_id,connection_id,issue_id || n,project_id,project_name,title,1,trigger,triggered_at,received_at,resume_until,collect_after,'open','queued',observation FROM automatic_incidents CROSS JOIN generate_series(1,101) n WHERE id=%s", (episode['id'],))
            conn.execute("INSERT INTO automatic_attempts(id,incident_id,user_id,number,state,created_at,resume_until) SELECT 'attempt-' || n,'backlog-' || n,user_id,1,'queued',created_at,resume_until FROM automatic_attempts CROSS JOIN generate_series(1,101) n WHERE id=%s", (attempt['id'],))
            conn.execute('UPDATE automatic_incidents SET collect_after=%s', (time.time()+600,))
        self.auto.advance(); self.auto.advance()
        self.assertEqual(db.one('SELECT COUNT(*) n FROM automatic_attempts WHERE checked_at>0')['n'], 102)
        with db.connection() as conn:
            conn.execute("UPDATE automatic_incidents SET collect_after=0,observation='invalid' WHERE id=%s", (episode['id'],))
            conn.execute("UPDATE automatic_incidents SET collect_after=0 WHERE id='backlog-101'")
            conn.execute("UPDATE automatic_attempts SET checked_at=0 WHERE id IN (%s,'attempt-101')", (attempt['id'],))
        self.auto.advance()
        self.assertEqual(db.one('SELECT state FROM automatic_attempts WHERE id=%s', (attempt['id'],))['state'], 'failed')
        self.assertIsNotNone(db.one("SELECT import_id FROM automatic_attempts WHERE id='attempt-101'")['import_id'])

    async def test_concurrent_reservation_never_exceeds_five(self):
        from concurrent.futures import ThreadPoolExecutor
        attempt = await self.handoff()
        episode = self.episode()
        with db.connection() as conn:
            conn.execute("INSERT INTO automatic_incidents(id,user_id,connection_id,issue_id,project_id,project_name,title,episode,trigger,triggered_at,received_at,resume_until,collect_after,lifecycle,state,observation) SELECT 'race-' || n,user_id,connection_id,issue_id || n,project_id,project_name,title,1,trigger,triggered_at,received_at,resume_until,collect_after,'open','queued',observation FROM automatic_incidents CROSS JOIN generate_series(1,5) n WHERE id=%s", (episode['id'],))
            conn.execute("INSERT INTO collections(id,user_id,title,symptom,filename,source_type,created_at,fingerprint,summary) SELECT 'race-collection-' || n,user_id,title,symptom,filename,source_type,created_at,fingerprint || '-race-' || n,summary FROM collections CROSS JOIN generate_series(1,5) n WHERE id=(SELECT collection_id FROM investigations WHERE id=%s)", (attempt['investigation_id'],))
            conn.execute("INSERT INTO investigations(id,user_id,collection_id,engine,state,created_at) SELECT 'race-report-' || n,user_id,'race-collection-' || n,engine,'queued',created_at FROM investigations CROSS JOIN generate_series(1,5) n WHERE id=%s", (attempt['investigation_id'],))
            conn.execute("INSERT INTO automatic_attempts(id,incident_id,user_id,number,state,created_at,resume_until,investigation_id) SELECT 'race-attempt-' || n,'race-' || n,user_id,1,'queued',created_at,resume_until,'race-report-' || n FROM automatic_attempts CROSS JOIN generate_series(1,5) n WHERE id=%s", (attempt['id'],))
        identifiers = [attempt['investigation_id']] + ['race-report-' + str(n) for n in range(1,6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            started = list(pool.map(automation.start_attempt, identifiers))
        self.assertEqual(sum(started), 5)
        self.assertEqual(db.one('SELECT attempts FROM automatic_daily_allowance')['attempts'], 5)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM automatic_attempts WHERE state='deferred'")['n'], 1)
        self.assertFalse(automation.start_attempt(identifiers[started.index(True)]))

    async def test_rate_limit_missing_event_time_and_resolved_before_start(self):
        await self.enable()
        issue = {**self.issue, 'firstSeen': None}
        await self.delivery(issue=issue)
        async def limited(*args):
            raise automation.SentryError('Limited', 429, retry_after=600)
        with patch.object(self.sentry, 'get', limited):
            await self.auto.process_deliveries()
        self.assertGreater(db.one('SELECT next_attempt FROM automatic_deliveries')['next_attempt'], time.time()+590)
        self.assertFalse(self.episode()['event_time_known'])
        self.assertIsNone(json.loads(self.episode()['observation'])['timestamp'])
        self.assertIn('Notification received', self.mail('incident_alert')[1]['text'])
        self.issue['status'] = 'resolved'
        db.execute('UPDATE automatic_deliveries SET next_attempt=0')
        await self.auto.process_deliveries()
        db.execute('UPDATE automatic_incidents SET collect_after=0')
        self.auto.advance()
        self.assertEqual(self.episode()['lifecycle'], 'resolved')
        self.assertIsNotNone(self.attempt()['import_id'])


if __name__ == '__main__':
    unittest.main()
