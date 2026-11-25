"""Synthetic operational signatures, additive index migration and source preservation."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from database import IsolatedDatabase
from backend import db, postgres_migrations, signatures, signatures_v1

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/operational_signatures.json').read_text())
V3_DIGEST = '7ebbdc61ef50b39de0e7a39e44f3a8d3b53d1519013bc982c1479bcdb470c194'

class CatalogueTests(unittest.TestCase):
    def test_frozen_migration3_and_v1_catalogue(self):
        self.assertEqual(hashlib.sha256(postgres_migrations.EVIDENCE_ANALYSIS.encode()).hexdigest(), V3_DIGEST)
        self.assertEqual(signatures.CATALOG[:len(signatures_v1.CATALOG)], signatures_v1.CATALOG)
        self.assertEqual(signatures.VERSION, 'signatures-v2')
        self.assertEqual(signatures_v1.VERSION, 'signatures-v1')

    def test_synthetic_positive_mixed_unknown_and_negative_cases(self):
        for case in FIXTURE['cases']:
            with self.subTest(case=case['id']):
                self.assertEqual(set(signatures.detect(case['row'])), set(case['tags']))

class CatalogueMigrationTests(unittest.TestCase):
    def test_v3_upgrade_preserves_sources_reports_ownership_clocks_and_v1_tags(self):
        with tempfile.TemporaryDirectory() as directory, IsolatedDatabase(directory):
            with patch.object(postgres_migrations, 'MIGRATIONS', postgres_migrations.MIGRATIONS[:3]):
                db.init()
            stamp = '2026-11-07T10:00:00.123456+05:30'
            with db.connection() as conn, conn.pipeline():
                conn.execute('INSERT INTO users VALUES(%s,%s,%s,%s,%s)', ('owner','Fixture','fixture@example.invalid','redacted-hash',stamp))
                conn.execute('INSERT INTO collections VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)', ('collection','owner','Synthetic examples','',None,'paste',stamp,'fixture','{}'))
                conn.execute('INSERT INTO investigations(id,collection_id,user_id,engine,state,created_at,report) VALUES(%s,%s,%s,%s,%s,%s,%s)', ('report','collection','owner','groq','completed',stamp,'{ "summary": "retained original" }'))
                for case in FIXTURE['cases']:
                    conn.execute('INSERT INTO evidence(id,user_id,kind,service,timestamp,payload,collection_id) VALUES(%s,%s,%s,%s,%s,%s,%s)', (case['id'],'owner','log','fixture-api',stamp,json.dumps(case['row'],indent=2),'collection'))
            tables = ['evidence','investigations','users','collections','evidence_arrivals','arrival_clock']
            before = {table: db.all_rows('SELECT * FROM ' + table) for table in tables}
            old_tags = db.all_rows("SELECT * FROM evidence_error_tags WHERE version='signatures-v1' ORDER BY evidence_id,signature")
            db.init()
            for table in tables:
                after = db.all_rows('SELECT * FROM ' + table)
                if table == 'investigations':
                    for row in after:
                        self.assertIsNone(row.pop('execution_manifest'))
                        self.assertIsNone(row.pop('execution_state'))
                self.assertEqual(after, before[table], table)
            self.assertEqual(db.all_rows("SELECT * FROM evidence_error_tags WHERE version='signatures-v1' ORDER BY evidence_id,signature"), old_tags)
            self.assertEqual(db.one('SELECT digest FROM platform_migrations WHERE version=3')['digest'], V3_DIGEST)
            for case in FIXTURE['cases']:
                stored = db.all_rows('SELECT signature FROM evidence_error_tags WHERE evidence_id=%s AND version=%s', (case['id'],signatures.VERSION))
                self.assertEqual({item['signature'] for item in stored}, set(signatures.detect(case['row'])), case['id'])
            db.execute('INSERT INTO evidence(id,user_id,kind,service,timestamp,payload,collection_id) VALUES(%s,%s,%s,%s,%s,%s,%s)', ('new-disk','owner','log','fixture-api',stamp,'{"message":"ENOSPC"}','collection'))
            for version, detect in [('signatures-v1',signatures_v1.detect),('signatures-v2',signatures.detect)]:
                tags = db.all_rows('SELECT signature FROM evidence_error_tags WHERE evidence_id=%s AND version=%s', ('new-disk',version))
                self.assertEqual({item['signature'] for item in tags},set(detect({'message':'ENOSPC'})))
            db.init()  # Applied migration digest remains compatible on restart.
