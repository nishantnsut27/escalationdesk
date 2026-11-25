"""Datadog cycle events share the existing incident/allowance/notification path."""
import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route
from . import auth, config, db
from .datadog_api import connection
from .imports import redact, timestamp
from .evidence_contracts import digest
from .providers import ProviderError
from .evidence_scope import validate_mappings


def parse_event(value):
    if not isinstance(value, dict):
        raise ValueError('Expected a monitor notification object.')
    monitor, cycle, scope = str(value.get('monitor_id', '')), value.get('cycle_key'), value.get('scope')
    if not re.fullmatch(r'[0-9]{1,30}', monitor) or not isinstance(cycle, str) or not 1 <= len(cycle) <= 128:
        raise ValueError('Monitor and cycle identifiers are required.')
    if not isinstance(scope, str) or len(scope) > 1000:
        raise ValueError('A monitor scope is required; use an empty string for an ungrouped monitor.')
    scope = ','.join(sorted(set(part.strip() for part in scope.split(',') if part.strip())))
    action = str(value.get('transition', '')).lower().replace('-', '').replace(' ', '')
    action = {'rewarn': 'warn', 'renodata': 'nodata'}.get(action, action)
    if action not in {'triggered', 'retriggered', 'recovered', 'warn', 'nodata', 'renotify'}:
        raise ValueError('Unknown monitor transition.')
    raw_time = value.get('timestamp')
    try:
        at = timestamp(int(raw_time))
    except (TypeError, ValueError):
        at = None
    if not at or datetime.fromisoformat(at) > datetime.now(timezone.utc)+timedelta(minutes=1):
        raise ValueError('A valid provider event timestamp is required.')
    return {'monitor_id': monitor, 'cycle_key': cycle, 'scope': scope, 'transition': action,
            'timestamp': at, 'resource_key': monitor + ':' + digest(scope),
            'title': redact(str(value.get('title') or 'Datadog monitor alert'))[0][:120]}


def public_settings(row):
    return {'enabled': row['enabled'], 'configuration': row.get('configuration') or {},
            'initial_email': row['initial_email'], 'result_email': row['result_email'],
            'webhook_configured': bool(row.get('webhook_digest'))}


def get_settings(request):
    row = connection(request)
    saved = db.one('SELECT * FROM sentry_automation WHERE connection_id=%s AND user_id=%s', (row['id'], row['user_id']))
    return JSONResponse({**public_settings(saved or {'enabled': False, 'initial_email': True, 'result_email': True}),
        'webhook_url': config.API_URL + '/api/connections/datadog/' + row['id'] + '/webhook'})


async def put_settings(request):
    row = connection(request, True)
    data = await auth.body(request)
    required = {'enabled', 'initial_email', 'result_email', 'monitor_ids'}
    if not required <= set(data) or set(data) - required - {'enrichment', 'metric_queries', 'mappings'} or any(type(data[k]) is not bool for k in ('enabled', 'initial_email', 'result_email')):
        raise HTTPException(400, 'Invalid monitor automation settings.')
    try:
        mappings = validate_mappings(data.get('mappings'))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    identifiers = data['monitor_ids']
    if not isinstance(identifiers, list) or len(identifiers) > 20 or any(not isinstance(v, str) or not re.fullmatch(r'[0-9]{1,30}', v) for v in identifiers) or data['enabled'] and not identifiers:
        raise HTTPException(400, 'Select one to twenty monitor IDs to enable automation.')
    enrichment = data.get('enrichment')
    if enrichment is not None:
        if not isinstance(enrichment, dict) or set(enrichment) != {'connection_id', 'project_ids'} or not isinstance(enrichment.get('connection_id'), str):
            raise HTTPException(400, 'Select an owned Sentry connection and projects for enrichment.')
        source = db.one("SELECT * FROM sentry_connections WHERE id=%s AND user_id=%s AND provider='sentry' AND state='connected'", (enrichment['connection_id'], row['user_id']))
        projects = enrichment['project_ids']
        if not source or not isinstance(projects, list) or not 1 <= len(projects) <= 5 or any(not isinstance(p, str) or p not in {v['id'] for v in json.loads(source['projects'])} for p in projects):
            raise HTTPException(400, 'Enrichment projects are unavailable to this account.')
    metrics = data.get('metric_queries', [])
    if not isinstance(metrics, list) or len(metrics) > 5 or any(not isinstance(q, str) or not 1 <= len(q) <= 1000 for q in metrics):
        raise HTTPException(400, 'Use at most five metric queries.')
    for monitor in identifiers if data['enabled'] else []:
        try:
            detail = await request.app.state.datadog.request(row, 'GET', '/api/v1/monitor/' + monitor)
            if not isinstance(detail, dict) or str(detail.get('id')) != monitor:
                raise ProviderError('Monitor identity mismatch.')
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from None
    secret = None
    with db.connection() as conn:
        db.lock(conn)
        current = conn.execute('SELECT state,generation FROM sentry_connections WHERE id=%s AND user_id=%s', (row['id'], row['user_id'])).fetchone()
        if current['state'] != 'connected' or current['generation'] != row['generation']:
            raise HTTPException(409, 'Connection changed while checking monitors.')
        saved = conn.execute('SELECT webhook_digest FROM sentry_automation WHERE connection_id=%s', (row['id'],)).fetchone()
        if not saved or not saved['webhook_digest']:
            secret = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO sentry_automation(connection_id,user_id,enabled,initial_email,result_email,updated_at,configuration,webhook_digest) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT(connection_id) DO UPDATE SET enabled=excluded.enabled,initial_email=excluded.initial_email,result_email=excluded.result_email,updated_at=excluded.updated_at,configuration=excluded.configuration,webhook_digest=COALESCE(sentry_automation.webhook_digest,excluded.webhook_digest)",
                     (row['id'], row['user_id'], data['enabled'], data['initial_email'], data['result_email'], db.now(), json.dumps({'monitor_ids': sorted(set(identifiers)), 'enrichment': enrichment, 'metric_queries': metrics, 'mappings': mappings}), auth.digest(secret) if secret else None))
    response = public_settings(db.one('SELECT * FROM sentry_automation WHERE connection_id=%s', (row['id'],)))
    if secret:
        response['webhook_secret'] = secret
    response['webhook_url'] = config.API_URL + '/api/connections/datadog/' + row['id'] + '/webhook'
    return JSONResponse(response)


