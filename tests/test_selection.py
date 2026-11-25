"""Recorded-source preservation and deterministic selection, without model calls."""
from copy import deepcopy
import json
import unittest

from backend.evidence import model_observation
from backend.imports import parse_logs
from backend.selection import OMISSION, encoded, excerpt, provenance, select_blocks
from backend.sentry_imports import normalize


class SourcePreservationTests(unittest.TestCase):
    def test_repeated_compaction_keeps_original_unicode_ranges(self):
        source = {'id': 'source', 'kind': 'log', 'service': 'billing', 'level': 'error',
                  'message': 'Exception \u754c\U0001f680\n' + 'stack frame \u754c\n' * 5000 + '\nCaused by: NullPointerException',
                  'attributes': {'measurements': list(range(1200)), 'note': 'recorded facts'}}
        original = deepcopy(source)
        first = excerpt(source, 2600)
        second = excerpt(first, 1500)
        self.assertLessEqual(len(encoded(second)), 1500)
        saved = provenance([second], {'source': source})[0]
        for key, metadata in saved['fields'].items():
            full = encoded(source[key]).decode() if isinstance(source[key], (dict, list)) else source[key]
            self.assertEqual(metadata['source_chars'], len(full))
            self.assertEqual(second[key], OMISSION.join(full[a:b] for a, b in metadata['ranges']))
        self.assertEqual(source, original)
        self.assertIn('NullPointerException', second['message'])

    def test_blank_lines_long_records_and_source_line_ranges_are_retained(self):
        content = '2026-11-07T10:00:00Z ERROR Request failed\n\nTraceback (most recent call last):\n'
        content += '  File "billing.py", line 42\n' * 1700 + 'TypeError: missing value'
        rows, _ = parse_logs(content, 'billing')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['original'], content)
        self.assertEqual(rows[0]['source_line'], 1)
        self.assertEqual(rows[0]['source_end_line'], len(content.splitlines()))
        self.assertIn('_excerpt', excerpt(model_observation(rows[0])))

    def test_sentry_keeps_the_entire_redacted_source_and_exception_stack(self):
        frames = [{'filename': f'frame-{i}.py', 'lineNo': i, 'function': 'handler'} for i in range(600)]
        source = {'eventID': 'long-event', 'service.name': 'billing', 'message': 'Failed authorization',
                  'timestamp': '2026-11-07T10:00:00Z', 'extra': {'note': 'x' * 30000, 'api_key': 'private-fixture'},
                  'entries': [{'type': 'exception', 'data': {'values': [{'type': 'TypeError', 'value': 'Missing invoice',
                              'stacktrace': {'frames': frames}}]}}]}
        row, _, truncated = normalize(source, 'errors', {'id': '42', 'slug': 'billing'}, {'id': 'connection', 'organization': 'fixture'})
        retained = json.loads(row['original'])
        self.assertFalse(truncated)
        self.assertEqual(len(retained['extra']['note']), 30000)
        self.assertEqual(len(retained['entries'][0]['data']['values'][0]['stacktrace']['frames']), 600)
        self.assertIn('frame-0.py', row['message'])
        self.assertIn('frame-599.py', row['message'])
        self.assertNotIn('private-fixture', json.dumps(row))


class BlockSelectionTests(unittest.TestCase):
    def test_repeated_observations_keep_latest_time_when_selection_reorders_sources(self):
        from backend.evidence import representatives
        rows = [{'id': 'latest', 'kind': 'log', 'service': 'billing', 'message': 'Recorded failure',
                 'timestamp': '2026-11-10T10:02:00Z'},
                {'id': 'earlier', 'kind': 'log', 'service': 'billing', 'message': 'Recorded failure',
                 'timestamp': '2026-11-10T11:00:00+01:00'}]
        original = deepcopy(rows)
        result = representatives(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['id'], 'latest')
        self.assertEqual(result[0]['observed_until'], rows[0]['timestamp'])
        self.assertEqual(rows, original)

    def test_repetition_retains_operation_identity_and_measurements(self):
        base = {'kind': 'span', 'service': 'billing', 'status': 'error', 'name': 'authorize', 'duration_ms': 40}
        rows = [{**base, 'id': 'first'}, {**base, 'id': 'same'}, {**base, 'id': 'other-operation', 'name': 'capture'},
                {**base, 'id': 'other-duration', 'duration_ms': 400}]
        _, metadata = select_blocks(rows)
        self.assertEqual(metadata['sources']['first']['occurrence_ids'], ['first', 'same'])
        self.assertEqual(metadata['sources']['other-operation']['occurrences'], 1)
        self.assertEqual(metadata['sources']['other-duration']['occurrences'], 1)

    def test_source_time_proximity_never_crosses_service_or_becomes_a_trace(self):
        rows = [{'id': 'failure', 'kind': 'log', 'service': 'billing', 'timestamp': '2026-11-07T10:00:30Z', 'message': 'TypeError'},
                {'id': 'near', 'kind': 'log', 'service': 'billing', 'timestamp': '2026-11-07T10:00:59Z', 'message': 'context'},
                {'id': 'far', 'kind': 'log', 'service': 'billing', 'timestamp': '2026-11-07T10:01:01Z', 'message': 'context'},
                {'id': 'other', 'kind': 'log', 'service': 'catalog', 'timestamp': '2026-11-07T10:00:30Z', 'message': 'context'}]
        chosen, metadata = select_blocks(rows)
        nearby = {key for key, meta in metadata['sources'].items() if any(reason == {'reason': 'same_service_30s_context_only', 'anchor_id': 'failure'} for reason in meta['selection'])}
        self.assertEqual(nearby, {'failure', 'near'})
        self.assertFalse(any('trace_id' in row for row in chosen))

    def test_late_resource_extremes_and_independent_success_survive_dense_errors(self):
        rows = [{'id': str(i), 'kind': 'log', 'service': 'worker', 'level': 'error', 'message': 'OutOfMemoryError', 'trace_id': 'failing-trace'} for i in range(150)]
        rows += [{'id': f'metric-{i}', 'kind': 'metric', 'service': 'worker', 'heap_used_bytes': i * 100} for i in range(30)]
        rows += [{'id': 'successful', 'kind': 'log', 'service': 'worker', 'level': 'info', 'http_status': 200, 'message': 'Request completed', 'trace_id': 'independent-trace'}]
        selected, metadata = select_blocks(rows, maximum=24)
        ids = {row['id'] for row in selected}
        self.assertTrue({'metric-0', 'metric-29', 'successful'} <= ids)
        self.assertGreater(metadata['omitted_observations'], 0)

