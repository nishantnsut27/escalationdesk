"""Shared import worker extension for Datadog; uses existing durable job rows."""
import asyncio
import json
import time
from . import db
from .providers import ProviderError
from .sentry_client import SentryError


async def run_datadog(worker, job):
    connection = db.one("SELECT * FROM sentry_connections WHERE id=%s AND user_id=%s AND provider='datadog'", (job['connection_id'], job['user_id']))
    summary = json.loads(db.one('SELECT summary FROM collections WHERE id=%s', (job['collection_id'],))['summary'])
    options = json.loads(job['parameters'])
    streams = [{'dataset': dataset, 'cursor': None, 'seen': set()} for dataset in options['datasets']]
    final, error, pages, deadline = 'failed', None, 0, time.monotonic() + 300
    summary['acquisition_started_at'] = time.time()
    storage_failed = False
    try:
        worker.alive(job)
        if not connection:
            raise ProviderError('Datadog connection is unavailable.')
        generation = connection['generation']
        if options.get('_skip_telemetry'):
            summary['datasets'] = {d: {'state': 'unavailable', 'received': 0, 'pages': 0,
                'reason': options['_skip_telemetry']} for d in options['datasets']}
            streams = []
        worker.progress(job, summary)
        while streams and not summary.get('limit_reached'):
            if pages >= 150 or time.monotonic() >= deadline:
                summary['limit_reached'] = True; break
            stream = streams.pop(0)
            dataset = stream['dataset']
            coverage = summary['datasets'].setdefault(dataset, {'state': 'pending', 'received': 0, 'pages': 0})
            try:
                page = None
                for attempt in range(3):
                    worker.alive(job)
                    current = db.one('SELECT generation FROM sentry_connections WHERE id=%s', (connection['id'],))
                    if current['generation'] != generation:
                        raise ProviderError('Connection changed during import.')
                    try:
                        page = await worker.datadog.fetch_page(connection, dataset, options, stream['cursor'])
                        break
                    except ProviderError as exc:
                        wait = exc.retry_after if exc.status == 429 else 2 ** attempt
                        if attempt == 2 or exc.status not in {None, 429, 500, 502, 503, 504} or wait > 120 or time.monotonic()+wait >= deadline:
                            raise
                        worker.progress(job, summary, 'rate_limited' if exc.status == 429 else 'running', {'retry_at': time.time()+wait, 'message': str(exc)})
                        end = time.monotonic()+wait
                        while time.monotonic() < end:
                            await asyncio.sleep(min(1, end-time.monotonic())); worker.alive(job)
                worker.alive(job)
                if db.one('SELECT generation FROM sentry_connections WHERE id=%s', (connection['id'],))['generation'] != generation:
                    raise ProviderError('Connection changed before evidence could be retained.')
                worker.save(job, connection, {'id': 'datadog', 'slug': 'datadog'}, dataset, page.records, summary)
                pages += 1
                if page.warnings:
                    coverage['warnings'] = sorted(set(coverage.get('warnings', []) + page.warnings))
                received = coverage['received'] + len(page.records)
                coverage.update(received=received, pages=coverage['pages']+1,
                                state='partial' if page.cursor or coverage.get('warnings') else 'available' if received else 'empty')
                if page.cursor:
                    if page.cursor in stream['seen'] or page.cursor == stream['cursor']:
                        raise ProviderError('Datadog pagination did not advance; retained results are partial.')
                    stream['seen'].add(page.cursor); stream['cursor'] = page.cursor; streams.append(stream)
            except ProviderError as exc:
                coverage.update(state='partial' if coverage['received'] else 'unavailable' if exc.status in {403, 404} else 'error', reason=str(exc))
                if exc.status == 401:
                    raise
            worker.progress(job, summary)
        final = 'completed'
        if summary.get('limit_reached') or summary['malformed_records'] or any(v['state'] not in {'available', 'empty'} for v in summary['datasets'].values()):
            final = 'partial' if summary['observations'] else 'failed'
    except asyncio.CancelledError:
        final, error = 'interrupted', 'Import interrupted. Retry creates a new snapshot.'
        raise
    except (ProviderError, SentryError) as exc:
        final, error = ('partial' if summary['observations'] else 'failed'), str(exc)
    except db.Error:
        # Leave the durable job recoverable instead of inventing a final result.
        storage_failed = True
        raise
    except Exception:
        final, error = ('partial' if summary['observations'] else 'failed'), 'Datadog import failed unexpectedly; retained evidence may be partial.'
    finally:
        summary['coverage'] = 'Bounded Datadog snapshot; retention, sampling and permissions limit coverage. ' + (error or final)
        if not storage_failed:
            with db.connection() as conn:
                changed = conn.execute("UPDATE sentry_imports SET state=%s,completed_at=%s,error=%s,progress=%s WHERE id=%s AND state IN ('queued','running','rate_limited') RETURNING id",
                         (final, db.now(), error, json.dumps({'observations': summary['observations'], 'datasets': summary['datasets']}), job['id']))
                if changed.fetchone():
                    conn.execute('UPDATE collections SET summary=%s WHERE id=%s', (json.dumps(summary), job['collection_id']))