async def webhook(request):
    if not config.ENABLE_DATADOG:
        raise HTTPException(404, 'Webhook unavailable.')
    row = db.one("SELECT c.*,a.webhook_digest FROM sentry_connections c JOIN sentry_automation a ON a.connection_id=c.id WHERE c.id=%s AND c.provider='datadog' AND c.state='connected'", (request.path_params['id'],))
    token = request.headers.get('x-tracedesk-webhook-token', '')
    if not row or not row['webhook_digest'] or len(token) > 256 or not hmac.compare_digest(auth.digest(token), row['webhook_digest']):
        raise HTTPException(401, 'Invalid webhook authentication.')
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 65536:
            raise HTTPException(413, 'Webhook body exceeds 64 KiB.')
    try:
        event = parse_event(json.loads(raw))
    except (ValueError, TypeError):
        raise HTTPException(400, 'Invalid monitor event identity, transition, scope or timestamp.') from None
    body_digest = hashlib.sha256(row['id'].encode()+bytes(raw)).hexdigest()
    state = 'expired' if time.time()-datetime.fromisoformat(event['timestamp']).timestamp() > 86400 else 'pending'
    db.execute('INSERT INTO automatic_deliveries(id,body_digest,connection_id,user_id,action,received_at,payload,state,next_attempt,resource_id,resource_key,cycle_key) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(body_digest) DO NOTHING',
               (db.uid(), body_digest, row['id'], row['user_id'], event['transition'], time.time(), json.dumps(event), state, time.time(), event['monitor_id'], event['resource_key'], event['cycle_key']))
    return JSONResponse({'accepted': True}, status_code=202)


