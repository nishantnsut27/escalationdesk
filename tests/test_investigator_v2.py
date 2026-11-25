import json
import unittest
from unittest.mock import patch
import httpx
from backend.evidence import EvidenceTools
from backend.investigator import Investigator
from test_provider_contract import REPORT
from test_provider_contract import Clock
from backend.quota import GroqQuota


class Tools(EvidenceTools):
    def __init__(self):
        super().__init__('fixture', 'owner', groq=True)
    def snapshot(self):
        return [{'id': 'source', 'kind': 'log', 'service': 'payment', 'level': 'error', 'message': 'Connection acquisition timed out', 'timestamp': '2026-11-10T10:00:00Z'}]
    def call(self, name, args):
        return {'records': [], 'omitted_groups': 0}


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_request_keeps_mechanism_measurement_and_impact(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from backend.evidence import EvidenceContext, encoded
        from backend.hypotheses import HYPOTHESIS
        from backend.investigator import CONCISE_REPORT_SCHEMA
        stamp = '2026-11-10T10:00:00+00:00'
        rows = [
            {'kind': 'log', 'service': 'payment', 'message': 'Connection acquisition timed out', 'timeout_ms': 1000, 'span_id': '3'*16},
            {'kind': 'metric', 'service': 'payment', 'message': 'Workload metric sample', 'pool_capacity': 2, 'pool_in_use': 2, 'acquisition_failures': 10},
            {'kind': 'log', 'service': 'gateway', 'message': 'Downstream request failed', 'downstream': 'order', 'http_status': 502, 'span_id': '1'*16},
            {'kind': 'log', 'service': 'order', 'message': 'Downstream request failed', 'downstream': 'payment', 'http_status': 503, 'span_id': '2'*16},
            {'kind': 'span', 'service': 'gateway', 'name': 'http.order', 'status': 'error', 'duration_ms': 1030, 'span_id': '1'*16, 'parent_id': None},
            {'kind': 'span', 'service': 'order', 'name': 'http.payment', 'status': 'error', 'duration_ms': 1020, 'span_id': '2'*16, 'parent_id': '1'*16},
            {'kind': 'span', 'service': 'payment', 'name': 'db.acquire', 'status': 'error', 'duration_ms': 1010, 'span_id': '3'*16, 'parent_id': '2'*16},
        ]
        rows.extend({'kind': 'log', 'service': 'catalog', 'level': 'info',
                     'message': f'Synthetic contextual observation {index}: ' + 'detail ' * 25}
                    for index in range(30))
        for index, row in enumerate(rows):
            row.update(id=f'{index:024x}', timestamp=stamp)
            if row['kind'] != 'metric':
                row['trace_id'] = 'a'*32
        originals = deepcopy(rows)
        tools = SimpleNamespace(seen={r['id']: r for r in rows}, selection=None, collection=None)
        context = EvidenceContext(tools)
        context.records = deepcopy(tools.seen)
        context.include_relationships = True
        context.hypothesis_sources = {rows[0]['id'], rows[1]['id']}
        schema = deepcopy(CONCISE_REPORT_SCHEMA)
        schema['properties']['hypothesis_updates'] = {'type': 'array', 'items': HYPOTHESIS, 'maxItems': 3}
        schema['required'].append('hypothesis_updates')
        base = {'response_format': {'type': 'json_schema', 'json_schema': {'schema': schema}}}
        payload, supplied = context.payload(base, 'Evidence and uncertainty. '*95, 'Assess the retained mechanism and impact.')
        self.assertLessEqual(len(encoded(payload)), 10240)
        self.assertTrue({r['id'] for r in rows[:3]} <= supplied)
        self.assertEqual(rows, originals)
        self.assertEqual({e['evidence_id'] for e in tools.last_excerpts}, supplied)

    async def exercise(self, reply, tools=None):
        requests, calls = [], []
        def respond(request):
            body = json.loads(request.content); requests.append(body)
            self.assertLessEqual(len(request.content), 10240)
            value = reply(body, len(requests))
            return value if isinstance(value, httpx.Response) else httpx.Response(200, json={'model': 'fixture', 'usage': {'total_tokens': 10}, 'choices': [{'finish_reason': 'stop', 'message': {'content': value if isinstance(value, str) else json.dumps(value)}}]})
        clock = Clock()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            with patch('backend.investigator.GROQ_KEY', 'fixture'):
                output = await Investigator(http, quota=GroqQuota(clock=clock, sleep=clock.sleep)).groq(tools or Tools(), lambda *a: calls.append(a), {'workflow_version': 'v2'})
        return output, requests, calls

    async def test_malformed_final_json_has_one_bounded_correction(self):
        def reply(body, number):
            if number == 1:
                return {'hypotheses': [], 'actions': [], 'stop_reason': 'Missing evidence.'}
            return '{bad json' if number == 2 else {**REPORT, 'hypothesis_updates': []}
        (_, usage), requests, _ = await self.exercise(reply)
        self.assertEqual(usage['requests'], 3)
        self.assertIn('Correct this validation error', requests[-1]['messages'][1]['content'])

    async def test_invalid_plans_stop_before_reserved_report_attempts(self):
        def reply(body, number):
            return {'unexpected': 'untrusted'} if body['response_format']['json_schema']['name'] == 'diagnostic_plan' else {**REPORT, 'hypothesis_updates': []}
        (_, usage), requests, calls = await self.exercise(reply)
        self.assertEqual(len(usage['plan_errors']), 2)
        self.assertEqual(len(requests), 3)
        self.assertFalse([call for call in calls if call[0] != 'select_evidence_blocks'])

    async def test_duplicate_queries_and_unknown_tools_do_not_execute(self):
        hypothesis = {'id': 'h1', 'description': 'Pool pressure', 'supporting_evidence_ids': ['source'],
            'contradicting_evidence_ids': [], 'missing_evidence': ['Capacity'], 'status': 'open', 'support_level': 'tentative', 'transition_reason': 'Observed timeout'}
        def reply(body, number):
            if body['response_format']['json_schema']['name'] == 'incident_report':
                return {**REPORT, 'hypothesis_updates': [hypothesis]}
            return {'hypotheses': [hypothesis], 'actions': [
                {'tool': 'get_metrics', 'arguments': '{}', 'hypothesis_id': 'h1', 'purpose': 'Capacity'},
                {'tool': 'run_shell', 'arguments': '{}', 'hypothesis_id': 'h1', 'purpose': 'Untrusted operation'}], 'stop_reason': ''}
        (_, usage), requests, calls = await self.exercise(reply)
        self.assertEqual(len([c for c in calls if c[0] == 'get_metrics']), 1)
        self.assertFalse([c for c in calls if c[0] == 'run_shell'])
        self.assertEqual(len(requests), 3)

    async def test_authentication_failure_is_not_retried_as_report_correction(self):
        count = []
        def reply(body, number):
            count.append(number)
            return {'hypotheses': [], 'actions': [], 'stop_reason': ''} if number == 1 else httpx.Response(401)
        with self.assertRaisesRegex(ValueError, 'authentication'):
            await self.exercise(reply)
        self.assertEqual(count, [1, 2])

    async def test_initial_authentication_failure_is_not_retried_as_invalid_plan(self):
        count = []
        def reply(body, number):
            count.append(number)
            return httpx.Response(401)
        with self.assertRaisesRegex(ValueError, 'authentication'):
            await self.exercise(reply)
        self.assertEqual(count, [1])

    async def test_validated_hypotheses_and_report_use_existing_input_budget(self):
        requests = []
        hypothesis = {'id': 'h1', 'description': 'Pool acquisition failed', 'supporting_evidence_ids': ['source'],
                      'contradicting_evidence_ids': [], 'missing_evidence': ['Pool capacity'], 'status': 'open',
                      'support_level': 'tentative', 'transition_reason': 'Observed acquisition failure'}
        def respond(request):
            body = json.loads(request.content); requests.append(body)
            self.assertLessEqual(len(request.content), 10240)
            if len(requests) > 1:
                properties = body['response_format']['json_schema']['schema']['properties']
                self.assertEqual(set(properties['origin']['enum']), {'unknown', 'payment'})
                self.assertEqual(properties['affected_services']['items']['enum'], ['payment'])
            result = {'hypotheses': [hypothesis], 'actions': [], 'stop_reason': 'Missing pool measurements.'} if len(requests) == 1 else {**REPORT, 'hypothesis_updates': [{**hypothesis, 'status': 'unresolved'}]}
            return httpx.Response(200, json={'model': 'fixture', 'usage': {'total_tokens': 10}, 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result)}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            with patch('backend.investigator.GROQ_KEY', 'fixture'):
                report, usage = await Investigator(http).groq(Tools(), lambda *a: None, {'workflow_version': 'v2'})
        self.assertEqual(report['assessment'], 'inconclusive')
        self.assertEqual(usage['requests'], 2)
        self.assertNotIn('plan_errors', usage)
        self.assertEqual(usage['hypotheses'][0]['id'], 'h1')
        self.assertEqual(usage['stage'], 'completed')
