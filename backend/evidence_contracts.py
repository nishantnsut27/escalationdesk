"""Versioned identities and provenance; conversion never guesses trace lineage."""
import hashlib
import json
import re
from datetime import datetime, timezone

VERSION = 'observations-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(timezone.utc).isoformat() if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def canonical_id(value, representation=None):
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    if representation == 'decimal64':
        if not re.fullmatch(r'[0-9]{1,20}', value) or not 0 < int(value) < 2 ** 64:
            return None
        return 'u64:' + str(int(value))
    if representation in {'hex128', 'hex64'}:
        size = 32 if representation == 'hex128' else 16
        if not re.fullmatch('[0-9a-fA-F]{' + str(size) + '}', value) or int(value, 16) == 0:
            return None
        return ('u128:' if size == 32 else 'u64:') + (value.lower() if size == 32 else str(int(value, 16)))
    return 'opaque:' + value


def namespace(row):
    # A trusted normalizer may set a propagation namespace. Matching raw strings
    # from different connections otherwise never establishes a shared request.
    return (row.get('correlation_namespace') or row.get('source_connection_id')
            or row.get('collection_id') or row.get('run_id') or 'local'), row.get('environment')


def trace_key(row):
    identity = canonical_id(row.get('trace_id'), row.get('trace_id_format'))
    return (*namespace(row), identity) if identity else None


def span_key(row, parent=False):
    trace = trace_key(row)
    value = row.get('parent_span_id', row.get('parent_id')) if parent else row.get('span_id')
    identity = canonical_id(value, row.get('span_id_format'))
    return (*trace, identity) if trace and identity else None


def provenance(row, *, provider, connection=None, dataset=None):
    return {'version': VERSION, 'provider': provider, 'connection_id': connection,
            'dataset': dataset, 'record_id': row.get('source_id'),
            'content_digest': digest(row), 'timestamp_known': timestamp(row.get('timestamp')) is not None}
