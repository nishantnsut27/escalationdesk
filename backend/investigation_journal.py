"""Append-only execution records. Never retains credentials or reasoning fields."""
import json
from . import db
from .evidence_contracts import digest

VERSION = 'execution-v1'
PRIVATE = {'reasoning', 'reasoning_content', 'analysis', 'authorization', 'api_key', 'application_key', 'credentials'}


def public_value(value):
    if isinstance(value, dict):
        return {k: public_value(v) for k, v in value.items() if k.lower() not in PRIVATE}
    if isinstance(value, list):
        return [public_value(v) for v in value]
    return value


class Journal:
    def __init__(self, identifier, owner):
        self.identifier, self.owner = identifier, owner

    def event(self, kind, payload):
        payload = public_value(payload)
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 262144:
            raise ValueError('Execution event exceeds the retention bound.')
        with db.connection() as conn:
            parent = conn.execute('SELECT id FROM investigations WHERE id=%s AND user_id=%s FOR UPDATE', (self.identifier, self.owner)).fetchone()
            if not parent:
                raise ValueError('Investigation is unavailable.')
            sequence = conn.execute('SELECT COALESCE(MAX(sequence),0)+1 n FROM investigation_events WHERE investigation_id=%s', (self.identifier,)).fetchone()['n']
            conn.execute('INSERT INTO investigation_events(investigation_id,user_id,sequence,kind,payload,digest) VALUES(%s,%s,%s,%s,%s::jsonb,%s)',
                         (self.identifier, self.owner, sequence, kind, encoded, digest(payload)))

    def checkpoint(self, state):
        db.execute('UPDATE investigations SET execution_state=%s::jsonb WHERE id=%s AND user_id=%s',
                   (json.dumps(public_value(state), allow_nan=False), self.identifier, self.owner))

    def manifest(self, value):
        value = public_value(value)
        with db.connection() as conn:
            changed = conn.execute('UPDATE investigations SET execution_manifest=%s::jsonb WHERE id=%s AND user_id=%s AND execution_manifest IS NULL RETURNING id',
                       (json.dumps(value, allow_nan=False), self.identifier, self.owner)).fetchone()
            if changed:
                sequence = conn.execute('SELECT COALESCE(MAX(sequence),0)+1 n FROM investigation_events WHERE investigation_id=%s', (self.identifier,)).fetchone()['n']
                payload = {'digest': digest(value), 'version': VERSION}
                conn.execute('INSERT INTO investigation_events(investigation_id,user_id,sequence,kind,payload,digest) VALUES(%s,%s,%s,%s,%s::jsonb,%s)',
                    (self.identifier, self.owner, sequence, 'manifest_created', json.dumps(payload), digest(payload)))


def events(identifier, owner, after=0, limit=100):
    return db.all_rows('SELECT sequence,kind,created_at::text,payload,digest FROM investigation_events '
                      'WHERE investigation_id=%s AND user_id=%s AND sequence>%s ORDER BY sequence LIMIT %s',
                      (identifier, owner, after, min(limit, 100)))
