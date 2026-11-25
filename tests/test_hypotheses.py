import unittest
from copy import deepcopy
from backend.hypotheses import apply_updates, canonical_query, validate_hypotheses, projection
import json
from backend.investigation_journal import public_value


class HypothesisTests(unittest.TestCase):
    def setUp(self):
        self.h = {'id': 'h1', 'description': 'Pool acquisition failed', 'supporting_evidence_ids': ['a'],
                  'contradicting_evidence_ids': [], 'missing_evidence': ['Connection hold duration'],
                  'status': 'supported', 'support_level': 'directly_observed', 'transition_reason': 'Acquisition failure recorded'}

    def test_unknown_and_conflicting_citations_fail(self):
        with self.assertRaises(ValueError):
            validate_hypotheses([self.h], set())
        self.h['contradicting_evidence_ids'] = ['b']
        with self.assertRaises(ValueError):
            validate_hypotheses([self.h], {'a', 'b'})

    def test_final_updates_keep_stored_claims_and_reject_unknown_or_rewritten_ids(self):
        update = {k: v for k, v in self.h.items() if k != 'description'}
        self.assertEqual(apply_updates([update], {'a'}, [self.h]), [self.h])
        self.assertNotIn('description', update)
        for invalid in ({**update, 'id': 'unknown'}, {**update, 'description': 'A different cause'},
                        {**update, 'supporting_evidence_ids': ['unseen']}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                apply_updates([invalid], {'a'}, [self.h])
        with self.assertRaises(ValueError):
            apply_updates([], {'a'}, [self.h])

    def test_probabilities_and_extra_reasoning_are_rejected(self):
        valid = deepcopy(self.h)
        self.assertEqual(validate_hypotheses([valid], {'a'}), [valid])
        self.h['confidence'] = 0.97
        with self.assertRaises(ValueError):
            validate_hypotheses([self.h], {'a'})

    def test_equivalent_queries_deduplicate_after_defaults_and_time_conversion(self):
        first, _ = canonical_query('search_logs', {'start_time': '2026-11-10T10:00:00Z'}, services=set(), scope='a')
        second, _ = canonical_query('search_logs', {'start_time': '2026-11-10T11:00:00+01:00', 'limit': 20, 'offset': 0}, services=set(), scope='a')
        self.assertEqual(first, second)

    def test_journal_discards_reasoning_and_credentials(self):
        self.assertEqual(public_value({'message': {'reasoning': 'private', 'content': 'public'}, 'credentials': 'secret'}),
                         {'message': {'content': 'public'}})

    def test_hypotheses_and_counterevidence_cannot_silently_disappear(self):
        previous = {**self.h, 'status': 'unresolved', 'contradicting_evidence_ids': ['b']}
        with self.assertRaises(ValueError):
            validate_hypotheses([self.h], {'a', 'b'}, [previous])
        with self.assertRaises(ValueError):
            validate_hypotheses([], {'a', 'b'}, [previous])
        self.assertEqual(validate_hypotheses([previous], {'a', 'b'}, [previous]), [previous])

    def test_projection_preserves_claims_and_contrary_ids_or_fails_within_bounds(self):
        compact = projection([self.h])
        self.assertEqual(compact[0]['description'], self.h['description'])
        self.assertEqual(compact[0]['contradicting_evidence_ids'], self.h['contradicting_evidence_ids'])
        items = [{**self.h, 'id': str(i), 'description': '\U0001f50e'*240, 'contradicting_evidence_ids': ['x'*128]*4} for i in range(3)]
        with self.assertRaisesRegex(ValueError, 'cannot be truncated'):
            projection(items)
