"""Synthetic provider contract fixtures; no Datadog account or network calls."""
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
import httpx
from cryptography.fernet import Fernet
from backend import config
from backend.connection_security import seal
from backend.datadog_client import DatadogClient, SITES
from backend.datadog_imports import normalize
from backend.datadog_automation import parse_event
from backend.providers import ProviderError


class DatadogContracts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.object(config, 'CONNECTION_KEY', Fernet.generate_key().decode()))
        self.connection = {'id': 'fixture', 'organization': 'Fixture', 'region': 'eu1',
                           'credentials': seal({'api_key': 'a'*32, 'application_key': 'b'*40})}

    async def test_regional_hosts_keys_and_opaque_pagination(self):
        requests = []
        def response(request):
            requests.append(request)
            self.assertEqual(request.headers['DD-API-KEY'], 'a'*32)
            return httpx.Response(200, json={'data': [], 'meta': {'page': {'after': 'opaque'}}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
            client = DatadogClient(http)
            for site, host in SITES.items():
                page = await client.fetch_page({**self.connection, 'region': site}, 'logs', {'start': '2026-11-10T00:00:00Z', 'end': '2026-11-10T00:01:00Z'})
                self.assertTrue(str(requests[-1].url).startswith(host))
                self.assertEqual(page.cursor, 'opaque')
            with self.assertRaises(ProviderError):
                await client.request({**self.connection, 'region': 'https://attacker.invalid'}, 'GET', '/api/v1/validate')

    async def test_redirects_are_not_followed_and_rate_reset_is_relative(self):
        for status in (302, 403, 429):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, headers={'location': 'https://attacker.invalid', 'x-ratelimit-reset': '12'}))) as http:
                with self.assertRaises(ProviderError) as error:
                    await DatadogClient(http).request(self.connection, 'GET', '/api/v1/validate')
                self.assertEqual(error.exception.status, status)
                if status == 429:
                    self.assertEqual(error.exception.retry_after, 12)

    async def test_metrics_keep_millisecond_points_and_do_not_impute_null(self):
        def response(request):
            self.assertEqual(request.url.params['from'], '1794268800')
            return httpx.Response(200, json={'series': [{'metric': 'system.cpu.user', 'tag_set': ['service:payment', 'env:test'], 'unit': None, 'interval': 10000, 'pointlist': [[1794268800000, 80], [1794268810000, None]]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
            page = await DatadogClient(http).fetch_page(self.connection, 'metrics', {'start': '2026-11-10T00:00:00+00:00', 'end': '2026-11-10T00:01:00+00:00', 'metric_queries': ['avg:system.cpu.user{service:payment}']})
        self.assertEqual(len(page.records), 1)
        row, _, _ = normalize(page.records[0], 'metrics', {}, self.connection)
        self.assertEqual(row['value'], 80)
        self.assertEqual(row['service'], 'payment')
        self.assertEqual(row['timestamp'], '2026-11-10T00:00:00+00:00')

    def test_redaction_identity_and_configuration_timestamp_role(self):
        row, _, _ = normalize({'id': 'log1', 'attributes': {'message': 'api_key=private-value', 'service': 'payment', 'tags': ['env:prod'], 'attributes': {'trace_id': '1234', 'span_id': '456'}}}, 'logs', {}, self.connection)
        self.assertNotIn('private-value', row['original'])
        self.assertEqual(row['trace_id_format'], 'decimal64')
        self.assertEqual(row['environment'], 'prod')
        monitor, _, _ = normalize({'id': 12, 'name': 'Pool alert', 'overall_state': 'Alert'}, 'monitors', {}, self.connection)
        self.assertEqual(monitor['timestamp_role'], 'retrieved_configuration')

    def test_cycle_scope_is_canonical_and_missing_identity_fails(self):
        value = {'monitor_id': '12', 'cycle_key': 'cycle', 'scope': 'env:test,service:payment', 'transition': 'Re-Triggered', 'timestamp': int(datetime.now(timezone.utc).timestamp()*1000)}
        first = parse_event(value)
        second = parse_event({**value, 'scope': 'service:payment,env:test'})
        self.assertEqual(first['resource_key'], second['resource_key'])
        with self.assertRaises(ValueError):
            parse_event({**value, 'cycle_key': None})

    def test_missing_span_status_never_implies_success(self):
        for status in (None, 'unrecognized', [], 'ok', 'error'):
            with self.subTest(status=status):
                row, _, _ = normalize({'id': 'span', 'attributes': {'status': status}}, 'spans', {}, self.connection)
                self.assertEqual(row['status'], status if status in ('ok', 'error') else 'unknown')
