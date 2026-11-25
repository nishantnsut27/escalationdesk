"""Owned composite snapshots and recorded execution inspection."""
import asyncio
import json
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route
from . import auth, db
from .evidence_scope import create_composite, rows
from .evidence_graph import build
from .investigation_journal import events
from .investigation_replay import inspect


async def composite(request):
    user = auth.current(request)
    auth.require_verified(user)
    auth.csrf(request)
    data = await auth.body(request)
    if set(data) - {'collection_ids', 'title', 'symptom', 'mappings'}:
        raise HTTPException(400, 'Unsupported composite snapshot fields.')
    try:
        identifier = await asyncio.to_thread(create_composite, user['id'], data.get('collection_ids'), data.get('title'), data.get('symptom', ''), mappings=data.get('mappings'))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return JSONResponse({'id': identifier}, status_code=201)


def execution(request):
    from .api import owned
    row = owned(request, 'investigations', request.path_params['id'])
    return JSONResponse({'version': 'execution-v1', 'investigation_id': row['id'],
        'manifest': row.get('execution_manifest'), 'state': row.get('execution_state'),
        'recorded': bool(row.get('execution_manifest')), 'note': 'Recorded execution; viewing makes no provider calls.'})


def execution_events(request):
    from .api import owned
    row = owned(request, 'investigations', request.path_params['id'])
    try:
        after = int(request.query_params.get('after', 0))
        if after < 0:
            raise ValueError()
    except ValueError:
        raise HTTPException(400, 'Use a nonnegative event sequence.') from None
    found = events(row['id'], row['user_id'], after)
    return JSONResponse({'events': found, 'next_sequence': found[-1]['sequence'] if len(found) == 100 else None})


def replay(request):
    from .api import owned
    row = owned(request, 'investigations', request.path_params['id'])
    export = request.query_params.get('export') == '1'
    result = inspect(row, include_sources=export)
    headers = {'Cache-Control': 'no-store'}
    if export:
        headers['Content-Disposition'] = 'attachment; filename="investigation-recording.json"'
    return JSONResponse(result, headers=headers)


def relationship_graph(request):
    from .api import owned
    row = owned(request, 'collections', request.path_params['id'])
    evidence = [db.public_evidence(r) for r in rows(row['user_id'], collection_id=row['id'])]
    return JSONResponse(build(evidence, mappings=json.loads(row['summary']).get('mappings')))


routes = [Route('/api/collections/composite', composite, methods=['POST']),
          Route('/api/collections/{id}/relationships', relationship_graph),
          Route('/api/investigations/{id}/execution', execution),
          Route('/api/investigations/{id}/events', execution_events),
          Route('/api/investigations/{id}/replay', replay)]