async def reconcile(worker, connection_row, delivery):
    from .automation import eligible, queue_reason, queue_notification
    event = json.loads(delivery['payload'])
    try:
        detail = await worker.importer.datadog.request(connection_row, 'GET', '/api/v1/monitor/' + event['monitor_id'])
        if not isinstance(detail, dict) or str(detail.get('id')) != event['monitor_id']:
            raise ProviderError('Monitor identity mismatch.', 403)
    except ProviderError as exc:
        state = 'expired' if time.time() >= delivery['received_at']+86400 else 'pending'
        db.execute('UPDATE automatic_deliveries SET state=%s,attempts=attempts+1,next_attempt=%s,error=%s WHERE id=%s',
                   (state, time.time()+min(300, max(5, exc.retry_after)), str(exc), delivery['id']))
        return
    with db.connection() as conn:
        db.lock(conn)
        connection_row = dict(conn.execute('SELECT * FROM sentry_connections WHERE id=%s', (connection_row['id'],)).fetchone())
        existing = conn.execute('SELECT * FROM automatic_incidents WHERE connection_id=%s AND resource_key=%s AND cycle_key=%s',
                                (connection_row['id'], event['resource_key'], event['cycle_key'])).fetchone()
        recovering = event['transition'] == 'recovered'
        creates = event['transition'] in {'triggered', 'retriggered'}
        identifier = existing['id'] if existing else None
        if not existing and (recovering or creates) and eligible(conn, connection_row, event['monitor_id']):
            identifier, stamp = db.uid(), delivery['received_at']
            reason = queue_reason(conn, connection_row['user_id'], time.time())
            state = 'recovered' if recovering else 'deferred' if reason else 'queued'
            observation = {'message': event['title'], 'timestamp': event['timestamp'], 'level': 'error',
                           'source_provider': 'datadog', 'source_dataset': 'monitors', 'record_type': 'monitor_alert',
                           'source_connection_id': connection_row['id'], 'monitor_id': event['monitor_id'],
                           'scope': event['scope'], 'source_note': 'Observed monitor notification, not proof of a root cause.'}
            conn.execute('INSERT INTO automatic_incidents(id,user_id,connection_id,project_name,title,episode,trigger,triggered_at,received_at,resume_until,collect_after,lifecycle,state,observation,resource_id,resource_key,cycle_key) VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                         (identifier, connection_row['user_id'], connection_row['id'], 'Datadog monitor '+event['monitor_id'], event['title'], event['transition'], event['timestamp'], stamp, stamp+86400, stamp+60, 'resolved' if recovering else 'open', state, json.dumps(observation), event['monitor_id'], event['resource_key'], event['cycle_key']))
            plan = conn.execute('SELECT configuration FROM sentry_automation WHERE connection_id=%s', (connection_row['id'],)).fetchone()['configuration']
            conn.execute('UPDATE automatic_incidents SET collection_plan=%s::jsonb WHERE id=%s', (json.dumps(plan), identifier))
            if not recovering:
                attempt_id = db.uid()
                conn.execute('INSERT INTO automatic_attempts(id,incident_id,user_id,number,state,created_at,resume_until) VALUES(%s,%s,%s,1,%s,%s,%s)', (attempt_id, identifier, connection_row['user_id'], state, db.now(), stamp+86400))
                episode = dict(conn.execute('SELECT * FROM automatic_incidents WHERE id=%s', (identifier,)).fetchone())
                attempt = dict(conn.execute('SELECT * FROM automatic_attempts WHERE id=%s', (attempt_id,)).fetchone())
                queue_notification(conn, episode, attempt, alert=True)
        if existing and recovering:
            conn.execute("UPDATE automatic_incidents SET lifecycle='resolved' WHERE id=%s", (identifier,))
            for attempt in conn.execute("SELECT * FROM automatic_attempts WHERE incident_id=%s AND started_at IS NULL AND state IN ('queued','importing','waiting','deferred')", (identifier,)).fetchall():
                conn.execute("UPDATE automatic_attempts SET state='interrupted',completed_at=%s,error='Monitor recovered before model admission.' WHERE id=%s", (db.now(), attempt['id']))
                if attempt['investigation_id']:
                    conn.execute("UPDATE investigations SET state='interrupted',completed_at=%s,error='Monitor recovered before admission.' WHERE id=%s AND state='queued'", (db.now(), attempt['investigation_id']))
                if attempt['import_id']:
                    conn.execute("UPDATE sentry_imports SET state='cancelled',completed_at=%s WHERE (id=%s OR id IN (SELECT import_id FROM automatic_attempt_imports WHERE attempt_id=%s)) AND state IN ('queued','running','rate_limited')", (db.now(), attempt['import_id'], attempt['id']))
                conn.execute("UPDATE automatic_incidents SET state='recovered' WHERE id=%s", (identifier,))
        conn.execute("UPDATE automatic_deliveries SET state='accepted',incident_id=%s,error=NULL WHERE id=%s", (identifier, delivery['id']))


def import_options(episode, connection_row):
    from .datadog_imports import parameters
    observation = json.loads(episode['observation'])
    tags = dict(part.split(':', 1) for part in observation.get('scope', '').split(',') if ':' in part)
    service, environment = tags.get('service'), tags.get('env')
    safe = re.compile(r'[A-Za-z0-9_.:/-]{1,80}\Z')
    if service and safe.fullmatch(service):
        query = 'service:' + service
        if environment and safe.fullmatch(environment):
            query += ' env:' + environment
    else:
        query = '*'
    end = min(datetime.fromisoformat(episode['triggered_at'])+timedelta(seconds=60), datetime.now(timezone.utc))
    metrics = (episode.get('collection_plan') or {}).get('metric_queries', [])
    options = parameters({'title': episode['title'], 'symptom': 'Datadog reported: '+episode['title'],
                       'datasets': ['logs', 'spans'] + (['metrics'] if metrics else []), 'metric_queries': metrics,
                       'start': (end-timedelta(minutes=15)).isoformat(), 'end': end.isoformat(), 'query': query}, connection_row)
    options['_source_service'] = service if service and safe.fullmatch(service) else None
    options['_source_environment'] = environment if environment and safe.fullmatch(environment) else None
    if not options['_source_service']:
        options['_skip_telemetry'] = 'Monitor notification has no usable service scope. No broad telemetry query was issued.'
    return options


routes = [Route('/api/connections/datadog/{id}/automation', get_settings),
          Route('/api/connections/datadog/{id}/automation', put_settings, methods=['PUT']),
          Route('/api/connections/datadog/{id}/webhook', webhook, methods=['POST'])]
