"""Platform PostgreSQL storage. Reference workloads keep their own SQLite stores."""
import json
import os
import uuid
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from .config import DATA
from . import postgres
from .postgres_migrations import migrate

Error = postgres.psycopg.Error
IntegrityError = postgres.psycopg.IntegrityError
_target = None
_schema = None
_request_connection = ContextVar("tracedesk_request_connection", default=None)
_request_timing = ContextVar("tracedesk_request_timing", default=None)

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return uuid.uuid4().hex

def target():
    if _target:
        return _target
    if os.environ.get("DESK_TEST_SCHEMA"):
        return postgres.test_target()
    return os.environ.get("DESK_DATABASE_URL", "")

def schema():
    return _schema or os.environ.get("DESK_TEST_SCHEMA", "public")

def connection():
    return postgres.connection(target(),schema())

@contextmanager
def request_connection():
    existing = _request_connection.get()
    if existing is not None:
        yield existing
        return
    with connection() as conn:
        token = _request_connection.set(conn)
        try:
            yield conn
        finally:
            _request_connection.reset(token)

def begin_request_timing():
    return _request_timing.set({"db": 0.0})

def end_request_timing(token):
    metrics = _request_timing.get() or {"db": 0.0}
    _request_timing.reset(token)
    return metrics

@contextmanager
def _timed_database():
    started = time.perf_counter()
    try:
        yield
    finally:
        metrics = _request_timing.get()
        if metrics is not None:
            metrics["db"] += (time.perf_counter() - started) * 1000

lock = postgres.lock
close = postgres.close

def init():
    DATA.mkdir(parents=True,exist_ok=True)
    with connection() as conn:
        if schema()=="public" and (DATA/"platform.sqlite3").exists():
            transferred=conn.execute("SELECT to_regclass('storage_transfers') AS name").fetchone()["name"]
            if not transferred or not conn.execute("SELECT 1 FROM storage_transfers LIMIT 1").fetchone():
                raise ValueError("Migrate the retained SQLite backup before starting PostgreSQL. Automatic fallback is disabled.")
        migrate(conn)
        automatic_schema = conn.execute("SELECT to_regclass('automatic_attempts') AS name").fetchone()['name'] is not None
        unstarted = " AND NOT (state='queued' AND EXISTS (SELECT 1 FROM automatic_attempts a WHERE a.investigation_id=investigations.id AND a.started_at IS NULL AND a.state IN ('queued','deferred')))" if automatic_schema else ""
        for row in conn.execute("SELECT c.id,c.summary FROM collections c JOIN sentry_imports s ON s.collection_id=c.id WHERE s.state IN ('queued','running','rate_limited')").fetchall():
            summary=json.loads(row["summary"])
            summary["coverage"]="Server restarted during Sentry import. Retained evidence is partial; retry creates a new snapshot."
            conn.execute("UPDATE collections SET summary=%s WHERE id=%s",(json.dumps(summary),row["id"]))
        conn.execute("UPDATE sentry_imports SET state='interrupted',completed_at=%s,error='Server restarted. Retry to create a new snapshot.' WHERE state IN ('queued','running','rate_limited')",(now(),))
        for row in conn.execute("SELECT id,usage FROM investigations WHERE state IN ('queued','running','quota_waiting')" + unstarted).fetchall():
            usage=json.loads(row["usage"])
            triage=usage.get("triage")
            if triage and triage.get("state") in {"pending","running"}:
                triage.update(state="interrupted",completed_at=now(),reason="Server restarted before triage finished.")
                conn.execute("UPDATE investigations SET usage=%s WHERE id=%s",(json.dumps(usage),row["id"]))
        conn.execute("UPDATE supplements SET state='interrupted',completed_at=%s,error='Server restarted. Retry further analysis to create a new retained attempt.' WHERE state IN ('queued','running','quota_waiting')",(now(),))
        conn.execute("UPDATE investigations SET state='interrupted',completed_at=%s,error='Server restarted. Start a new investigation.' WHERE state IN ('queued','running','quota_waiting')" + unstarted, (now(),))
        if automatic_schema:
            conn.execute("UPDATE automatic_attempts a SET state=CASE WHEN i.report::jsonb->>'assessment'='inconclusive' THEN 'inconclusive' ELSE 'completed' END,completed_at=i.completed_at,error=NULL FROM investigations i WHERE a.investigation_id=i.id AND i.state='completed' AND a.state IN ('investigating','waiting','queued')")
            conn.execute("UPDATE automatic_incidents i SET state=a.state,completed_at=a.completed_at,error=a.error FROM automatic_attempts a WHERE a.incident_id=i.id AND a.number=(SELECT MAX(number) FROM automatic_attempts WHERE incident_id=i.id) AND a.state IN ('completed','inconclusive') AND i.state IN ('investigating','waiting','queued')")
            conn.execute("UPDATE automatic_attempts SET state='interrupted',completed_at=%s,error='Server restarted after investigation started. Retry explicitly; previous spending is retained.' WHERE started_at IS NOT NULL AND state IN ('investigating','waiting','queued')", (now(),))
            conn.execute("UPDATE automatic_incidents i SET state='interrupted',error='Server restarted after investigation started. Retry explicitly; previous spending is retained.',completed_at=%s WHERE EXISTS (SELECT 1 FROM automatic_attempts a WHERE a.incident_id=i.id AND a.state='interrupted' AND a.number=(SELECT MAX(number) FROM automatic_attempts WHERE incident_id=i.id)) AND i.state IN ('investigating','waiting','queued')", (now(),))
        conn.execute("UPDATE runs SET state='interrupted',ended_at=%s WHERE state IN ('running','starting','resetting')",(now(),))
        conn.execute("UPDATE incidents SET state='interrupted' WHERE state='open'")

