"""Offline regression checks for PostgreSQL targeting and public diagnostics."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from psycopg import OperationalError
from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import UndefinedTable
from psycopg_pool import PoolTimeout, TooManyRequests

from backend import auth, postgres
from backend.api import create_app, MIGRATIONS
from scripts import rehearse_postgres


def supabase(project="productionfixture", role="postgres", host="aws-0-ap-southeast-1.pooler.supabase.com", port="5432"):
    return f"postgresql://{role}.{project}:unused-fixture@{host}:{port}/postgres?sslmode=require"


class TargetTests(unittest.TestCase):
    def test_separate_supabase_projects_on_shared_pooler_are_allowed(self):
        working, testing = supabase(), supabase("testfixture")
        with patch.dict(os.environ, {"DESK_DATABASE_URL": working, "DESK_TEST_DATABASE_URL": testing}):
            self.assertEqual(conninfo_to_dict(postgres.test_target())["user"], "postgres.testfixture")

    def test_same_project_is_rejected_across_roles_hosts_and_databases(self):
        for testing in (supabase(), supabase(role="readonly"),
                        supabase(host="aws-1-ap-south-1.pooler.supabase.com"),
                        supabase().replace("/postgres?", "/other?")):
            with self.subTest(target_variant=testing.split("@")[-1]), patch.dict(os.environ, {
                    "DESK_DATABASE_URL": supabase(), "DESK_TEST_DATABASE_URL": testing}):
                with self.assertRaisesRegex(ValueError, "separate PostgreSQL"):
                    postgres.test_target()

    def test_neon_direct_and_pooler_aliases_still_cannot_be_test_and_working_targets(self):
        working = "postgresql://fixture:unused@ep-fixture-pooler.neon.tech/neondb?sslmode=require"
        for testing in (working.replace("-pooler.", "."), working.replace("neondb", "other"),
                        working.replace(".tech/", ".tech:5433/"), working.replace("ep-fixture", "EP-FIXTURE")):
            with patch.dict(os.environ, {"DESK_DATABASE_URL": working, "DESK_TEST_DATABASE_URL": testing}):
                with self.assertRaisesRegex(ValueError, "separate"):
                    postgres.test_target()

    def test_tls_verifies_hostname_and_uses_trusted_certificates(self):
        options = conninfo_to_dict(postgres.connection_info(supabase()))
        self.assertEqual(options["sslmode"], "verify-full")
        self.assertTrue(Path(options["sslrootcert"]).is_file())
        self.assertEqual(options["connect_timeout"], "10")

    def test_supabase_uses_its_official_root_while_neon_keeps_public_cas(self):
        options = conninfo_to_dict(postgres.connection_info(supabase()))
        certificate = x509.load_pem_x509_certificate(Path(options["sslrootcert"]).read_bytes())
        self.assertTrue(certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        self.assertEqual(certificate.fingerprint(hashes.SHA256()).hex(),
                         "807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa")
        neon = postgres.connection_info("postgresql://fixture:unused@ep-fixture.neon.tech/neondb?sslmode=require")
        self.assertEqual(conninfo_to_dict(neon)["sslrootcert"], certifi.where())

    def test_unsafe_tls_or_unsupported_supabase_targets_fail_before_connecting(self):
        targets = (supabase(port="6543"), supabase().replace("postgres.productionfixture", "postgres"),
                   supabase().replace("aws-0-ap-southeast-1.pooler.supabase.com", "db.productionfixture.supabase.co"),
                   supabase().replace("require", "disable"), supabase().replace("require", "prefer"),
                   supabase() + "&hostaddr=127.0.0.1", supabase().replace(".com", ".com,other.test"),
                   supabase().replace("unused-fixture", "unescaped@fixture#password"))
        for target in targets:
            with self.assertRaises(ValueError) as result:
                postgres.connection_info(target)
            self.assertNotIn("unused-fixture", str(result.exception))

    def test_missing_test_url_is_rejected_without_connecting(self):
        with patch.dict(os.environ, {"DESK_TEST_DATABASE_URL": ""}):
            with self.assertRaisesRegex(ValueError, "required"):
                postgres.test_target()


class PoolTests(unittest.TestCase):
    def test_pool_defaults_to_five_and_accepts_smaller_limits(self):
        for limit in (None, "1", "5"):
            with patch.dict(postgres._pools, {}, clear=True), patch.dict(os.environ), patch.object(postgres, "ConnectionPool") as factory:
                if limit is None:
                    os.environ.pop("DESK_DATABASE_POOL_MAX", None)
                else:
                    os.environ["DESK_DATABASE_POOL_MAX"] = limit
                postgres.pool(supabase("testfixture"))
                self.assertEqual(factory.call_args.kwargs["max_size"], int(limit or "5"))
                self.assertEqual(factory.call_args.kwargs["min_size"], 0)

    def test_invalid_or_excessive_limits_never_create_a_pool(self):
        for limit in ("0", "6", "10", "-1", "invalid", ""):
            with patch.dict(postgres._pools, {}, clear=True), patch.dict(os.environ, {"DESK_DATABASE_POOL_MAX": limit}), patch.object(postgres, "ConnectionPool") as factory:
                with self.assertRaisesRegex(ValueError, "between 1 and 5"):
                    postgres.pool(supabase("testfixture"))
                factory.assert_not_called()


class RehearsalSafetyTests(unittest.TestCase):
    def test_rehearsal_rejects_missing_non_supabase_or_same_production_target_before_connecting(self):
        cases = (("", supabase("testfixture")),
                 ("postgresql://fixture:unused@ep-fixture.neon.tech/neondb?sslmode=require", supabase("testfixture")),
                 (supabase(), supabase(role="readonly")))
        for working, testing in cases:
            with patch.dict(os.environ, {"DESK_DATABASE_URL": working, "DESK_TEST_DATABASE_URL": testing}), \
                 patch.object(postgres, "connection") as connection:
                with self.assertRaises(ValueError):
                    rehearse_postgres.rehearsal()
                connection.assert_not_called()


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_liveness_never_authenticates_or_queries_database_even_with_cookie(self):
        with patch("backend.api.auth.current", side_effect=AssertionError("session database access")) as current, \
             patch("backend.api.db.connection", side_effect=AssertionError("database access")) as connection, \
             patch("backend.api.demo.availability", side_effect=AssertionError("demo database access")) as demo:
            for method in ("GET", "HEAD"):
                for cookie in (None, "expired-fixture", "demo-fixture"):
                    headers = {"Cookie": f"{auth.COOKIE}={cookie}"} if cookie else {}
                    response = await self.client.request(method, "/api/health", headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertIn("db;dur=0.0", response.headers["server-timing"])
                    if method == "GET":
                        self.assertEqual(response.json()["application"], "TraceDesk")
                        self.assertNotIn("demo_case_count", response.json())
            current.assert_not_called()
            connection.assert_not_called()
            demo.assert_not_called()

    async def test_readiness_reports_safe_failure_for_unreachable_or_empty_database(self):
        for error in (OperationalError, UndefinedTable, PoolTimeout, TooManyRequests, ValueError):
            with patch("backend.api.db.all_rows", side_effect=error("private credential detail")), \
                 patch("backend.api.auth.current", side_effect=AssertionError("unnecessary session query")):
                response = await self.client.get("/api/ready", headers={"Cookie": f"{auth.COOKIE}=fixture"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["database"], "unavailable")
            self.assertNotIn("private", response.text)
            self.assertEqual(response.headers["retry-after"], "5")

    async def test_readiness_requires_current_migrations(self):
        versions = [{"version": version} for version, _ in MIGRATIONS]
        for ledger, code in ((versions, 200), (versions[:-1], 503), ([], 503)):
            with patch("backend.api.db.all_rows", return_value=ledger):
                response = await self.client.get("/api/ready")
            self.assertEqual(response.status_code, code)
            self.assertEqual(response.json()["schema"], "ready" if code == 200 else "pending")

    async def test_demo_count_remains_available_on_its_own_endpoint(self):
        count = {"demo_available": True, "demo_case_count": 6}
        with patch("backend.api.demo.availability", return_value=count), \
             patch("backend.api.auth.current", side_effect=AssertionError("unnecessary session query")):
            response = await self.client.get("/api/demo/availability", headers={"Cookie": f"{auth.COOKIE}=fixture"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), count)

    async def test_demo_count_database_failure_is_safe_json(self):
        with patch("backend.api.demo.availability", side_effect=OperationalError("private connection detail")):
            response = await self.client.get("/api/demo/availability")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)

    async def test_protected_endpoints_still_check_sessions_and_handle_storage_failures(self):
        with patch("backend.api.auth.current", side_effect=OperationalError("private connection detail")) as current:
            response = await self.client.get("/api/collections", headers={"Cookie": f"{auth.COOKIE}=fixture"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)
        current.assert_called_once()


if __name__ == "__main__":
    unittest.main()
