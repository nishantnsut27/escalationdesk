"""Owned automatic incident episodes and durable, bounded handoffs.

Webhook acceptance does no external I/O. Only the single Investigator may reserve
an allowance and start model work. Started attempts are never replayed on restart.
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route
from . import auth, config, db, email_service, email_templates
from .imports import redact, timestamp
from .sentry_client import SentryError
from .sentry_imports import create_import, parameters, source_scrub
from .imports import redact_object

ACTIVE = ('queued', 'importing', 'investigating', 'waiting', 'deferred')
TERMINAL = ('completed', 'inconclusive', 'failed', 'interrupted')
WINDOW = 86400
QUEUE_LIMIT = 20
logger = logging.getLogger(__name__)


def regular(request, write=False):
    owner = auth.current(request)
    if auth.is_demo(owner):
        raise HTTPException(403, "Demo Account cannot use automatic investigations.")
    if write:
        auth.require_verified(owner)
        auth.csrf(request)
    return owner


def owned_connection(request, write=False):
    owner = regular(request, write)
    row = db.one("SELECT * FROM sentry_connections WHERE id=%s AND user_id=%s", (request.path_params['id'], owner['id']))
    if not row:
        raise HTTPException(404, "Sentry connection not found.")
    return row


def settings(conn, connection):
    row = conn.execute("SELECT * FROM sentry_automation WHERE connection_id=%s AND user_id=%s", (connection['id'], connection['user_id'])).fetchone()
    return dict(row) if row else dict(connection_id=connection['id'], user_id=connection['user_id'], enabled=False, projects='[]', initial_email=True, result_email=True, updated_at=None)


def public_settings(row):
    return {key: json.loads(value) if key == 'projects' else value for key, value in row.items() if key != 'user_id'}


def eligible(conn, connection, project_id):
    option = settings(conn, connection)
    return (connection['state'] == 'connected' and option['enabled']
        and project_id in json.loads(option['projects'])
        and project_id in {p['id'] for p in json.loads(connection['projects'])}
        and conn.execute("SELECT 1 FROM account_security WHERE user_id=%s AND verified_at IS NOT NULL", (connection['user_id'],)).fetchone()
        and not conn.execute("SELECT 1 FROM account_roles WHERE user_id=%s", (connection['user_id'],)).fetchone())


async def get_settings(request):
    connection = await asyncio.to_thread(owned_connection, request)
    def read():
        with db.connection() as conn:
            return public_settings(settings(conn, connection))
    return JSONResponse(await asyncio.to_thread(read))


async def put_settings(request):
    connection = await asyncio.to_thread(owned_connection, request, True)
    value = await auth.body(request)
    if set(value) != {'enabled', 'projects', 'initial_email', 'result_email'} or any(type(value.get(k)) is not bool for k in ('enabled', 'initial_email', 'result_email')):
        raise HTTPException(400, "Provide enabled, projects, initial_email and result_email preferences.")
    projects = value['projects']
    if not isinstance(projects, list) or len(projects) > 32 or any(not isinstance(p, str) for p in projects):
        raise HTTPException(400, "Choose up to 32 authorized projects.")
    if value['enabled'] and not projects:
        raise HTTPException(400, "Select an authorized project before enabling automation.")
    if value['enabled'] and (not config.GROQ_KEY or not email_service.configured()):
        raise HTTPException(409, "Automatic investigations and notification delivery must be configured before enabling automation.")
    def save():
        with db.connection() as conn:
            db.lock(conn)
            current = conn.execute("SELECT * FROM sentry_connections WHERE id=%s AND user_id=%s", (connection['id'], connection['user_id'])).fetchone()
            if any(p not in {x['id'] for x in json.loads(current['projects'])} for p in projects):
                raise HTTPException(403, "A selected project is not authorized for this connection.")
            if value['enabled'] and current['state'] != 'connected':
                raise HTTPException(409, "Reconnect Sentry before enabling automation.")
            conn.execute("INSERT INTO sentry_automation(connection_id,user_id,enabled,projects,initial_email,result_email,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(connection_id) DO UPDATE SET enabled=excluded.enabled,projects=excluded.projects,initial_email=excluded.initial_email,result_email=excluded.result_email,updated_at=excluded.updated_at",
                (connection['id'], connection['user_id'], value['enabled'], json.dumps(sorted(set(projects))), value['initial_email'], value['result_email'], db.now()))
            return public_settings(settings(conn, connection))
    return JSONResponse(await asyncio.to_thread(save))


def accept(raw, value, headers):
    data = value.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('issue'), dict) or not isinstance(data['issue'].get('project'), dict):
        raise HTTPException(400, "Invalid Sentry issue notification.")
    issue = data['issue']
    action = value.get('action')
    if action not in {'created', 'unresolved', 'resolved', 'archived'}:
        return {'ok': True, 'ignored': True}
    try:
        identifier, project_id = str(issue['id']), str(issue['project']['id'])
        if not re.fullmatch(r'[0-9]{1,32}', identifier) or not re.fullmatch(r'[0-9]{1,32}', project_id):
            raise ValueError()
        cleaned = redact_object(source_scrub(value))[0]
    except (KeyError, TypeError, ValueError, RecursionError):
        raise HTTPException(400, "Invalid Sentry issue notification.") from None
    stamp = time.time()
    with db.connection() as conn:
        db.lock(conn)
        connection = conn.execute("SELECT * FROM sentry_connections WHERE installation_id=%s", (value['installation']['uuid'],)).fetchone()
        if not connection or connection['state'] != 'connected':
            raise HTTPException(403, "This installation is not connected to an account.")
        option = settings(conn, connection)
        if project_id not in json.loads(option['projects']) or project_id not in {p['id'] for p in json.loads(connection['projects'])}:
            raise HTTPException(403, "This project is not authorized for automation.")
        if action in {'created', 'unresolved'} and not eligible(conn, connection, project_id):
            return {'ok': True, 'ignored': True}
        digest = hashlib.sha256(raw).hexdigest()
        request_id = headers.get('request-id', '')[:200] or None
        # The unsigned request ID cannot replace the exact signed-body digest.
        row = conn.execute("INSERT INTO automatic_deliveries(id,body_digest,request_id,connection_id,user_id,issue_id,project_id,action,received_at,payload,next_attempt) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(body_digest) DO NOTHING RETURNING id",
            (db.uid(), digest, request_id, connection['id'], connection['user_id'], identifier, project_id, action, stamp, json.dumps(cleaned), stamp)).fetchone()
    return {'ok': True, 'duplicate': not bool(row)}


def queue_reason(conn, owner, stamp, *, admission=True):
    day = datetime.fromtimestamp(stamp, timezone.utc).date()
    used = conn.execute("SELECT attempts FROM automatic_daily_allowance WHERE user_id=%s AND day=%s", (owner, day)).fetchone()
    if used and used['attempts'] >= 5:
        return "The five-investigation UTC daily allowance is used. Automatic resumption remains available for 24 hours."
    active = conn.execute("SELECT COUNT(*) FROM (SELECT id FROM investigations WHERE user_id=%s AND state IN ('queued','running','quota_waiting') UNION ALL SELECT id FROM supplements WHERE user_id=%s AND state IN ('queued','running','quota_waiting')) jobs", (owner, owner)).fetchone()[0]
    if active >= 2:
        return "Two account jobs are active. This incident will resume when capacity returns."
    total = conn.execute("SELECT COUNT(*) FROM investigations WHERE state IN ('queued','running','quota_waiting')").fetchone()[0]
    imports = conn.execute("SELECT COUNT(*) FROM sentry_imports WHERE state IN ('queued','running','rate_limited')").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM automatic_attempts WHERE state IN ('queued','importing')").fetchone()[0]
    if total >= QUEUE_LIMIT or imports >= QUEUE_LIMIT or admission and pending >= QUEUE_LIMIT:
        return "The bounded queue is full. This incident will resume when capacity returns."
    return None


def notification_allowed(job):
    with db.connection() as conn:
        row = conn.execute("SELECT a.* FROM sentry_automation a JOIN automatic_incidents i ON i.connection_id=a.connection_id AND i.user_id=a.user_id WHERE i.id=%s AND i.user_id=%s", (job['incident_id'], job['user_id'])).fetchone()
        preference = 'initial_email' if job['kind'] == 'incident_alert' else 'result_email'
        # Stopping automation does not suppress outcomes of already running work.
        return bool(row and row[preference] and conn.execute("SELECT 1 FROM account_security WHERE user_id=%s AND verified_at IS NOT NULL", (job['user_id'],)).fetchone())


def notify(conn, episode, attempt, *, alert=False):
    kind = 'incident_alert' if alert else 'incident_result'
    key = 'incident:' + episode['id'] + (':alert' if alert else ':result:' + attempt['id'])
    if conn.execute("SELECT 1 FROM email_jobs WHERE dedupe_key=%s", (key,)).fetchone():
        return
    preference = 'initial_email' if alert else 'result_email'
    option = conn.execute("SELECT * FROM sentry_automation WHERE connection_id=%s AND user_id=%s", (episode['connection_id'], episode['user_id'])).fetchone()
    # Suppressed outcomes are durable too; opting in later cannot replay old mail.
    if not option or not option[preference]:
        conn.execute("INSERT INTO email_jobs(id,user_id,kind,dedupe_key,created_at,expires,state,next_attempt,template_version,incident_id,automatic_attempt_id,last_error) VALUES(%s,%s,%s,%s,%s,%s,'cancelled',%s,%s,%s,%s,'Notification disabled.')", (db.uid(), episode['user_id'], kind, key, time.time(), time.time(), time.time(), email_templates.VERSION, episode['id'], attempt['id']))
        return
    report, coverage = None, ''
    if attempt.get('investigation_id'):
        saved = conn.execute("SELECT report FROM investigations WHERE id=%s AND user_id=%s", (attempt['investigation_id'], episode['user_id'])).fetchone()
        if saved and saved['report']:
            report = json.loads(saved['report'])
    if attempt.get('import_id'):
        saved = conn.execute("SELECT c.summary FROM collections c JOIN sentry_imports s ON s.collection_id=c.id WHERE s.id=%s", (attempt['import_id'],)).fetchone()
        if saved:
            summary = json.loads(saved['summary'])
            coverage = summary.get('coverage', '') + ' ' + ' '.join(k + ': ' + v['state'] for k, v in summary.get('datasets', {}).items())
    rendered = email_templates.incident(episode, attempt, report, alert=alert, coverage=coverage)
    owner = conn.execute("SELECT * FROM users WHERE id=%s", (episode['user_id'],)).fetchone()
    email_service.enqueue(conn, owner, kind, rendered['text'], key, time.time() + 23*3600,
        rendered=rendered, incident_id=episode['id'], attempt_id=attempt['id'])


def finish(conn, episode, attempt, state, error=None):
    conn.execute("UPDATE automatic_attempts SET state=%s,error=%s,completed_at=%s WHERE id=%s", (state, error, db.now(), attempt['id']))
    conn.execute("UPDATE automatic_incidents SET state=%s,error=%s,completed_at=%s WHERE id=%s", (state, error, db.now(), episode['id']))


def start_attempt(investigation_id):
    """Called by the single model worker immediately before its first work."""
    with db.connection() as conn:
        db.lock(conn)
        attempt = conn.execute("SELECT * FROM automatic_attempts WHERE investigation_id=%s", (investigation_id,)).fetchone()
        if not attempt:
            return True
        if attempt['started_at'] or attempt['state'] not in ACTIVE:
            return False
        episode = conn.execute("SELECT * FROM automatic_incidents WHERE id=%s", (attempt['incident_id'],)).fetchone()
        connection = conn.execute("SELECT * FROM sentry_connections WHERE id=%s", (episode['connection_id'],)).fetchone()
        stamp = time.time()
        if not eligible(conn, connection, episode['project_id']) or stamp >= attempt['resume_until']:
            message = "Automatic work was disabled or its 24-hour resumption window expired. Retry explicitly."
            finish(conn, episode, attempt, 'interrupted', message)
            conn.execute("UPDATE investigations SET state='interrupted',error=%s,completed_at=%s WHERE id=%s AND state='queued'", (message, db.now(), investigation_id))
            return False
        day = datetime.fromtimestamp(stamp, timezone.utc).date()
        reserved = conn.execute("INSERT INTO automatic_daily_allowance(user_id,day,attempts) VALUES(%s,%s,1) ON CONFLICT(user_id,day) DO UPDATE SET attempts=automatic_daily_allowance.attempts+1 WHERE automatic_daily_allowance.attempts<5 RETURNING attempts", (episode['user_id'], day)).fetchone()
        if not reserved:
            conn.execute("UPDATE automatic_attempts SET state='deferred',error='Daily allowance used; waiting for the next UTC day.' WHERE id=%s", (attempt['id'],))
            conn.execute("UPDATE automatic_incidents SET state='deferred',error='Daily allowance used; waiting for the next UTC day.' WHERE id=%s", (episode['id'],))
            return False
        conn.execute("UPDATE automatic_attempts SET state='investigating',started_at=%s,allowance_day=%s,error=NULL WHERE id=%s", (db.now(), day, attempt['id']))
        conn.execute("UPDATE automatic_incidents SET state='investigating',error=NULL WHERE id=%s", (episode['id'],))
    return True


def public_incident(conn, row, detail=False):
    result = {k: v for k, v in row.items() if k not in {'user_id', 'observation'}}
    attempts = [dict(a) for a in conn.execute("SELECT * FROM automatic_attempts WHERE incident_id=%s AND user_id=%s ORDER BY number", (row['id'], row['user_id']))]
    result['attempts'] = [{k: v.isoformat() if isinstance(v, datetime) or k == 'allowance_day' and v else v for k, v in a.items() if k != 'user_id'} for a in attempts]
    if attempts:
        result['resume_until'] = attempts[-1]['resume_until']
    result['notifications'] = [dict(a) for a in conn.execute("SELECT id,kind,state,attempts,delivery_status,delivery_at,last_error,automatic_attempt_id,template_version FROM email_jobs WHERE incident_id=%s AND user_id=%s ORDER BY created_at,id", (row['id'], row['user_id']))]
    result['report_id'] = next((a['investigation_id'] for a in reversed(attempts) if a['state'] in {'completed', 'inconclusive'}), None)
    if detail:
        result['observation'] = json.loads(row['observation'])
        result['deliveries'] = [dict(a) for a in conn.execute("SELECT id,action,received_at,state,error FROM automatic_deliveries WHERE incident_id=%s AND user_id=%s ORDER BY received_at,id", (row['id'], row['user_id']))]
    return result


async def listing(request):
    owner = regular(request)
    try:
        offset = int(request.query_params.get('offset', '0'))
        if not 0 <= offset <= 100000:
            raise ValueError()
    except ValueError:
        raise HTTPException(400, "Invalid incident offset.") from None
    def read():
        with db.connection() as conn:
            rows = conn.execute("SELECT * FROM automatic_incidents WHERE user_id=%s ORDER BY triggered_at DESC,id DESC LIMIT 51 OFFSET %s", (owner['id'], offset)).fetchall()
            return {'incidents': [public_incident(conn, row) for row in rows[:50]], 'next_offset': offset+50 if len(rows)>50 else None}
    return JSONResponse(await asyncio.to_thread(read))


def owned_incident(conn, request, owner):
    row = conn.execute("SELECT * FROM automatic_incidents WHERE id=%s AND user_id=%s", (request.path_params['id'], owner['id'])).fetchone()
    if not row:
        raise HTTPException(404, "Automatic incident not found.")
    return dict(row)


async def detail(request):
    owner = regular(request)
    def read():
        with db.connection() as conn:
            return public_incident(conn, owned_incident(conn, request, owner), True)
    return JSONResponse(await asyncio.to_thread(read))


async def retry(request):
    owner = regular(request, True)
    if await auth.body(request):
        raise HTTPException(400, "Retry takes no additional options.")
    def insert():
        with db.connection() as conn:
            db.lock(conn)
            episode = owned_incident(conn, request, owner)
            old = conn.execute("SELECT * FROM automatic_attempts WHERE incident_id=%s ORDER BY number DESC LIMIT 1", (episode['id'],)).fetchone()
            if old['state'] in ACTIVE:
                # Repeated button submissions reuse the retained retry.
                if old['explicit_retry']:
                    return public_incident(conn, episode, True)
                raise HTTPException(409, "This incident already has active work.")
            connection = conn.execute("SELECT * FROM sentry_connections WHERE id=%s", (episode['connection_id'],)).fetchone()
            if not eligible(conn, connection, episode['project_id']):
                raise HTTPException(409, "Reconnect Sentry and enable this project before retrying.")
            stamp = time.time()
            reason = queue_reason(conn, owner['id'], stamp)
            state = 'deferred' if reason else 'queued'
            conn.execute("INSERT INTO automatic_attempts(id,incident_id,user_id,number,explicit_retry,state,created_at,resume_until) VALUES(%s,%s,%s,%s,TRUE,%s,%s,%s)", (db.uid(), episode['id'], owner['id'], old['number']+1, state, db.now(), stamp+WINDOW))
            conn.execute("UPDATE automatic_incidents SET state=%s,error=%s,completed_at=NULL WHERE id=%s", (state, reason, episode['id']))
            episode.update(state=state, error=reason, completed_at=None)
            return public_incident(conn, episode, True)
    result = await asyncio.to_thread(insert)
    return JSONResponse(result, status_code=202)


class AutomationWorker:
    def __init__(self, sentry, importer, investigator):
        self.sentry, self.importer, self.investigator = sentry, importer, investigator
        self.task = None
        self.enqueued = set()

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def loop(self):
        while True:
            try:
                await self.process_deliveries()
                await asyncio.to_thread(self.advance)
                for row in await asyncio.to_thread(db.all_rows, "SELECT i.id FROM investigations i JOIN automatic_attempts a ON a.investigation_id=i.id WHERE i.state='queued' AND a.started_at IS NULL AND a.state='queued'"):
                    if row['id'] not in self.enqueued:
                        self.enqueued.add(row['id'])
                        self.investigator.enqueue(row['id'])
                self.enqueued.intersection_update({r['investigation_id'] for r in await asyncio.to_thread(db.all_rows, "SELECT investigation_id FROM automatic_attempts WHERE state IN ('queued','deferred','investigating','waiting') AND investigation_id IS NOT NULL")})
                self.importer.wake()
            except Exception:
                logger.warning("Automatic investigation handoff unavailable; retained work will be checked again.")
            await asyncio.sleep(1)

    async def process_deliveries(self):
        # Bounded reconciliation; outages retain accepted deliveries for retry.
        rows = await asyncio.to_thread(db.all_rows, "SELECT * FROM automatic_deliveries WHERE state='pending' AND next_attempt<=%s ORDER BY received_at,id LIMIT 10", (time.time(),))
        for delivery in rows:
            if delivery['action'] == 'created':
                # Queue the immediate observed alert before external evidence or
                # reconciliation I/O; the webhook only persists the delivery.
                await asyncio.to_thread(self.prepare_created, delivery)
            connection = await asyncio.to_thread(db.one, "SELECT * FROM sentry_connections WHERE id=%s", (delivery['connection_id'],))
            try:
                if connection['state'] != 'connected':
                    await asyncio.to_thread(db.execute, "UPDATE automatic_deliveries SET state='ignored',error='Connection disconnected before reconciliation.' WHERE id=%s", (delivery['id'],))
                    continue
                issue, _ = await self.sentry.get(connection, f"/api/0/organizations/{connection['organization']}/issues/{delivery['issue_id']}/")
                if not isinstance(issue, dict) or str(issue.get('id')) != delivery['issue_id'] or str((issue.get('project') or {}).get('id')) != delivery['project_id']:
                    raise SentryError("Issue identity or project could not be reconciled.", 403)
                if delivery['action'] != 'created' and 'activity' not in issue:
                    activities, _ = await self.sentry.get(connection, f"/api/0/organizations/{connection['organization']}/issues/{delivery['issue_id']}/activities/", {'per_page': 100})
                    if not isinstance(activities, list):
                        raise SentryError("Issue transition history could not be reconciled.")
                    issue['activity'] = activities
                await asyncio.to_thread(self.reconcile, delivery, issue)
            except SentryError:
                stamp = time.time()
                if stamp >= delivery['received_at'] + WINDOW:
                    # Persist the incident before marking the expired handoff.
                    await asyncio.to_thread(self.expire_delivery, delivery)
                else:
                    await asyncio.to_thread(db.execute, "UPDATE automatic_deliveries SET attempts=attempts+1,next_attempt=%s,error='Current Sentry issue could not be reconciled; retrying within 24 hours.' WHERE id=%s AND state='pending'", (stamp+min(300, 5*2**min(delivery['attempts'],6)), delivery['id']))

    def prepare_created(self, delivery):
        issue = json.loads(delivery['payload'])['data']['issue']
        if issue.get('issueCategory') != 'error':
            return
        with db.connection() as conn:
            db.lock(conn)
            connection = conn.execute("SELECT * FROM sentry_connections WHERE id=%s", (delivery['connection_id'],)).fetchone()
            if not eligible(conn, connection, delivery['project_id']):
                return
            current = conn.execute("SELECT * FROM automatic_issues WHERE connection_id=%s AND issue_id=%s", (connection['id'], delivery['issue_id'])).fetchone()
            if not current or current['episode'] == 0:
                identifier = self.create_episode(conn, delivery, issue, connection, 1, 'created')
                conn.execute("INSERT INTO automatic_issues(connection_id,issue_id,user_id,project_id,lifecycle,episode) VALUES(%s,%s,%s,%s,'open',1) ON CONFLICT(connection_id,issue_id) DO UPDATE SET episode=1,lifecycle='open'", (connection['id'], delivery['issue_id'], connection['user_id'], delivery['project_id']))
            else:
                episode = conn.execute("SELECT id FROM automatic_incidents WHERE connection_id=%s AND issue_id=%s AND episode=%s", (connection['id'], delivery['issue_id'], current['episode'])).fetchone()
                identifier = episode['id'] if episode else None
            conn.execute("UPDATE automatic_deliveries SET incident_id=%s WHERE id=%s", (identifier, delivery['id']))

    def reconcile(self, delivery, issue):
        original = json.loads(delivery['payload'])['data']['issue']
        lifecycle = {'ignored': 'archived', 'resolved': 'resolved', 'unresolved': 'open'}.get(issue.get('status'))
        if not lifecycle:
            raise SentryError("Unknown Sentry issue lifecycle.")
        with db.connection() as conn:
            db.lock(conn)
            if conn.execute("SELECT state FROM automatic_deliveries WHERE id=%s", (delivery['id'],)).fetchone()['state'] != 'pending':
                return
            connection = conn.execute("SELECT * FROM sentry_connections WHERE id=%s", (delivery['connection_id'],)).fetchone()
            cursor = conn.execute("SELECT * FROM automatic_issues WHERE connection_id=%s AND issue_id=%s", (connection['id'], delivery['issue_id'])).fetchone()
            previous = dict(cursor) if cursor else None
            activity = issue.get('activity') or []
            changes = [a for a in activity if isinstance(a, dict) and a.get('type') in {'set_unresolved', 'set_regression', 'set_resolved', 'set_ignored'} and timestamp(a.get('dateCreated'))]
            latest = max(changes, key=lambda a: timestamp(a['dateCreated'])) if changes else None
            transition = str(latest.get('id')) if latest and latest.get('id') else None
            transition_at = timestamp(latest['dateCreated']) if latest else None
            prior_episode = conn.execute("SELECT triggered_at FROM automatic_incidents WHERE connection_id=%s AND issue_id=%s ORDER BY episode DESC LIMIT 1", (connection['id'], delivery['issue_id'])).fetchone() if previous else None
            cutoff = previous['transition_at'] or (prior_episode['triggered_at'] if prior_episode else None) if previous else None
            intervening_close = any(a['type'] in {'set_resolved', 'set_ignored'} and (not cutoff or timestamp(a['dateCreated']) > cutoff) and transition_at and timestamp(a['dateCreated']) < transition_at for a in changes)
            newer_reopen = bool(latest and latest['type'] in {'set_unresolved', 'set_regression'}
                and transition and (not previous or transition != previous['transition_key'])
                and (latest['type'] == 'set_regression' or intervening_close)
                and (not previous or not previous['transition_at'] or transition_at > previous['transition_at']))
            category = original.get('issueCategory') or issue.get('issueCategory')
            created = delivery['action'] == 'created' and (not previous or previous['episode'] == 0)
            reopening = delivery['action'] != 'created' and lifecycle == 'open' and (newer_reopen or previous and previous['lifecycle'] in {'resolved', 'archived'} and delivery['action'] == 'unresolved')
            episode_number = previous['episode'] if previous else 0
            incident_id = None
            if lifecycle == 'open' and category == 'error' and (created or reopening) and eligible(conn, connection, delivery['project_id']):
                episode_number += 1
                incident_id = self.create_episode(conn, delivery, original, connection, episode_number, 'created' if created else 'reopened')
            elif previous and episode_number:
                row = conn.execute("SELECT id FROM automatic_incidents WHERE connection_id=%s AND issue_id=%s AND episode=%s", (connection['id'], delivery['issue_id'], episode_number)).fetchone()
                incident_id = row['id'] if row else None
            conn.execute("INSERT INTO automatic_issues(connection_id,issue_id,user_id,project_id,lifecycle,episode,transition_key,transition_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(connection_id,issue_id) DO UPDATE SET lifecycle=excluded.lifecycle,episode=excluded.episode,transition_key=COALESCE(excluded.transition_key,automatic_issues.transition_key),transition_at=COALESCE(excluded.transition_at,automatic_issues.transition_at)", (connection['id'], delivery['issue_id'], connection['user_id'], delivery['project_id'], lifecycle, episode_number, transition, transition_at))
            conn.execute("UPDATE automatic_incidents SET lifecycle=%s WHERE connection_id=%s AND issue_id=%s AND episode=%s", (lifecycle, connection['id'], delivery['issue_id'], episode_number))
            conn.execute("UPDATE automatic_deliveries SET state='accepted',incident_id=%s,error=NULL WHERE id=%s", (incident_id, delivery['id']))

    @staticmethod
    def create_episode(conn, delivery, issue, connection, number, trigger):
        stamp = delivery['received_at']
        event_time = timestamp(issue.get('firstSeen') if trigger == 'created' else issue.get('lastSeen')) or datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        observation = {'message': redact(str(issue.get('title') or 'Sentry error issue'))[0][:2000], 'timestamp': event_time,
            'source_issue_id': delivery['issue_id'], 'source_project_id': delivery['project_id'], 'source_project': issue['project'].get('slug'),
            'source_format': 'sentry', 'source_dataset': 'issue', 'source_note': 'Triggering issue notification; this is an observed symptom, not a full error event.', 'level': 'error'}
        context = (issue.get('contexts') or {}).get('trace') or {}
        trace = issue.get('trace_id') or context.get('trace_id')
        if isinstance(trace, str) and re.fullmatch(r'[a-fA-F0-9]{32}', trace):
            observation['trace_id'] = trace
        else:
            trace = None
        reason = queue_reason(conn, connection['user_id'], time.time())
        state = 'deferred' if reason else 'queued'
        identifier, attempt_id = db.uid(), db.uid()
        conn.execute("INSERT INTO automatic_incidents(id,user_id,connection_id,issue_id,project_id,project_name,title,episode,trigger,triggered_at,received_at,resume_until,collect_after,lifecycle,state,observation,trace_id,error) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',%s,%s,%s,%s)",
            (identifier, connection['user_id'], connection['id'], delivery['issue_id'], delivery['project_id'], redact(str(issue['project'].get('name') or issue['project'].get('slug') or delivery['project_id']))[0][:120], observation['message'][:120], number, trigger, event_time, stamp, stamp+WINDOW, stamp+60, state, json.dumps(observation), trace, reason))
        conn.execute("INSERT INTO automatic_attempts(id,incident_id,user_id,number,state,created_at,resume_until) VALUES(%s,%s,%s,1,%s,%s,%s)", (attempt_id, identifier, connection['user_id'], state, db.now(), stamp+WINDOW))
        episode = dict(conn.execute("SELECT * FROM automatic_incidents WHERE id=%s", (identifier,)).fetchone())
        attempt = dict(conn.execute("SELECT * FROM automatic_attempts WHERE id=%s", (attempt_id,)).fetchone())
        notify(conn, episode, attempt, alert=True)
        return identifier

    def expire_delivery(self, delivery):
        # Ambiguous transitions cannot justify a second investigation. A new
        # issue can still be retained with an explicit retry after the outage.
        with db.connection() as conn:
            db.lock(conn)
            issue = json.loads(delivery['payload'])['data']['issue']
            existing = conn.execute("SELECT id FROM automatic_incidents WHERE connection_id=%s AND issue_id=%s ORDER BY episode DESC LIMIT 1", (delivery['connection_id'], delivery['issue_id'])).fetchone()
            identifier = existing['id'] if existing else None
            if not existing and delivery['action'] == 'created' and issue.get('issueCategory') == 'error':
                connection = conn.execute("SELECT * FROM sentry_connections WHERE id=%s", (delivery['connection_id'],)).fetchone()
                identifier = self.create_episode(conn, delivery, issue, connection, 1, 'created')
                conn.execute("INSERT INTO automatic_issues(connection_id,issue_id,user_id,project_id,lifecycle,episode) VALUES(%s,%s,%s,%s,'open',1) ON CONFLICT DO NOTHING", (connection['id'], delivery['issue_id'], connection['user_id'], delivery['project_id']))
            conn.execute("UPDATE automatic_deliveries SET state='expired',incident_id=%s,error='Reconciliation was unavailable for 24 hours; explicit retry required.' WHERE id=%s", (identifier, delivery['id']))

    def advance(self):
        with db.connection() as conn:
            db.lock(conn)
            # Notifications are a separate durable handoff after saving outcomes.
            ended = conn.execute("SELECT i.* FROM automatic_incidents i JOIN automatic_attempts a ON a.incident_id=i.id WHERE a.state IN ('completed','inconclusive','failed','interrupted') AND NOT EXISTS (SELECT 1 FROM email_jobs e WHERE e.dedupe_key='incident:' || i.id || ':result:' || a.id) ORDER BY i.received_at LIMIT 20").fetchall()
            for episode in ended:
                for attempt in conn.execute("SELECT * FROM automatic_attempts WHERE incident_id=%s AND state IN ('completed','inconclusive','failed','interrupted')", (episode['id'],)).fetchall():
                    notify(conn, dict(episode), dict(attempt))
            pending = conn.execute("SELECT * FROM automatic_attempts WHERE state IN ('queued','importing','investigating','waiting','deferred') ORDER BY created_at,id LIMIT 100").fetchall()
            for item in pending:
                attempt = dict(item)
                episode = dict(conn.execute("SELECT * FROM automatic_incidents WHERE id=%s", (attempt['incident_id'],)).fetchone())
                if attempt['investigation_id']:
                    saved = conn.execute("SELECT * FROM investigations WHERE id=%s", (attempt['investigation_id'],)).fetchone()
                    if saved['state'] in {'completed', 'failed', 'interrupted'}:
                        state = ('inconclusive' if json.loads(saved['report'])['assessment'] == 'inconclusive' else 'completed') if saved['state'] == 'completed' else saved['state']
                        finish(conn, episode, attempt, state, saved['error'])
                        continue
                    if attempt['started_at']:
                        state = 'waiting' if saved['state'] == 'quota_waiting' else 'investigating'
                        conn.execute("UPDATE automatic_attempts SET state=%s WHERE id=%s", (state, attempt['id']))
                        conn.execute("UPDATE automatic_incidents SET state=%s WHERE id=%s", (state, episode['id']))
                        continue
                connection = conn.execute("SELECT * FROM sentry_connections WHERE id=%s", (episode['connection_id'],)).fetchone()
                stamp = time.time()
                if stamp >= attempt['resume_until'] or not eligible(conn, connection, episode['project_id']):
                    message = 'The 24-hour automatic resumption window expired. Retry explicitly.' if stamp >= attempt['resume_until'] else 'Automation is disabled or the connection/project is unavailable. Retry explicitly after enabling it.'
                    finish(conn, episode, attempt, 'interrupted', message)
                    if attempt['investigation_id']:
                        conn.execute("UPDATE investigations SET state='interrupted',error=%s,completed_at=%s WHERE id=%s AND state='queued'", (message, db.now(), attempt['investigation_id']))
                    if attempt['import_id']:
                        conn.execute("UPDATE sentry_imports SET state='cancelled',error=%s,completed_at=%s WHERE id=%s AND state='queued'", (message, db.now(), attempt['import_id']))
                    continue
                if stamp < episode['collect_after']:
                    continue
                unresolved = conn.execute("SELECT 1 FROM automatic_deliveries WHERE incident_id=%s AND state='pending'", (episode['id'],)).fetchone()
                if unresolved:
                    self.defer(conn, episode, attempt, 'Waiting to reconcile the current Sentry issue before starting work.')
                    continue
                if attempt['import_id']:
                    job = conn.execute("SELECT * FROM sentry_imports WHERE id=%s", (attempt['import_id'],)).fetchone()
                    if job['state'] in {'queued', 'running', 'rate_limited'}:
                        state = 'waiting' if job['state'] == 'rate_limited' else 'importing'
                        conn.execute("UPDATE automatic_incidents SET state=%s WHERE id=%s", (state, episode['id']))
                        continue
                    if job['state'] == 'interrupted':
                        # Evidence collection has no model spending. Resume by
                        # retaining a new snapshot; never overwrite the old one.
                        conn.execute("UPDATE automatic_attempts SET import_id=NULL,state='queued' WHERE id=%s", (attempt['id'],))
                        attempt['import_id'] = None
                    elif job['state'] in {'failed', 'cancelled'}:
                        finish(conn, episode, attempt, 'failed', job['error'] or 'Sentry evidence collection failed. Retained observations remain available.')
                        continue
                    elif not attempt['investigation_id']:
                        if conn.execute("SELECT 1 FROM investigations WHERE collection_id=%s AND state IN ('queued','running','quota_waiting')", (job['collection_id'],)).fetchone():
                            self.defer(conn, episode, attempt, 'An investigation already uses this snapshot. Waiting for it to finish.')
                            continue
                        reason = queue_reason(conn, episode['user_id'], stamp, admission=False)
                        if reason:
                            self.defer(conn, episode, attempt, reason)
                            continue
                        identifier = db.uid()
                        conn.execute("INSERT INTO investigations(id,user_id,collection_id,engine,state,created_at,usage) VALUES(%s,%s,%s,'groq','queued',%s,%s)", (identifier, episode['user_id'], job['collection_id'], db.now(), json.dumps({'triage': {'requested': True, 'state': 'pending'}, 'automatic_incident_id': episode['id'], 'trigger_observation_id': 'auto-trigger-' + job['id']})))
                        conn.execute("UPDATE automatic_attempts SET investigation_id=%s,state='queued',error=NULL WHERE id=%s", (identifier, attempt['id']))
                        conn.execute("UPDATE automatic_incidents SET state='queued',error=NULL WHERE id=%s", (episode['id'],))
                        continue
                if attempt['investigation_id']:
                    # Requeue unstarted investigations after UTC capacity returns.
                    day = datetime.fromtimestamp(stamp, timezone.utc).date()
                    used = conn.execute("SELECT attempts FROM automatic_daily_allowance WHERE user_id=%s AND day=%s", (episode['user_id'], day)).fetchone()
                    if attempt['state'] == 'deferred' and (not used or used['attempts'] < 5):
                        conn.execute("UPDATE automatic_attempts SET state='queued',error=NULL WHERE id=%s", (attempt['id'],))
                        conn.execute("UPDATE automatic_incidents SET state='queued',error=NULL WHERE id=%s", (episode['id'],))
                        self.enqueued.discard(attempt['investigation_id'])
                    continue
                if not attempt['import_id']:
                    reason = queue_reason(conn, episode['user_id'], stamp, admission=False)
                    if reason:
                        self.defer(conn, episode, attempt, reason)
                        continue
                    trigger_time = datetime.fromisoformat(episode['triggered_at'])
                    # Fifteen minutes ending one minute after the event captures
                    # immediate aftermath without requesting future telemetry.
                    end = min(trigger_time+timedelta(seconds=60), datetime.now(timezone.utc))
                    options = parameters({'projects': [episode['project_id']], 'datasets': ['errors', 'logs', 'spans'], 'start': (end-timedelta(minutes=15)).isoformat(), 'end': end.isoformat(), 'title': episode['title'], 'symptom': 'Sentry reported: ' + episode['title']}, connection)
                    try:
                        job, created = create_import(episode['user_id'], connection, options, transaction=conn, scope_key=attempt['id'])
                        # An episode/retry always owns a distinct immutable scope.
                        if not created:
                            job, _ = create_import(episode['user_id'], connection, options, retry_of=job['id'], transaction=conn, scope_key=attempt['id'])
                    except HTTPException as exc:
                        if exc.status_code in {409, 429}:
                            self.defer(conn, episode, attempt, 'Evidence import capacity is busy. Automatic resumption remains available for 24 hours.')
                            continue
                        raise
                    observation = json.loads(episode['observation'])
                    conn.execute("INSERT INTO evidence(id,user_id,collection_id,kind,service,timestamp,payload) VALUES(%s,%s,%s,'log','unknown',%s,%s)", ('auto-trigger-' + job['id'], episode['user_id'], job['collection_id'], episode['triggered_at'], json.dumps(observation)))
                    summary = json.loads(conn.execute("SELECT summary FROM collections WHERE id=%s", (job['collection_id'],)).fetchone()['summary'])
                    summary['observations'] += 1
                    summary['input_bytes'] += len(json.dumps(observation).encode())
                    summary['services'] = sorted(set(summary['services']) | {'unknown'})
                    summary['kinds'] = sorted(set(summary['kinds']) | {'log'})
                    summary['trigger_note'] = observation['source_note']
                    conn.execute("UPDATE collections SET summary=%s WHERE id=%s", (json.dumps(summary), job['collection_id']))
                    conn.execute("UPDATE automatic_attempts SET import_id=%s,state='importing',error=NULL WHERE id=%s", (job['id'], attempt['id']))
                    conn.execute("UPDATE automatic_incidents SET state='importing',error=NULL WHERE id=%s", (episode['id'],))

    @staticmethod
    def defer(conn, episode, attempt, reason):
        conn.execute("UPDATE automatic_attempts SET state='deferred',error=%s WHERE id=%s", (reason, attempt['id']))
        conn.execute("UPDATE automatic_incidents SET state='deferred',error=%s WHERE id=%s", (reason, episode['id']))


routes = [Route('/api/connections/sentry/{id}/automation', get_settings, methods=['GET']),
    Route('/api/connections/sentry/{id}/automation', put_settings, methods=['PUT']),
    Route('/api/automatic-incidents', listing), Route('/api/automatic-incidents/{id}', detail),
    Route('/api/automatic-incidents/{id}/retry', retry, methods=['POST'])]