def one(statement,args=()):
    conn = _request_connection.get()
    if conn is None:
        with request_connection():
            return one(statement,args)
    with _timed_database():
        row=conn.execute(statement,args).fetchone()
    return dict(row) if row else None

def all_rows(statement,args=()):
    conn = _request_connection.get()
    if conn is None:
        with request_connection():
            return all_rows(statement,args)
    with _timed_database():
        rows = conn.execute(statement,args).fetchall()
    return [dict(row) for row in rows]

def execute(statement,args=()):
    conn = _request_connection.get()
    if conn is None:
        with request_connection():
            return execute(statement,args)
    with _timed_database():
        conn.execute(statement,args)

def read_many(statements):
    """Pipeline independent SELECTs on the current request connection."""
    conn = _request_connection.get()
    if conn is None:
        with request_connection():
            return read_many(statements)
    with _timed_database():
        cursors = []
        with conn.pipeline():
            for statement,args,mode in statements:
                cursors.append((conn.execute(statement,args),mode))
        results = []
        for cursor,mode in cursors:
            if mode == "one":
                row = cursor.fetchone()
                results.append(dict(row) if row else None)
            else:
                results.append([dict(row) for row in cursor.fetchall()])
        return results

def investigation_scope(investigation):
    collection_id = investigation.get("collection_id")
    if collection_id:
        return None, collection_id
    incident = one("SELECT run_id FROM incidents WHERE id=%s AND user_id=%s",
                   (investigation["incident_id"], investigation["user_id"]))
    if not incident:
        raise ValueError("Investigation evidence scope is unavailable.")
    return incident["run_id"], None


def public_evidence(row):
    from .signatures import detect, VERSION
    payload = json.loads(row["payload"])
    level = str(payload.get("level", "unknown")).lower()
    level = {"warn": "warning", "fatal": "error", "critical": "error"}.get(level, level)
    if level not in {"error", "warning", "info", "debug", "trace"}:
        level = "unknown"
    if payload.get("parent_span_id") and not payload.get("parent_id"):
        payload = {**payload, "parent_id": payload["parent_span_id"]}
    return {**payload, "error_tags": detect(payload), "signature_version": VERSION,
            "collection_id": row.get("collection_id"), "run_id": row.get("run_id"),
            "level": level,
            **{key: row[key] for key in ("id", "kind", "service", "timestamp") }}


def evidence(run_id, owner, kind=None, service=None, limit=100, trace_id=None,
             start_time=None, end_time=None, *, collection_id=None, offset=0, query=None, level=None, request_id=None, error_type=None, source_ids=None):
    field, scope = ("collection_id", collection_id) if collection_id else ("run_id", run_id)
    statement = f"SELECT e.* FROM evidence e JOIN evidence_index i ON i.evidence_id=e.id WHERE e.{field}=%s AND e.user_id=%s"
    args = [scope, owner]
    if source_ids is not None:
        statement += " AND e.id=ANY(%s)"
        args.append(list(source_ids))
    for key,value in (("kind",kind),("service",service)):
        if value:
            statement += f" AND e.{key}=%s"
            args.append(value)
    for key,value in (("trace_id",trace_id),("request_id",request_id),("level",level)):
        if value:
            statement += f" AND i.{key}=%s"
            args.append(value)
    if query:
        statement += " AND strpos(lower(COALESCE(i.search_text,'')),lower(%s))>0"
        args.append(query)
    if error_type:
        from .signatures import VERSION
        statement += " AND EXISTS (SELECT 1 FROM evidence_error_tags t WHERE t.evidence_id=e.id AND t.version=%s AND t.signature=%s)"
        args.extend((VERSION, error_type))
    for value,operator in ((start_time,">="),(end_time,"<=")):
        if value:
            statement += f" AND i.observed_at{operator}%s::timestamptz"
            args.append(value)
    rows=all_rows(statement+" ORDER BY i.observed_at NULLS LAST,i.source_line,e.id LIMIT %s OFFSET %s",
                  (*args,min(limit,201),offset))
    return [public_evidence(row) for row in rows]
