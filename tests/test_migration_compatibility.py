"""Upgrade both recorded migration-3 versions without changing saved sources."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from database import IsolatedDatabase
from backend import db, postgres_migrations as migrations


class MigrationCompatibilityTests(unittest.TestCase):
    def historical_database(self, statement):
        versions = (*migrations.MIGRATIONS[:2], (3, statement))
        with patch.object(migrations, "MIGRATIONS", versions):
            db.init()

    def seed_sources(self):
        stamp = "2026-11-07T10:00:00.123456+05:30"
        with db.connection() as conn:
            conn.execute("INSERT INTO users VALUES(%s,%s,%s,%s,%s)", ("owner", "Owner", "owner@example.invalid", "retained-hash", stamp))
            conn.execute("INSERT INTO sessions VALUES(%s,%s,%s)", ("retained-session-digest", "owner", 2000000000))
            conn.execute("INSERT INTO collections VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", ("collection", "owner", "Saved sources", "", None, "paste", stamp, "fixture", "{}"))
            conn.execute("INSERT INTO investigations(id,collection_id,user_id,engine,state,created_at,report) VALUES(%s,%s,%s,%s,%s,%s,%s)", ("report", "collection", "owner", "groq", "completed", stamp, '{ "summary": "unchanged" }'))
            conn.execute("INSERT INTO sentry_connections(id,user_id,installation_id,organization,region,credentials,state,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", ("connection", "owner", "fixture-installation", "fixture", "us", "retained-encrypted-credential", "connected", stamp))
            for identifier, timestamp in (("aware", stamp), ("naive", "2026-11-07T10:00:00"), ("invalid", "no-clock")):
                payload = json.dumps({"message": "ENOSPC", "original": "untouched source\n", "source_line": 17}, indent=2)
                conn.execute("INSERT INTO evidence(id,user_id,kind,service,timestamp,payload,collection_id) VALUES(%s,%s,%s,%s,%s,%s,%s)", (identifier, "owner", "log", "fixture-api", timestamp, payload, "collection"))

    def snapshot(self):
        tables = ("users", "sessions", "collections", "evidence", "investigations", "sentry_connections", "evidence_arrivals", "arrival_clock")
        added = {'execution_manifest', 'execution_state', 'provider', 'configuration'}
        return {table: sorted([{k: v for k, v in row.items() if k not in added} for row in db.all_rows("SELECT * FROM " + table)], key=repr) for table in tables}

    def test_both_v3_versions_upgrade_and_restart_preserving_history(self):
        for statement in (migrations.ORIGINAL_EVIDENCE_ANALYSIS, migrations.EVIDENCE_ANALYSIS):
            with self.subTest(digest=hashlib.sha256(statement.encode()).hexdigest()):
                with tempfile.TemporaryDirectory() as directory, IsolatedDatabase(Path(directory)):
                    self.historical_database(statement)
                    self.seed_sources()
                    original = self.snapshot()
                    recorded = db.all_rows("SELECT * FROM platform_migrations ORDER BY version")
                    tags = db.all_rows("SELECT * FROM evidence_error_tags WHERE version='signatures-v1' ORDER BY evidence_id,signature")
                    db.init()
                    upgraded_rows = self.snapshot()
                    for table, previous in original.items():
                        self.assertEqual([{k: row[k] for k in old} for row, old in zip(upgraded_rows[table], previous)], previous)
                        self.assertEqual(len(upgraded_rows[table]), len(previous))
                    self.assertEqual(db.all_rows("SELECT * FROM platform_migrations WHERE version<=3 ORDER BY version"), recorded)
                    self.assertEqual(db.all_rows("SELECT * FROM evidence_error_tags WHERE version='signatures-v1' ORDER BY evidence_id,signature"), tags)
                    self.assertEqual(db.one("SELECT MAX(version) version FROM platform_migrations")["version"], migrations.MIGRATIONS[-1][0])
                    indexes = {row["evidence_id"]: row["observed_at"] for row in db.all_rows("SELECT evidence_id,observed_at FROM evidence_index")}
                    self.assertEqual(indexes["aware"], datetime.fromisoformat("2026-11-07T10:00:00.123456+05:30"))
                    self.assertIsNone(indexes["naive"])
                    self.assertIsNone(indexes["invalid"])
                    db.execute("INSERT INTO evidence(id,user_id,kind,service,timestamp,payload,collection_id) VALUES(%s,%s,%s,%s,%s,%s,%s)", ("new-naive", "owner", "log", "fixture-api", "2026-11-07T11:00:00", '{"message":"ENOSPC"}', "collection"))
                    self.assertIsNone(db.one("SELECT observed_at FROM evidence_index WHERE evidence_id='new-naive'")["observed_at"])
                    upgraded = self.snapshot()
                    history = db.all_rows("SELECT * FROM platform_migrations ORDER BY version")
                    db.init()
                    self.assertEqual(self.snapshot(), upgraded)
                    self.assertEqual(db.all_rows("SELECT * FROM platform_migrations ORDER BY version"), history)

    def test_unknown_checksums_still_fail_before_upgrading(self):
        for version in (1, 3):
            with self.subTest(version=version):
                with tempfile.TemporaryDirectory() as directory, IsolatedDatabase(Path(directory)):
                    self.historical_database(migrations.ORIGINAL_EVIDENCE_ANALYSIS)
                    self.seed_sources()
                    before = self.snapshot()
                    db.execute("UPDATE platform_migrations SET digest=%s WHERE version=%s", ("0" * 64, version))
                    with self.assertRaisesRegex(ValueError, f"migration has changed.*version {version}"):
                        db.init()
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(db.one("SELECT MAX(version) version FROM platform_migrations")["version"], 3)

    def test_v6_upgrade_retains_all_automatic_import_generations(self):
        with tempfile.TemporaryDirectory() as directory, IsolatedDatabase(Path(directory)):
            with patch.object(migrations, 'MIGRATIONS', migrations.MIGRATIONS[:6]):
                db.init()
            self.seed_sources()
            with db.connection() as conn:
                for n in range(3):
                    conn.execute("INSERT INTO collections SELECT %s,user_id,title,symptom,filename,source_type,created_at,%s,summary FROM collections WHERE id='collection'", ('snapshot-'+str(n), 'fingerprint-'+str(n)))
                    conn.execute("INSERT INTO sentry_imports(id,user_id,connection_id,collection_id,fingerprint,parameters,state,created_at,retry_of) VALUES(%s,'owner','connection',%s,%s,'{}','interrupted',%s,%s)", ('import-'+str(n), 'snapshot-'+str(n), str(n), db.now(), 'import-'+str(n-1) if n else None))
                conn.execute("INSERT INTO automatic_incidents(id,user_id,connection_id,issue_id,project_id,project_name,title,episode,trigger,triggered_at,received_at,resume_until,collect_after,lifecycle,state,observation) VALUES('incident','owner','connection','123','42','Project','Symptom',1,'created',%s,1,86401,61,'open','interrupted','{}')", (db.now(),))
                conn.execute("INSERT INTO automatic_attempts(id,incident_id,user_id,number,state,created_at,resume_until,import_id) VALUES('attempt','incident','owner',1,'interrupted',%s,86401,'import-2')", (db.now(),))
            original = self.snapshot()
            imports = db.all_rows('SELECT * FROM sentry_imports ORDER BY id')
            for _ in range(2):
                db.init()
                self.assertEqual(self.snapshot(), original)
                self.assertEqual(db.all_rows('SELECT * FROM sentry_imports ORDER BY id'), imports)
                self.assertEqual(db.all_rows('SELECT * FROM automatic_attempt_imports ORDER BY import_id'), [{'attempt_id':'attempt','import_id':'import-'+str(n)} for n in range(3)])


if __name__ == "__main__":
    unittest.main()
