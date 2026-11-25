"""Datadog records retain source identity, timestamp roles and metric semantics."""
import json
import math
import re
from datetime import datetime, timedelta, timezone
from starlette.exceptions import HTTPException
from . import db
from .imports import redact, redact_object, timestamp
from .sentry_imports import source_scrub
from .evidence_contracts import digest

VERSION = 1


def parameters(data, connection):
    if set(data) - {'title', 'symptom', 'datasets', 'start', 'end', 'query', 'metric_queries'}:
        raise HTTPException(400, 'Unsupported Datadog import fields.')
    datasets = data.get('datasets', [])
    if not isinstance(datasets, list) or not datasets or len(datasets) > 4 or any(not isinstance(d, str) or d not in {'logs', 'spans', 'metrics', 'monitors'} for d in datasets):
        raise HTTPException(400, 'Choose logs, spans, metrics or monitors.')
    start, end = timestamp(data.get('start')), timestamp(data.get('end'))
    if not start or not end or not timedelta(0) < datetime.fromisoformat(end)-datetime.fromisoformat(start) <= timedelta(hours=24):
        raise HTTPException(400, 'Choose a positive time window of at most 24 hours.')
    if datetime.fromisoformat(end) > datetime.now(timezone.utc) + timedelta(minutes=1):
        raise HTTPException(400, 'Import window cannot end in the future.')
    title, symptom, query = data.get('title', 'Datadog evidence'), data.get('symptom', ''), data.get('query', '*')
    if any(not isinstance(v, str) for v in (title, symptom, query)) or not 1 <= len(title.strip()) <= 120 or len(symptom) > 2000 or not 1 <= len(query) <= 1000:
        raise HTTPException(400, 'Invalid title, symptom or query length.')
    metrics = data.get('metric_queries', [])
    if not isinstance(metrics, list) or len(metrics) > 5 or any(not isinstance(q, str) or not 1 <= len(q) <= 1000 for q in metrics):
        raise HTTPException(400, 'Use at most five bounded metric queries.')
    if 'metrics' in datasets and not metrics:
        raise HTTPException(400, 'Select a metric query before importing metrics.')
    return {'projects': [], 'datasets': sorted(set(datasets)), 'start': start, 'end': end,
            'title': redact(title.strip())[0], 'symptom': redact(symptom)[0], 'query': query, 'metric_queries': metrics}


def normalize(source, dataset, project, connection):
    if not isinstance(source, dict):
        raise ValueError('Invalid Datadog observation.')
    cleaned, changes = redact_object(source_scrub(source))
    attrs = cleaned.get('attributes', {})
    if not isinstance(attrs, dict):
        raise ValueError('Invalid Datadog attributes.')
    custom = attrs.get('attributes', {})
    custom = custom if isinstance(custom, dict) else {}
    tags = {}
    for tag in attrs.get('tags', cleaned.get('tags', cleaned.get('tag_set', []))) or []:
        if isinstance(tag, str) and ':' in tag:
            key, value = tag.split(':', 1); tags[key] = value
    service = attrs.get('service') or custom.get('service') or tags.get('service') or 'unknown'
    if not isinstance(service, str) or not 1 <= len(service) <= 80:
        service = 'unknown'
    row = {'id': db.uid(), 'kind': {'spans': 'span', 'metrics': 'metric'}.get(dataset, 'log'),
           'service': service, 'timestamp': timestamp(attrs.get('timestamp', attrs.get('start_timestamp'))),
           'source_id': str(cleaned.get('id') or digest(cleaned)), 'source_provider': 'datadog',
           'source_connection_id': connection['id'], 'source_dataset': dataset,
           'source_organization': connection['organization'], 'source_site': connection['region'],
           'normalization_version': 'datadog-v1', 'environment': tags.get('env', custom.get('env')),
           'release': tags.get('version', custom.get('version')), 'ingested_at': db.now(),
           'original': json.dumps(cleaned, ensure_ascii=False), 'source_truncated': False,
           'level': str(attrs.get('status', 'unknown')).lower(),
           'message': str(attrs.get('message', cleaned.get('name', '')))}
    for key in ('trace_id', 'span_id', 'parent_id', 'request_id', 'instance'):
        value = attrs.get(key, custom.get(key))
        if value is not None and isinstance(value, (str, int)) and not isinstance(value, bool):
            row[key] = str(value)
    if row.get('trace_id'):
        value = row['trace_id']
        row['trace_id_format'] = 'hex128' if len(value) == 32 else 'decimal64' if value.isdecimal() else 'opaque'
    if row.get('span_id'):
        row['span_id_format'] = 'decimal64'
    propagated = custom.get('w3c.trace_id') or attrs.get('w3c.trace_id')
    if isinstance(propagated, str) and re.fullmatch(r'[0-9a-fA-F]{32}', propagated) and int(propagated, 16):
        row['propagation_trace_id'] = propagated.lower()
    if dataset == 'spans':
        row.update(name=str(attrs.get('resource_name', attrs.get('operation_name', row['message']))),
                   span_op=attrs.get('operation_name'),
                   status=attrs.get('status') if attrs.get('status') in ('error', 'ok') else 'unknown')
        end = timestamp(attrs.get('end_timestamp'))
        if end and row['timestamp']:
            duration = (datetime.fromisoformat(end)-datetime.fromisoformat(row['timestamp'])).total_seconds()*1000
            if duration >= 0:
                row['duration_ms'] = duration
        row['span_id_format'] = 'decimal64'
    if dataset == 'metrics':
        point = cleaned.get('point')
        if not isinstance(point, list) or len(point) != 2 or type(point[1]) not in (int, float) or not math.isfinite(point[1]):
            raise ValueError('Metric point must contain a finite value.')
        row.update(timestamp=timestamp(point[0]), metric_name=cleaned.get('metric'), value=point[1],
                   unit=cleaned.get('unit'), dimensions=tags, interval_ms=cleaned.get('interval'),
                   aggregation=cleaned.get('expression'), query=cleaned.get('query'), message=str(cleaned.get('metric', 'Metric point')))
    if dataset == 'monitors':
        row.update(timestamp=row['ingested_at'], timestamp_role='retrieved_configuration', record_type='monitor_configuration',
                   monitor_id=str(cleaned.get('id', '')), monitor_state=cleaned.get('overall_state'),
                   monitor_query=cleaned.get('query'), message=str(cleaned.get('name', 'Monitor configuration')))
    row['provenance'] = {'content_digest': digest(cleaned), 'provider_record_id': row['source_id'],
                         'redactions': changes, 'retrieval': 'authenticated_api'}
    return row, changes, False
