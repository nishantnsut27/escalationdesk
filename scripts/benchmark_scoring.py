"""Offline metrics from bound human reviews and retained evidence IDs."""
import math


def citations(report):
    refs = list(report.get('evidence_ids', []))
    for field in ('hypotheses', 'supporting_evidence'):
        for item in report.get(field, []):
            refs.extend(item.get('evidence_ids', []))
    return set(refs)


def score(attempt, review, truth):
    if attempt.get('state') != 'completed':
        return {**attempt, 'state': 'failed', 'review': None, 'metrics': {'citation_validity': None, 'evidence_precision': None}}
    if not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
        raise ValueError('An identified human reviewer is required for semantic scores.')
    labels = ('mechanism_correct', 'service_correct', 'abstention_correct')
    if any(type(review.get(k)) is not bool for k in labels):
        raise ValueError('Complete every semantic and abstention label; unknown is not a pass.')
    refs = citations(attempt.get('report', {}))
    supports = review.get('citation_support')
    if not isinstance(supports, dict) or set(supports) != refs or any(type(v) is not bool for v in supports.values()):
        raise ValueError('Review the semantic support of every cited evidence ID.')
    relevant = truth.get('relevant_evidence_ids')
    if not isinstance(relevant, list) or any(not isinstance(v, str) for v in relevant):
        raise ValueError('Ground truth requires an explicit list of relevant evidence IDs.')
    relevant = set(relevant)
    supplied = set(attempt.get('usage', {}).get('supplied_evidence_ids', []))
    supplied_all = {e['evidence_id'] for request in attempt.get('usage', {}).get('supplied_excerpts', []) for e in request.get('excerpts', [])}
    retrieved = {r['id'] for call in attempt.get('activity', []) for r in call.get('result', {}).get('records', [])}
    return {**attempt, 'review': {**review, 'truth': truth}, 'metrics': {
        'citation_validity': len(refs & supplied)/len(refs) if refs else None,
        'evidence_precision': sum(supports.values())/len(refs) if refs else None,
        'retrieved_evidence_recall': len(relevant & retrieved)/len(relevant) if relevant else None,
        'supplied_evidence_recall': len(relevant & supplied_all)/len(relevant) if relevant else None}}


def numeric_summary(values):
    values = sorted(v for v in values if type(v) in (int, float) and math.isfinite(v))
    if not values:
        return {'measured': 0, 'mean': None, 'p50': None, 'p95': None}
    return {'measured': len(values), 'mean': sum(values)/len(values),
        'p50': values[(len(values)-1)//2], 'p95': values[math.ceil(.95*len(values))-1]}


def usage_tokens(attempt, field):
    diagnostics = attempt.get('usage', {}).get('diagnostics', [])
    values = [d.get('actual_usage', {}).get(field) for d in diagnostics]
    return sum(values) if values and all(type(v) is int and v >= 0 for v in values) else None
