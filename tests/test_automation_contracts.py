"""Provider-boundary and evidence isolation checks without a live database."""
import json
import unittest
from unittest.mock import patch
from starlette.exceptions import HTTPException
from backend import automation, config, email_templates
from backend.evidence import EvidenceTools
from backend.sentry_imports import query_spec


class AutomationContracts(unittest.TestCase):
    def test_malformed_signed_body_is_rejected_before_storage(self):
        base = {'installation': {'uuid': 'fixture'}, 'action': 'created', 'data': {'issue': {'id': '12', 'project': {'id': '42'}}}}
        for change in ({'contexts': []}, {'contexts': {'trace': []}}, {'issueCategory': []}, {'firstSeen': {}}, {'title': []}):
            value = {**base, 'data': {'issue': {**base['data']['issue'], **change}}}
            with self.subTest(change=change), patch('backend.automation.db.connection') as connection:
                with self.assertRaises(HTTPException) as error:
                    automation.accept(json.dumps(value).encode(), value, {})
                self.assertEqual(error.exception.status_code, 400)
                connection.assert_not_called()
        with self.assertRaises(HTTPException):
            automation.accept(b'{}', {**base, 'action': []}, {})

    def test_automatic_errors_use_issue_endpoint_manual_scope_is_unchanged(self):
        connection, project = {'organization': 'org'}, {'id': '42', 'slug': 'project'}
        options = {'start': 'start', 'end': 'end'}
        manual, _ = query_spec(connection, project, 'errors', options)
        automatic, params = query_spec(connection, project, 'errors', {**options, 'issue_id': '12'}, 'cursor')
        self.assertEqual(manual, '/api/0/projects/org/project/events/')
        self.assertEqual(automatic, '/api/0/organizations/org/issues/12/events/')
        self.assertEqual(params, {'start': 'start', 'end': 'end', 'full': 'true', 'cursor': 'cursor'})

    def test_unrelated_project_errors_never_enter_automatic_model_scope(self):
        tools = object.__new__(EvidenceTools)
        tools.collection = {'summary': json.dumps({'automatic_issue_id': '12'})}
        rows = [{'id': 'trigger', 'source_issue_id': '12', 'source_dataset': 'issue'},
                {'id': 'event', 'source_issue_id': '12', 'source_dataset': 'errors', 'trace_id': 'linked'},
                {'id': 'span', 'source_dataset': 'spans', 'trace_id': 'linked'},
                {'id': 'unrelated', 'source_dataset': 'errors', 'trace_id': 'other'}]
        with patch('backend.selection.fetch_owned', return_value=rows):
            self.assertEqual([row['id'] for row in tools.snapshot()], ['trigger', 'event', 'span'])
        self.assertEqual(tools.incident_source_ids, {'trigger', 'event', 'span'})

    def test_title_and_unlinked_context_remain_symptom_only(self):
        tools = object.__new__(EvidenceTools)
        tools.collection = {'summary': json.dumps({'automatic_issue_id': '12'})}
        rows = [{'id': 'trigger', 'source_issue_id': '12', 'source_dataset': 'issue'}, {'id': 'noise', 'source_dataset': 'logs'}]
        with patch('backend.selection.fetch_owned', return_value=rows):
            self.assertEqual(tools.snapshot(), rows[:1])

    def test_received_time_is_not_claimed_as_telemetry_time(self):
        episode = dict(id='episode', project_name='project', title='symptom', triggered_at='2026-11-09T12:00:00Z', event_time_known=False, state='queued')
        with patch.object(config, 'PUBLIC_URL', 'https://desk.test'):
            rendered = email_templates.incident(episode, {}, alert=True)
        self.assertIn('Notification received (UTC)', rendered['text'])
        self.assertNotIn('Event time (UTC)', rendered['text'])
