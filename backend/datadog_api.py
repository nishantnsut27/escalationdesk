"""Owned Datadog connections. Credentials are write-only and never serialized."""
import asyncio
import json
import re
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route
from . import auth, config, db
from .connection_security import seal
from .datadog_client import SITES
from .datadog_imports import parameters
from .providers import ProviderError
from .sentry_imports import create_import, public_import
from .sentry_api import cancel_jobs, public_connection


def user(request, write=False):
    owner = auth.current(request)
    if auth.is_demo(owner):
        raise HTTPException(403, 'Demo Account cannot manage connections.')
    if write:
        auth.require_verified(owner)
        auth.csrf(request)
        if not config.ENABLE_DATADOG:
            raise HTTPException(409, 'Datadog is not enabled on this server.')
    return owner


def connection(request, write=False):
    owner = user(request, write)
    row = db.one("SELECT * FROM sentry_connections WHERE id=%s AND user_id=%s AND provider='datadog'", (request.path_params['id'], owner['id']))
    if not row:
        raise HTTPException(404, 'Datadog connection not found.')
    return row


def public(row):
    return {**public_connection(row), 'provider': row.get('provider', 'sentry')}


def listing(request):
    owner = user(request)
    return JSONResponse({'datadog_enabled': config.ENABLE_DATADOG,
        'connections': [public(r) for r in db.all_rows("SELECT * FROM sentry_connections WHERE user_id=%s AND state<>'disconnected' ORDER BY created_at DESC", (owner['id'],))],
        'imports': [public_import(r) for r in db.all_rows('SELECT * FROM sentry_imports WHERE user_id=%s ORDER BY created_at DESC LIMIT 50', (owner['id'],))]})


async def create(request):
    owner = user(request, True)
    data = await auth.body(request)
    if set(data) != {'site', 'name', 'api_key', 'application_key'} or not isinstance(data['site'], str) or data['site'] not in SITES:
        raise HTTPException(400, 'Select a supported Datadog site and enter both keys.')
    if not isinstance(data['name'], str) or not 1 <= len(data['name'].strip()) <= 120:
        raise HTTPException(400, 'Use a connection name of 1-120 characters.')
    if not all(isinstance(data[k], str) and re.fullmatch(r'[A-Za-z0-9_-]{16,128}', data[k]) for k in ('api_key', 'application_key')):
        raise HTTPException(400, 'Invalid Datadog credential format.')
    row = {'id': db.uid(), 'user_id': owner['id'], 'region': data['site'], 'organization': data['name'].strip(),
           'generation': 0, 'provider': 'datadog'}
    try:
        row['credentials'] = seal({k: data[k] for k in ('api_key', 'application_key')})
        capabilities = await request.app.state.datadog.validate_connection(row)
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from None
    stamp = db.now()
    db.execute("INSERT INTO sentry_connections(id,user_id,organization,region,credentials,state,created_at,checked_at,capabilities,provider) VALUES(%s,%s,%s,%s,%s,'connected',%s,%s,%s,'datadog')",
               (row['id'], owner['id'], row['organization'], row['region'], row['credentials'], stamp, stamp, json.dumps(capabilities)))
    return JSONResponse(public(db.one('SELECT * FROM sentry_connections WHERE id=%s', (row['id'],))), status_code=201)


async def check(request):
    row = connection(request, True)
    if row['state'] == 'disconnected':
        raise HTTPException(409, 'Reconnect Datadog first.')
    try:
        capabilities = await request.app.state.datadog.validate_connection(row)
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from None
    db.execute("UPDATE sentry_connections SET capabilities=%s,checked_at=%s,error=NULL WHERE id=%s AND generation=%s AND state<>'disconnected'",
               (json.dumps(capabilities), db.now(), row['id'], row['generation']))
    return JSONResponse(public(connection(request)))


async def disconnect(request):
    row = connection(request, True)
    with db.connection() as conn:
        db.lock(conn)
        conn.execute("UPDATE sentry_connections SET credentials=NULL,state='disconnected',generation=generation+1 WHERE id=%s AND user_id=%s", (row['id'], row['user_id']))
        conn.execute('UPDATE sentry_automation SET enabled=FALSE WHERE connection_id=%s', (row['id'],))
        cancel_jobs(conn, row['id'])
    return JSONResponse({'ok': True})


async def import_start(request):
    row = connection(request, True)
    options = parameters(await auth.body(request), row)
    job, created = await asyncio.to_thread(create_import, row['user_id'], row, options)
    request.app.state.sentry_importer.wake()
    return JSONResponse(public_import(job), status_code=202 if created else 200)


def owned_import(request, write=False):
    owner = user(request, write)
    row = db.one('SELECT * FROM sentry_imports WHERE id=%s AND user_id=%s', (request.path_params['id'], owner['id']))
    if not row:
        raise HTTPException(404, 'Import not found.')
    return row


def import_detail(request):
    return JSONResponse(public_import(owned_import(request)))


async def import_retry(request):
    job = owned_import(request, True)
    row = db.one('SELECT * FROM sentry_connections WHERE id=%s AND user_id=%s', (job['connection_id'], job['user_id']))
    if job['state'] in {'queued', 'running', 'rate_limited'}:
        raise HTTPException(409, 'This import is still active.')
    result, _ = await asyncio.to_thread(create_import, job['user_id'], row, json.loads(job['parameters']), job['id'])
    request.app.state.sentry_importer.wake()
    return JSONResponse(public_import(result), status_code=202)


async def import_cancel(request):
    job = owned_import(request, True)
    db.execute("UPDATE sentry_imports SET state='cancelled',completed_at=%s,error='Cancelled by owner; retained evidence remains available.' WHERE id=%s AND user_id=%s AND state IN ('queued','running','rate_limited')", (db.now(), job['id'], job['user_id']))
    return JSONResponse({'ok': True})


routes = [Route('/api/connections', listing), Route('/api/connections/datadog', create, methods=['POST']),
          Route('/api/connections/datadog/{id}/check', check, methods=['POST']),
          Route('/api/connections/datadog/{id}', disconnect, methods=['DELETE']),
          Route('/api/connections/datadog/{id}/imports', import_start, methods=['POST']),
          Route('/api/provider-imports/{id}', import_detail),
          Route('/api/provider-imports/{id}/retry', import_retry, methods=['POST']),
          Route('/api/provider-imports/{id}/cancel', import_cancel, methods=['POST'])]
