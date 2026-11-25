"""Bounded, justified evidence relationships. Temporal edges are context only."""
from collections import defaultdict
from datetime import datetime
from .evidence_contracts import canonical_id, digest, namespace, span_key, timestamp, trace_key

VERSION = 'evidence-relationships-v1'
MAX_EDGES = 2000


def build(rows, *, maximum=MAX_EDGES, mappings=None):
    rows = sorted(rows, key=lambda r: (r.get('timestamp') or '', r['id']))
    edges, spans, groups, ambiguous = [], defaultdict(list), defaultdict(list), []
    cross_source, service_context = defaultdict(dict), defaultdict(list)
    seen = set()
    omitted = 0

    def add(a, b, relation, fields, support='established', directed=False):
        nonlocal omitted
        if a['id'] == b['id']:
            return
        key = (a['id'], b['id']) if directed else tuple(sorted((a['id'], b['id'])))
        key = (*key, relation)
        if key in seen:
            return
        seen.add(key)
        if len(edges) >= maximum:
            omitted += 1
            return
        edge = {'source': a['id'], 'target': b['id'], 'relationship': relation,
                'justification': fields, 'support': support, 'directed': directed, 'rule': VERSION}
        edge['id'] = digest(edge)[:24]
        edges.append(edge)

    for row in rows:
        provider = row.get('source_provider')
        environment = row.get('environment')
        environment = (mappings or {}).get('environments', {}).get(str(provider)+':'+str(environment), environment)
        if provider in {'sentry', 'datadog'} and environment and (row.get('source_connection_id') or row.get('correlation_namespace')):
            propagated = canonical_id(row.get('propagation_trace_id'), 'hex128')
            native = canonical_id(row.get('trace_id'), 'hex128') if row.get('trace_id_format') == 'hex128' else None
            for identity, field in ((propagated, 'propagation_trace_id'), (native, 'trace_id')):
                if identity:
                    cross_source[(environment, identity)].setdefault(row['id'], (row, field))
            service = (mappings or {}).get('services', {}).get(provider+':'+str(row.get('service')))
            if service:
                service_context[(environment, service)].append(row)
        if row.get('kind') == 'span' and span_key(row):
            spans[span_key(row)].append(row)
        if trace_key(row):
            groups[('shared_trace', trace_key(row))].append(row)
        if row.get('request_id'):
            groups[('shared_request', (*namespace(row), row['request_id']))].append(row)
        if row.get('instance'):
            groups[('shared_instance', (*namespace(row), row.get('service'), row['instance']))].append(row)
        if row.get('release') and row.get('environment') and row.get('instance'):
            groups[('deployment_association', (*namespace(row), row.get('service'), row['instance'], row['release']))].append(row)
    for row in rows:
        parent = span_key(row, parent=True)
        if not parent:
            continue
        candidates = spans.get(parent, [])
        if len(candidates) == 1:
            add(candidates[0], row, 'parent_span', {'trace_id': row['trace_id'],
                'parent_span_id': row.get('parent_span_id', row.get('parent_id'))}, directed=True)
        else:
            ambiguous.append({'evidence_id': row['id'], 'state': 'ambiguous_parent' if candidates else 'missing_parent',
                              'candidate_ids': [r['id'] for r in candidates]})
    # Identity edges precede broad context so the graph limit cannot evict them.
    for (environment, identity), indexed in sorted(cross_source.items()):
        members = list(indexed.values())
        anchors = {row['source_provider']: (row, field) for row, field in reversed(members)}
        for other, target_field in members:
            for provider, (first, source_field) in anchors.items():
                if provider == other['source_provider']:
                    continue
                add(first, other, 'shared_cross_source_trace', {'source_field': source_field, 'target_field': target_field,
                    'full_128_bit_identity': identity, 'environment': environment,
                    'environment_mapping': (mappings or {}).get('environments', {})})
    for (relation, key), members in sorted(groups.items(), key=lambda item: (item[0][0] != 'shared_trace', repr(item[0]))):
        for other in members[1:]:
            add(members[0], other, relation, {'identity': list(key)},
                'context' if relation == 'deployment_association' else 'established')
    for (environment, service), members in sorted(service_context.items()):
        previous_provider = {}
        for row in members:
            at = timestamp(row.get('timestamp'))
            if not at:
                continue
            for provider, other in previous_provider.items():
                if provider != row['source_provider']:
                    gap = abs((datetime.fromisoformat(at)-datetime.fromisoformat(timestamp(other['timestamp']))).total_seconds())
                    if gap <= 30:
                        add(other, row, 'mapped_service_context', {'service': service, 'environment': environment,
                            'service_mapping': (mappings or {}).get('services', {}),
                            'source_time': other['timestamp'], 'target_time': row['timestamp'], 'seconds_apart': gap}, 'context')
            previous_provider[row['source_provider']] = row
    previous = {}
    for row in rows:
        at = timestamp(row.get('timestamp'))
        key = (*namespace(row), row.get('service'))
        if at and key in previous:
            other = previous[key]
            add(other, row, 'temporal_order', {'source_time': other['timestamp'], 'target_time': row['timestamp']}, 'context', True)
        if at:
            previous[key] = row
    return {'version': VERSION, 'edges': edges, 'ambiguities': ambiguous,
            'omitted_edges': omitted, 'note': 'Recorded relationships and time order do not themselves establish causality.'}
