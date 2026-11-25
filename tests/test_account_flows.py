"""Account and email logic checks with mocked storage and HTTP providers."""
import base64
import hashlib
import hmac
import json
import os
import time
import unittest
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import httpx
from cryptography.fernet import Fernet
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend import account_email, auth, automation, config, db, demo, email_service, postgres


PASSWORD = "artificial-account-password"


def user(role=None):
    return {"id": "fixture-user", "name": "Alice", "email": "alice@example.test",
            "password_hash": auth.hasher.hash(PASSWORD), "_role": role, "_verified": None}


def request(value=None, *, cookie="", csrf="", headers=None):
    raw = json.dumps(value or {}).encode()
    fields = [(b"content-type", b"application/json")]
    if cookie:
        fields.append((b"cookie", (auth.COOKIE + "=" + cookie).encode()))
    if csrf:
        fields.append((b"x-csrf-token", csrf.encode()))
    fields.extend((key.encode(), value.encode()) for key, value in (headers or {}).items())
    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/", "headers": fields,
                    "client": ("127.0.0.1", 1234), "scheme": "https", "server": ("fixture.test", 443)}, receive)


@contextmanager
def connection_mock():
    conn = MagicMock()
    with patch.object(db, "connection") as factory, patch.object(db, "lock"):
        factory.return_value.__enter__.return_value = conn
        yield conn


