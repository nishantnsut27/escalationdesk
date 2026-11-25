import unittest
from backend.evidence_graph import build
from backend.evidence_contracts import canonical_id
from backend.evidence_quality import assess
from backend.evidence import model_observation


def span(identifier, connection='a', parent=None, **extra):
    return {'id': identifier, 'kind': 'span', 'service': 'payment', 'trace_id': 'trace',
            'span_id': identifier, 'source_connection_id': connection, 'parent_id': parent,
            'timestamp': '2026-11-10T10:00:00Z', **extra}


class RelationshipTests(unittest.TestCase):
    def test_full_cross_source_identity_survives_model_projection_and_edge_limit(self):
        trace = 'a' * 32
        sentry = span('s', source_provider='sentry', environment='prod', trace_id=trace, trace_id_format='hex128', propagation_trace_id=trace)
        datadog = span('d', connection='b', source_provider='datadog', environment='prod', trace_id=trace, trace_id_format='hex128', propagation_trace_id=trace)
        for records in ([sentry, datadog], [model_observation(sentry), model_observation(datadog)]):
            graph = build(records, maximum=1)
            self.assertEqual(len(graph['edges']), 1)
            self.assertEqual(graph['edges'][0]['relationship'], 'shared_cross_source_trace')
            self.assertEqual(graph['omitted_edges'], 0)

    def test_propagated_identity_requires_full_width_and_compatible_environment(self):
        sentry = span('s', source_provider='sentry', environment='production', trace_id='a'*32, trace_id_format='hex128')
        datadog = span('d', connection='b', source_provider='datadog', environment='prod', trace_id='123', trace_id_format='decimal64', propagation_trace_id='a'*32)
        cross = lambda graph: [e for e in graph['edges'] if e['relationship'] == 'shared_cross_source_trace']
        self.assertFalse(cross(build([sentry, datadog])))
        mapping = {'environments': {'sentry:production': 'prod'}}
        self.assertEqual(len(cross(build([sentry, datadog], mappings=mapping))), 1)
        for invalid in (None, 'a'*16, '0'*32):
            self.assertFalse(cross(build([sentry, {**datadog, 'propagation_trace_id': invalid}], mappings=mapping)))

    def test_service_alias_is_context_only_and_does_not_rewrite_sources(self):
        sentry = span('s', source_provider='sentry', service='payments-api', environment='prod')
        datadog = span('d', connection='b', source_provider='datadog', service='pay', environment='prod')
        graph = build([sentry, datadog], mappings={'services': {'sentry:payments-api': 'payment', 'datadog:pay': 'payment'}})
        self.assertEqual(len(graph['edges']), 1)
        self.assertEqual(graph['edges'][0]['relationship'], 'mapped_service_context')
        self.assertEqual(graph['edges'][0]['support'], 'context')
        self.assertEqual(sentry['service'], 'payments-api')

    def test_duplicate_parent_identity_is_ambiguous_not_overwritten(self):
        graph = build([span('root'), span('other', span_id='root'), span('child', parent='root')])
        self.assertFalse(any(e['relationship'] == 'parent_span' for e in graph['edges']))
        self.assertEqual(graph['ambiguities'][0]['state'], 'ambiguous_parent')

    def test_same_ids_across_connections_do_not_link(self):
        graph = build([span('root'), span('child', connection='b', parent='root')])
        self.assertFalse(any(e['relationship'] in {'parent_span', 'shared_trace'} for e in graph['edges']))

    def test_known_parent_is_justified_and_time_is_only_context(self):
        graph = build([span('root'), span('child', parent='root')])
        self.assertTrue(any(e['relationship'] == 'parent_span' and e['justification']['parent_span_id'] == 'root' for e in graph['edges']))
        self.assertTrue(all(e['support'] == 'context' for e in graph['edges'] if e['relationship'] == 'temporal_order'))

    def test_id_width_is_never_guessed(self):
        self.assertNotEqual(canonical_id('1', 'decimal64'), canonical_id('0' * 31 + '1', 'hex128'))
        self.assertEqual(canonical_id('1', 'decimal64'), canonical_id('0' * 15 + '1', 'hex64'))
        self.assertIsNone(canonical_id(str(2 ** 64), 'decimal64'))

    def test_environment_and_edge_budgets_are_enforced(self):
        graph = build([span('root', environment='prod'), span('child', parent='root', environment='test')])
        self.assertFalse(any(e['relationship'] == 'parent_span' for e in graph['edges']))
        limited = build([span(str(i)) for i in range(50)], maximum=5)
        self.assertEqual(len(limited['edges']), 5)
        self.assertGreater(limited['omitted_edges'], 0)

    def test_quality_retains_conflicts_and_unknown_time(self):
        records = [{'id': 'a', 'source_id': 'same', 'message': 'failed', 'timestamp': None},
                   {'id': 'b', 'source_id': 'same', 'message': 'passed', 'timestamp': None}]
        quality = assess(records)
        self.assertEqual(quality['a']['conflicting_ids'], ['b'])
        self.assertEqual(quality['a']['timestamp'], 'unknown')
