import unittest
from scripts.evaluate_investigations import citations, summarize, query_snapshot
from scripts.benchmark_scoring import score, usage_tokens


class BenchmarkTests(unittest.TestCase):
    def test_scoring_failure_preserves_experiment_identity_and_usage(self):
        attempt = {'arm': 'C', 'case': 'pool', 'state': 'failed', 'error': 'capacity unavailable',
                   'duration_ms': 1500, 'usage': {'requests': 1}}
        scored = score(attempt, {}, {})
        self.assertEqual(scored['arm'], 'C')
        self.assertEqual(scored['usage'], attempt['usage'])
        self.assertEqual(scored['duration_ms'], 1500)
        self.assertEqual(scored['error'], attempt['error'])
        self.assertEqual(summarize([scored])['failed'], 1)

    def test_new_findings_are_included_in_citation_review(self):
        self.assertEqual(citations({'evidence_ids': ['a'], 'hypotheses': [{'evidence_ids': ['b']}], 'supporting_evidence': [{'evidence_ids': ['c']}]}), {'a','b','c'})

    def test_failures_and_unreviewed_results_are_not_dropped(self):
        value = summarize([{'state': 'failed'}, {'state': 'completed'}, {'state': 'completed', 'review': {'mechanism_correct': False}}])
        self.assertEqual(value['attempts'], 3)
        self.assertEqual(value['failed'], 1)
        self.assertEqual(value['unreviewed'], 1)
        self.assertEqual(value['mechanism_correct']['rate'], 0)
        self.assertIsNone(value['service_correct']['rate'])

    def test_scores_require_complete_source_bound_semantic_review(self):
        attempt = {'state': 'completed', 'report': {'evidence_ids': ['a', 'b']}, 'usage': {'supplied_evidence_ids': ['a', 'b'],
            'supplied_excerpts': [{'excerpts': [{'evidence_id': 'a'}, {'evidence_id': 'b'}]}]},
            'activity': [{'result': {'records': [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}]}}]}
        review = {'reviewer': 'fixture-reviewer', 'mechanism_correct': False, 'service_correct': True,
            'abstention_correct': False, 'citation_support': {'a': True, 'b': False}}
        scored = score(attempt, review, {'relevant_evidence_ids': ['a', 'c']})
        self.assertEqual(scored['metrics']['evidence_precision'], .5)
        self.assertEqual(scored['metrics']['retrieved_evidence_recall'], 1)
        self.assertEqual(scored['metrics']['supplied_evidence_recall'], .5)
        with self.assertRaises(ValueError):
            score(attempt, {**review, 'citation_support': {'a': True}}, {'relevant_evidence_ids': ['a']})
        self.assertIsNone(usage_tokens(attempt, 'prompt_tokens'))

    def test_snapshot_queries_compare_instants_and_filter_signatures(self):
        rows = [{'id': 'a', 'kind': 'log', 'service': 'api', 'message': 'No evidence', 'timestamp': '2026-11-10T11:00:00+01:00'},
            {'id': 'b', 'kind': 'log', 'service': 'api', 'message': 'No evidence', 'timestamp': None}]
        self.assertEqual([r['id'] for r in query_snapshot(rows, start_time='2026-11-10T09:59:00Z', end_time='2026-11-10T10:01:00Z')], ['a'])
        self.assertEqual(query_snapshot(rows, error_type='out_of_memory'), [])
