"""Allowlisted Datadog reads with bounded responses and explicit capabilities."""
import asyncio
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
import httpx
from .connection_security import unseal
from .providers import ProviderError, ProviderPage

SITES = {'us1': 'https://api.datadoghq.com', 'us3': 'https://api.us3.datadoghq.com',
         'us5': 'https://api.us5.datadoghq.com', 'eu1': 'https://api.datadoghq.eu',
         'ap1': 'https://api.ap1.datadoghq.com', 'ap2': 'https://api.ap2.datadoghq.com',
         'uk1': 'https://api.uk1.datadoghq.com'}
SCOPES = {'logs': 'logs_read_data', 'spans': 'apm_read', 'metrics': 'timeseries_query', 'monitors': 'monitors_read'}
PATH = re.compile(r'/api/(?:v1/(?:validate|query|monitor(?:/[0-9]+)?)|v2/(?:logs|spans)/events/search)\Z')


class DatadogClient:
    def __init__(self, http, *, clock=time.monotonic, sleep=asyncio.sleep):
        self.http, self.clock, self.sleep = http, clock, sleep
        self.span_lock = asyncio.Lock()
        self.next_span = 0.0

    async def request(self, connection, method, path, *, params=None, payload=None):
        site = connection['region']
        if site not in SITES or not PATH.fullmatch(path) or method not in {'GET', 'POST'}:
            raise ProviderError('Invalid Datadog API destination.')
        credentials = unseal(connection['credentials'])
        if not all(isinstance(credentials.get(k), str) and re.fullmatch(r'[A-Za-z0-9_-]{16,128}', credentials[k]) for k in ('api_key', 'application_key')):
            raise ProviderError('Invalid Datadog credential format.')
        if '/spans/' in path:
            async with self.span_lock:
                await self.sleep(max(0, self.next_span - self.clock()))
                self.next_span = self.clock() + 1.21
        try:
            async with self.http.stream(method, SITES[site] + path, params=params, json=payload,
                    headers={'DD-API-KEY': credentials['api_key'], 'DD-APPLICATION-KEY': credentials['application_key']},
                    timeout=15, follow_redirects=False) as response:
                if response.status_code >= 300:
                    delay = 0
                    if response.status_code == 429:
                        try:
                            delay = float(response.headers.get('retry-after', response.headers.get('x-ratelimit-reset', '60')))
                            if not math.isfinite(delay) or delay < 0:
                                delay = 60
                        except ValueError:
                            delay = 60
                    raise ProviderError({401: 'Datadog credentials were rejected.', 403: 'Datadog denied this capability; check application-key scopes.',
                                         429: 'Datadog rate limit reached.'}.get(response.status_code, 'Datadog request failed (HTTP %s).' % response.status_code), response.status_code, delay)
                content = bytearray()
                async for part in response.aiter_bytes():
                    content.extend(part)
                    if len(content) > 2 * 1024 * 1024:
                        raise ProviderError('Datadog response exceeds 2 MiB; narrow the query.')
                try:
                    return json.loads(content)
                except (ValueError, RecursionError):
                    raise ProviderError('Datadog returned invalid JSON.') from None
        except httpx.RequestError:
            raise ProviderError('Datadog could not be reached.') from None

    async def fetch_page(self, connection, dataset, options, cursor=None):
        if dataset in {'logs', 'spans'}:
            payload = {'filter': {'from': options['start'], 'to': options['end'], 'query': options.get('query', '*')},
                       'sort': 'timestamp', 'page': {'limit': 100}}
            if cursor:
                payload['page']['cursor'] = cursor
            if dataset == 'logs':
                payload['filter']['storage_tier'] = 'indexes'
            data = await self.request(connection, 'POST', f'/api/v2/{dataset}/events/search', payload=payload)
            if not isinstance(data, dict) or not isinstance(data.get('data'), list):
                raise ProviderError('Invalid Datadog evidence page.')
            meta = data.get('meta') or {}
            if not isinstance(meta, dict) or not isinstance(meta.get('page', {}), dict):
                raise ProviderError('Invalid Datadog pagination metadata.')
            next_cursor = (meta.get('page') or {}).get('after')
            if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 4096):
                raise ProviderError('Invalid Datadog pagination cursor.')
            warnings = ['Provider reports incomplete search results.'] if meta.get('warnings') or meta.get('status') in {'timeout', 'done_with_warnings'} else []
            return ProviderPage(data['data'], next_cursor, warnings)
        if dataset == 'metrics':
            records, warnings = [], []
            for query in options.get('metric_queries', [])[:5]:
                data = await self.request(connection, 'GET', '/api/v1/query', params={
                    'from': int(datetime.fromisoformat(options['start']).timestamp()),
                    'to': int(datetime.fromisoformat(options['end']).timestamp()), 'query': query})
                series = data.get('series') if isinstance(data, dict) else None
                if not isinstance(series, list):
                    raise ProviderError('Invalid Datadog metric response.')
                if len(series) > 10:
                    warnings.append('Metric series truncated at ten per query.')
                for item in series[:10]:
                    if not isinstance(item, dict) or not isinstance(item.get('pointlist', []), list):
                        raise ProviderError('Invalid Datadog metric series.')
                    points = item.get('pointlist', [])
                    if any(not isinstance(p, list) or len(p) != 2 or type(p[0]) not in (int, float)
                           or not math.isfinite(p[0]) or p[1] is not None and (type(p[1]) not in (int, float) or not math.isfinite(p[1])) for p in points):
                        raise ProviderError('Invalid Datadog metric points.')
                    if len(points) > 120:
                        warnings.append('Metric series sampled at 120 original points, retaining endpoints and extrema; nulls are not imputed.')
                        finite = [i for i, p in enumerate(points) if p[1] is not None]
                        indices = {0, len(points)-1}
                        if finite:
                            indices.update((min(finite, key=lambda i: points[i][1]), max(finite, key=lambda i: points[i][1])))
                        indices.update(round(i*(len(points)-1)/115) for i in range(116))
                        points = [points[i] for i in sorted(indices)]
                    for point in points:
                        if isinstance(point, list) and len(point) == 2 and point[1] is not None:
                            records.append({'metric': item.get('metric'), 'point': point, 'scope': item.get('scope'),
                                            'tag_set': item.get('tag_set', []), 'unit': item.get('unit'),
                                            'interval': item.get('interval'), 'query': query, 'expression': item.get('expression')})
            return ProviderPage(records, warnings=warnings)
        if dataset == 'monitors':
            page = int(cursor or 0)
            data = await self.request(connection, 'GET', '/api/v1/monitor', params={'page': page, 'page_size': 100})
            if not isinstance(data, list):
                raise ProviderError('Invalid Datadog monitor list.')
            return ProviderPage(data, str(page + 1) if len(data) == 100 else None)
        raise ProviderError('Unsupported Datadog dataset.')

    async def validate_connection(self, connection):
        valid = await self.request(connection, 'GET', '/api/v1/validate')
        if not isinstance(valid, dict) or valid.get('valid') is not True:
            raise ProviderError('Datadog API key is invalid.', 401)
        end = datetime.now(timezone.utc)
        options = {'start': (end - timedelta(minutes=1)).isoformat(), 'end': end.isoformat(),
                   'query': 'service:__tracedesk_permission_probe__', 'metric_queries': ['avg:system.cpu.user{service:__tracedesk_permission_probe__}']}
        capabilities = {}
        for dataset, permission in SCOPES.items():
            try:
                await self.fetch_page(connection, dataset, options)
                capabilities[dataset] = {'state': 'available', 'permission': permission}
            except ProviderError as exc:
                if exc.status == 401:
                    raise
                capabilities[dataset] = {'state': 'unavailable' if exc.status == 403 else 'error', 'permission': permission, 'reason': str(exc)}
        return capabilities