class AccountFlows(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        auth.attempts.clear()
        self.enterContext(patch.object(postgres, "pool", side_effect=AssertionError("Live database access is forbidden in logic checks.")))

    async def test_signup_normalizes_email_hashes_password_and_requests_verification(self):
        with patch.object(db, "execute") as insert, patch.object(account_email, "issue", return_value=True) as issue, \
             patch.object(auth, "session_response", return_value=JSONResponse({"ok": True})) as session:
            response = await auth.signup(request({"name": " Alice ", "email": " ALICE@EXAMPLE.TEST ", "password": PASSWORD}))
        self.assertEqual(response.status_code, 200)
        stored = insert.call_args.args[1]
        self.assertEqual(stored[1:3], ("Alice", "alice@example.test"))
        self.assertNotEqual(stored[3], PASSWORD)
        self.assertTrue(auth.hasher.verify(stored[3], PASSWORD))
        self.assertEqual(issue.call_args.args[1], "verify")
        session.assert_called_once()

    async def test_invalid_signup_and_duplicate_account_do_not_queue_email(self):
        with patch.object(db, "execute") as insert, patch.object(account_email, "issue") as issue:
            with self.assertRaises(HTTPException) as invalid:
                await auth.signup(request({"name": "Alice", "email": "invalid", "password": PASSWORD}))
            self.assertEqual(invalid.exception.status_code, 400)
            insert.assert_not_called()
            insert.side_effect = db.IntegrityError("artificial duplicate")
            with self.assertRaises(HTTPException) as duplicate:
                await auth.signup(request({"name": "Alice", "email": "alice@example.test", "password": PASSWORD}))
            self.assertEqual(duplicate.exception.status_code, 409)
            issue.assert_not_called()

    async def test_correct_login_issues_session_and_wrong_or_unknown_login_is_401(self):
        row = user()
        with patch.object(db, "one", return_value=row), patch.object(auth, "session_response", return_value=JSONResponse({"ok": True})) as session:
            response = await auth.login(request({"email": "ALICE@EXAMPLE.TEST", "password": PASSWORD}))
        self.assertEqual(response.status_code, 200)
        session.assert_called_once_with(row, "")
        errors = []
        for candidate in (row, None):
            with patch.object(db, "one", return_value=candidate), patch.object(auth, "session_response") as session:
                with self.assertRaises(HTTPException) as result:
                    await auth.login(request({"email": "alice@example.test", "password": "incorrect"}))
                errors.append((result.exception.status_code, result.exception.detail))
                session.assert_not_called()
        self.assertEqual(errors[0], errors[1])
        self.assertEqual(errors[0][0], 401)

    async def test_sessions_store_digest_use_secure_cookie_and_rotate_old_session(self):
        row = user()
        with connection_mock() as conn, patch.object(auth, "PUBLIC_URL", "https://desk.example.test"):
            conn.execute.return_value.fetchone.return_value = {"password_hash": row["password_hash"]}
            response = auth.session_response(row, "old-fixture-session")
        cookies = SimpleCookie(response.headers["set-cookie"])
        token = cookies[auth.COOKIE].value
        self.assertTrue(cookies[auth.COOKIE]["httponly"])
        self.assertTrue(cookies[auth.COOKIE]["secure"])
        self.assertEqual(cookies[auth.COOKIE]["samesite"], "lax")
        insertion = next(call.args[1] for call in conn.execute.call_args_list if call.args[0].startswith("INSERT INTO sessions"))
        self.assertEqual(insertion[:2], (auth.digest(token), row["id"]))
        self.assertNotIn(token, insertion)
        self.assertGreater(insertion[2], time.time())
        self.assertEqual(json.loads(response.body)["csrf"], auth.digest(token + ":csrf"))
        deletion = next(call.args[1] for call in conn.execute.call_args_list if call.args[0].startswith("DELETE FROM sessions"))
        self.assertEqual(deletion[0], auth.digest("old-fixture-session"))

    async def test_current_session_is_cached_and_logout_revokes_it_with_csrf(self):
        row, token = user(), "fixture-session"
        message = request(cookie=token, csrf=auth.digest(token + ":csrf"))
        with patch.object(db, "one", return_value=row) as lookup, patch.object(db, "execute") as execute:
            self.assertEqual(auth.current(message)["id"], row["id"])
            self.assertEqual(json.loads(auth.me(message).body)["user"]["email"], row["email"])
            response = auth.logout(message)
        lookup.assert_called_once()
        execute.assert_called_once_with("DELETE FROM sessions WHERE digest=%s", (auth.digest(token),))
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        with patch.object(auth, "current", return_value=row), patch.object(db, "execute") as execute:
            with self.assertRaises(HTTPException) as result:
                auth.logout(request(cookie=token, csrf="incorrect"))
            self.assertEqual(result.exception.status_code, 403)
            execute.assert_not_called()

    async def test_demo_login_has_read_only_identity_and_does_not_issue_email(self):
        row = user("demo")
        with patch.object(db, "one", return_value=row), patch.object(demo, "ready", return_value=True), \
             patch.object(auth, "session_response", return_value=JSONResponse({"ok": True})) as session, \
             patch.object(account_email, "issue") as issue:
            self.assertEqual((await auth.demo_login(request({"password": PASSWORD}))).status_code, 200)
        session.assert_called_once()
        issue.assert_not_called()
        self.assertTrue(auth.public_user(row)["read_only"])
        self.assertTrue(auth.public_user(row)["email_verified"])

    async def test_expired_session_and_unverified_write_are_rejected(self):
        with patch.object(db, "one", return_value=None):
            with self.assertRaises(HTTPException) as expired:
                auth.current(request(cookie="expired"))
            self.assertEqual(expired.exception.status_code, 401)
        with self.assertRaises(HTTPException) as unverified:
            auth.require_verified(user())
        self.assertEqual(unverified.exception.status_code, 403)

    async def test_verification_queues_digest_and_fragment_link_with_expiry(self):
        row = user()
        with connection_mock() as conn, patch.object(auth, "is_demo", return_value=False), \
             patch.object(email_service, "configured", return_value=True), patch.object(email_service, "enqueue") as enqueue, \
             patch.object(config, "PUBLIC_URL", "https://desk.example.test"):
            conn.execute.return_value.fetchone.return_value = None
            conn.execute.return_value.fetchall.return_value = []
            self.assertTrue(account_email.issue(row, "verify"))
        args = enqueue.call_args.args
        text = args[3]
        token = text.split("/account#auth/verify/")[1].split()[0]
        insert = next(call.args[1] for call in conn.execute.call_args_list if call.args[0].startswith("INSERT INTO auth_tokens"))
        self.assertEqual(insert[:3], (auth.digest(token), row["id"], "verify"))
        self.assertNotIn(token, insert)
        self.assertAlmostEqual(insert[4] - insert[3], 86400, delta=1)
        self.assertEqual(args[4], "auth:" + auth.digest(token))

    async def test_recovery_does_not_disclose_account_existence(self):
        outputs = []
        for row in (user(), None):
            with patch.object(db, "one", return_value=row), patch.object(account_email, "issue"):
                response = await account_email.forgot_password(request({"email": "alice@example.test"}))
                outputs.append((response.status_code, json.loads(response.body)))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0][0], 200)

    async def test_reset_same_password_does_not_consume_link_or_revoke_sessions(self):
        row = user()
        with connection_mock() as conn:
            conn.execute.return_value.fetchone.side_effect = [{"user_id": row["id"]}, row]
            with self.assertRaises(HTTPException) as result:
                account_email.consume("fixture-digest", "reset", password=PASSWORD)
        self.assertEqual(result.exception.status_code, 400)
        self.assertFalse(any(call.args[0].startswith(("UPDATE", "DELETE", "INSERT")) for call in conn.execute.call_args_list))

    async def test_successful_reset_revokes_sessions_links_and_oauth_states(self):
        row = user()
        new_hash = auth.hasher.hash("replacement-fixture-password")
        with connection_mock() as conn, patch.object(email_service, "enqueue") as enqueue:
            conn.execute.return_value.fetchone.side_effect = [{"user_id": row["id"]}, row]
            account_email.consume("fixture-digest", "reset", password_hash=new_hash, password="replacement-fixture-password")
        statements = [call.args[0] for call in conn.execute.call_args_list]
        self.assertTrue(any(statement.startswith("UPDATE users SET password_hash") for statement in statements))
        self.assertTrue(any(statement.startswith("DELETE FROM sessions WHERE user_id") for statement in statements))
        self.assertTrue(any(statement.startswith("DELETE FROM sentry_oauth_states") for statement in statements))
        self.assertTrue(any(statement.startswith("UPDATE auth_tokens SET consumed_at") for statement in statements))
        self.assertEqual(enqueue.call_args.args[2], "password_changed")

    async def test_consumed_or_expired_link_is_rejected_without_changes(self):
        with connection_mock() as conn:
            conn.execute.return_value.fetchone.return_value = None
            with self.assertRaises(HTTPException) as result:
                account_email.consume("fixture-digest", "verify")
        self.assertEqual(result.exception.status_code, 400)
        self.assertEqual(len(conn.execute.call_args_list), 1)


