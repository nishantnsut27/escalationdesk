"""Validate public diagnostic claims, not private model reasoning."""
from copy import deepcopy
import json
from .evidence import TOOL_NAMES, PROPERTIES, parse_time

STATUSES = ('open', 'supported', 'weakened', 'rejected', 'unresolved')
SUPPORT = ('insufficient', 'tentative', 'corroborated', 'directly_observed')
TEXT = {'type': 'string'}
REFS = {'type': 'array', 'items': TEXT, 'maxItems': 4}
HYPOTHESIS = {'type': 'object', 'additionalProperties': False, 'properties': {
    'id': TEXT, 'description': TEXT, 'supporting_evidence_ids': REFS,
    'contradicting_evidence_ids': REFS, 'missing_evidence': {'type': 'array', 'items': TEXT, 'maxItems': 2},
    'status': {'type': 'string', 'enum': list(STATUSES)},
    'support_level': {'type': 'string', 'enum': list(SUPPORT)}, 'transition_reason': TEXT}}
HYPOTHESIS['required'] = list(HYPOTHESIS['properties'])
HYPOTHESIS_UPDATE = deepcopy(HYPOTHESIS)
HYPOTHESIS_UPDATE['properties'].pop('description')
HYPOTHESIS_UPDATE['required'].remove('description')


def planning_schema():
    actions = []
    for name in TOOL_NAMES:
        properties = deepcopy(PROPERTIES[name])
        for field in properties.values():
            field.pop('enum', None) if field.get('enum') == ['gateway', 'order', 'payment'] else None
        # Optional tool arguments are encoded in a JSON string to avoid a large
        # five-way strict-schema union consuming the evidence input allowance.
        actions.append(name)
    action = {'type': 'object', 'additionalProperties': False,
              'properties': {'tool': {'type': 'string', 'enum': actions}, 'arguments': TEXT,
                             'hypothesis_id': TEXT, 'purpose': TEXT},
              'required': ['tool', 'arguments', 'hypothesis_id', 'purpose']}
    return {'type': 'object', 'additionalProperties': False, 'properties': {
        'hypotheses': {'type': 'array', 'items': HYPOTHESIS, 'maxItems': 3},
        'actions': {'type': 'array', 'items': action, 'maxItems': 2}, 'stop_reason': TEXT},
        'required': ['hypotheses', 'actions', 'stop_reason']}


def validate_hypotheses(items, supplied, previous=()):
    if not isinstance(items, list) or len(items) > 3:
        raise ValueError('Use at most three hypotheses.')
    identifiers = set()
    previous = {h['id']: h for h in previous}
    for h in items:
        if not isinstance(h, dict) or set(h) != set(HYPOTHESIS['required']):
            raise ValueError('Invalid hypothesis fields.')
        for field, bound in (('id', 24), ('description', 240), ('transition_reason', 240)):
            if not isinstance(h[field], str) or not 1 <= len(h[field]) <= bound:
                raise ValueError('Invalid hypothesis text.')
        if h['id'] in identifiers or h['status'] not in STATUSES or h['support_level'] not in SUPPORT:
            raise ValueError('Invalid hypothesis identity or support label.')
        identifiers.add(h['id'])
        old = previous.get(h['id'])
        if old and h['description'] != old['description']:
            raise ValueError('Keep hypothesis identities stable; a different claim needs a different ID.')
        for field in ('supporting_evidence_ids', 'contradicting_evidence_ids'):
            if (not isinstance(h[field], list) or len(h[field]) > 4
                    or any(not isinstance(ref, str) or ref not in supplied for ref in h[field])
                    or len(set(h[field])) != len(h[field])):
                raise ValueError('Hypothesis cites evidence not supplied in this request.')
        if set(h['supporting_evidence_ids']) & set(h['contradicting_evidence_ids']):
            raise ValueError('The same observation cannot support and contradict the same claim.')
        if old and not set(old['contradicting_evidence_ids']) <= set(h['contradicting_evidence_ids']):
            raise ValueError('Retain contrary evidence; unresolved observations cannot silently disappear.')
        if h['status'] == 'supported' and (not h['supporting_evidence_ids'] or h['contradicting_evidence_ids']):
            raise ValueError(f"Resolve material contradictions before marking a hypothesis supported: {h['id']}.")
        if h['support_level'] in {'corroborated', 'directly_observed'} and not h['supporting_evidence_ids']:
            raise ValueError('Evidence is required for this support level.')
        if h['status'] == 'rejected' and not h['contradicting_evidence_ids']:
            raise ValueError(f"Rejecting a hypothesis requires contrary evidence: {h['id']}. Without it use unresolved or weakened, never invent a citation.")
        if (not isinstance(h['missing_evidence'], list) or len(h['missing_evidence']) > 2
                or any(not isinstance(v, str) or not 1 <= len(v) <= 180 for v in h['missing_evidence'])):
            raise ValueError('Invalid missing evidence.')
    if not set(previous) <= identifiers:
        raise ValueError('Retain previously considered hypotheses, including rejected and unresolved claims.')
    return deepcopy(items)


def projection(items, maximum=2048):
    result = [{'id': h['id'], 'description': h['description'], 'status': h['status'],
               'contradicting_evidence_ids': h['contradicting_evidence_ids']} for h in items]
    if len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()) > maximum:
        raise ValueError('Hypothesis state exceeds its input allowance; immutable claims and contrary citations cannot be truncated.')
    return result


def apply_updates(items, supplied, previous):
    """Final assessment updates claims by ID; the backend retains their wording."""
    if not isinstance(items, list):
        raise ValueError('Expected hypothesis updates.')
    known = {h['id']: h for h in previous}
    merged = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or item['id'] not in known:
            raise ValueError('Final updates require an existing hypothesis ID.')
        description = known[item['id']]['description']
        if 'description' in item and item['description'] != description:
            raise ValueError('Final updates cannot replace an existing claim.')
        merged.append({**item, 'description': description})
    return validate_hypotheses(merged, supplied, previous)


def canonical_query(name, arguments, *, services, scope):
    if name not in TOOL_NAMES or not isinstance(arguments, dict) or set(arguments) - set(PROPERTIES[name]):
        raise ValueError('Unknown tool or unsupported arguments.')
    args = dict(arguments)
    if name != 'get_topology':
        args.setdefault('limit', 20)
        args.setdefault('offset', 0)
    for key in ('start_time', 'end_time'):
        if key in args:
            args[key] = parse_time(args[key])
    if args.get('start_time', '') > args.get('end_time', '~'):
        raise ValueError('Invalid time window.')
    if 'service' in args and args['service'] not in services:
        raise ValueError('Unknown service.')
    if name == 'get_trace' and not args.get('trace_id'):
        raise ValueError('A recorded trace ID is required.')
    for field, high in (('limit', 50), ('offset', 5000)):
        if field in args and (type(args[field]) is not int or not (1 if field == 'limit' else 0) <= args[field] <= high):
            raise ValueError('Invalid query bound.')
    return name + ':' + json.dumps([scope, args], sort_keys=True, separators=(',', ':')), args