class EmailFlows(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = Fernet.generate_key().decode()
        self.enterContext(patch.dict(os.environ, {"DESK_EMAIL_KEY": self.key, "RESEND_API_KEY": "fixture-only"}))
        self.enterContext(patch.object(postgres, "pool", side_effect=AssertionError("Live database access is forbidden in logic checks.")))
        self.payload = {"from": email_service.SENDER, "to": ["alice@example.test"], "reply_to": email_service.REPLY_TO,
                        "subject": "Fixture verification", "text": "Artificial verification message"}

    def job(self, **values):
        return {"id": "fixture-job", "attempts": 0, "expires": time.time() + 3600,
                "payload": email_service.encryption().encrypt(json.dumps(self.payload).encode()).decode(), **values}

    async def process(self, response, **job):
        calls = []
        async def provider(request):
            calls.append(request)
            return response
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            worker = email_service.EmailWorker(client)
            with patch.object(worker, "claim", return_value=self.job(**job)), patch.object(worker, "finish") as finish:
                self.assertTrue(await worker.process_one())
                return calls, finish.call_args.args

    async def test_successful_email_uses_stable_idempotency_and_expected_sender(self):
        calls, result = await self.process(httpx.Response(200, json={"id": "fixture-provider-id"}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].headers["idempotency-key"], "tracedesk/fixture-job")
        self.assertEqual(json.loads(calls[0].content), self.payload)
        self.assertEqual(self.payload["reply_to"], "yournishantraj@gmail.com")
        self.assertEqual(result[1:3], ("fixture-provider-id", False))

    async def test_transient_errors_retry_and_private_provider_body_is_not_saved(self):
        for code in (429, 500):
            calls, result = await self.process(httpx.Response(code, json={"error": "private provider body"}, headers={"retry-after": "120"}))
            self.assertEqual(len(calls), 1)
            self.assertTrue(result[2])
            self.assertNotIn("private", result[4])
            if code == 429:
                self.assertGreater(result[3], time.time() + 100)

    async def test_permanent_failure_or_retry_limit_does_not_retry(self):
        for response, values in ((httpx.Response(401, json={}), {}), (httpx.Response(500, json={}), {"attempts": 4})):
            _, result = await self.process(response, **values)
            self.assertFalse(result[2])
            self.assertIsNone(result[1])

    async def test_corrupt_encrypted_payload_never_reaches_provider(self):
        calls, result = await self.process(httpx.Response(200, json={"id": "fixture"}), payload="corrupt")
        self.assertEqual(calls, [])
        self.assertFalse(result[2])
        self.assertIn("invalid", result[4])

    async def test_incident_email_is_cancelled_when_owner_disables_notifications(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("No provider request is allowed."))) as client:
            worker = email_service.EmailWorker(client)
            with patch.object(worker, "claim", return_value=self.job(incident_id="fixture-incident")), \
                 patch.object(automation, "notification_allowed", return_value=False), patch.object(db, "execute") as execute:
                self.assertTrue(await worker.process_one())
                self.assertIn("state='cancelled'", execute.call_args.args[0])

    async def test_queue_encrypts_link_and_deduplicates_with_expiry(self):
        conn = MagicMock()
        email_service.enqueue(conn, user(), "verify", "Artificial link fixture-token", "auth:fixture-digest", time.time() + 86400)
        statement, values = conn.execute.call_args.args
        self.assertIn("ON CONFLICT(dedupe_key) DO NOTHING", statement)
        self.assertNotIn("fixture-token", values[4])
        payload = json.loads(email_service.encryption().decrypt(values[4].encode()))
        self.assertIn("fixture-token", payload["text"])
        self.assertEqual(payload["reply_to"], "yournishantraj@gmail.com")
        self.assertLessEqual(values[6] - values[5], 23 * 3600 + 1)

    async def test_delivery_receipts_require_valid_fresh_signature(self):
        secret_bytes = b"artificial-svix-secret-32-bytes!!"
        secret = "whsec_" + base64.b64encode(secret_bytes).decode()
        value = {"type": "email.delivered", "data": {"email_id": "fixture-provider-id"}}
        raw = json.dumps(value).encode()
        stamp = str(int(time.time()))
        signed = b"fixture-receipt." + stamp.encode() + b"." + raw
        signature = base64.b64encode(hmac.digest(secret_bytes, signed, "sha256")).decode()
        fields = {"svix-id": "fixture-receipt", "svix-timestamp": stamp, "svix-signature": "v1," + signature}
        self.assertEqual(email_service.verify_webhook(raw, fields, secret=secret), ("fixture-receipt", value))
        for body, headers, now in ((raw + b" ", fields, time.time()), (raw, fields, time.time() + 600)):
            with self.assertRaises(HTTPException) as invalid:
                email_service.verify_webhook(body, headers, secret=secret, now=now)
            self.assertEqual(invalid.exception.status_code, 400)


class FreshStartup(unittest.TestCase):
    def test_new_data_directory_preserves_legacy_backup_and_runs_migrations(self):
        with TemporaryDirectory() as directory:
            legacy = Path(directory) / "platform.sqlite3"
            legacy.write_bytes(b"retained artificial backup")
            fresh = Path(directory) / "supabase"
            with patch.object(db, "DATA", fresh), connection_mock() as conn, patch.object(db, "migrate") as migrate:
                conn.execute.return_value.fetchone.return_value = {"name": None}
                conn.execute.return_value.fetchall.return_value = []
                db.init()
                migrate.assert_called_once_with(conn)
            self.assertEqual(legacy.read_bytes(), b"retained artificial backup")
            self.assertTrue(fresh.is_dir())

    def test_shipped_demo_bundle_is_valid_for_fresh_installation(self):
        bundle = json.loads(demo.BUNDLE.read_text(encoding="utf-8"))
        self.assertTrue(demo.validate(bundle))
        self.assertEqual(len(bundle["cases"]), 6)


if __name__ == "__main__":
    unittest.main()
