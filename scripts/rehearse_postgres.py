"""Rehearse the platform schema on the separate Supabase test project only."""
import argparse
import hashlib
import json
import logging
import os
import sys
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import config, postgres
from backend.postgres_migrations import MIGRATIONS, migrate
from psycopg import sql
from psycopg_pool import PoolTimeout


def load_credentials(path):
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for name in ("DESK_DATABASE_URL", "DESK_TEST_DATABASE_URL"):
        if not values.get(name):
            raise ValueError("The private credential file must contain both working and test database URLs.")
    for name in ("DESK_DATABASE_URL", "DESK_TEST_DATABASE_URL", "DESK_DATABASE_POOL_MAX"):
        if name in values:
            os.environ[name] = values[name]


def rehearsal():
    working = os.environ.get("DESK_DATABASE_URL", "")
    if not working or postgres.target_identity(working)[0] != "supabase":
        raise ValueError("Configure the Supabase production URL for identity comparison; production will not be contacted.")
    target = postgres.test_target()
    if postgres.target_identity(target)[0] != "supabase":
        raise ValueError("Configure a separate Supabase test Session pooler URL.")
    schema = "test_supabase_" + os.urandom(12).hex()
    created = False
    result = {"production_contacted": False, "schema": schema, "checks": {}}
    try:
        with postgres.connection(target) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        created = True
        print("Created isolated schema on the test project. Applying all platform migrations.", flush=True)
        with postgres.connection(target, schema) as conn:
            if not conn.pgconn.ssl_in_use:
                raise ValueError("The test connection must use TLS.")
            migrate(conn)
            ledger = conn.execute("SELECT version,digest,applied_at FROM platform_migrations ORDER BY version").fetchall()
            expected = [(version, hashlib.sha256(statement.encode()).hexdigest()) for version, statement in MIGRATIONS]
            if [(row["version"], row["digest"]) for row in ledger] != expected:
                raise ValueError("Migration versions or checksums did not match.")
            conn.execute("CREATE TABLE compatibility_probe(value INTEGER PRIMARY KEY)")
            with conn.pipeline():
                conn.execute("INSERT INTO compatibility_probe VALUES(%s)", (1,))
                conn.execute("INSERT INTO compatibility_probe VALUES(%s)", (2,))
            if conn.execute("SELECT SUM(value) AS total FROM compatibility_probe").fetchone()["total"] != 3:
                raise ValueError("Pipelined transactions did not retain their results.")
        result["checks"].update(verified_tls=True, migrations=True, query_pipelining=True, transactions_commit=True)
        try:
            with postgres.connection(target, schema) as conn:
                conn.execute("INSERT INTO compatibility_probe VALUES(3)")
                raise RuntimeError("intentional rollback")
        except RuntimeError:
            pass
        with postgres.connection(target, schema) as conn:
            if conn.execute("SELECT COUNT(*) AS n FROM compatibility_probe WHERE value=3").fetchone()["n"]:
                raise ValueError("The rolled-back record was retained.")
        result["checks"]["transactions_rollback"] = True
        print("Migrations, TLS and pipelined commit/rollback passed. Checking locks and pool bounds.", flush=True)
        maximum = postgres.pool(target).max_size
        if maximum < 2:
            raise ValueError("The rehearsal requires DESK_DATABASE_POOL_MAX between 2 and 5 for its lock check.")
        with postgres.connection(target, schema) as first:
            postgres.lock(first, "compatibility-probe")
            with postgres.connection(target, schema) as second:
                acquired = second.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema() || ':' || %s,0)) AS acquired", ("compatibility-probe",)).fetchone()["acquired"]
                if acquired:
                    raise ValueError("The advisory lock did not serialize separate connections.")
        with postgres.connection(target, schema) as conn:
            acquired = conn.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema() || ':' || %s,0)) AS acquired", ("compatibility-probe",)).fetchone()["acquired"]
            if not acquired:
                raise ValueError("The completed transaction did not release its advisory lock.")
        result["checks"]["advisory_locks"] = True
        pool = postgres.pool(target)
        with ExitStack() as stack:
            connections = [stack.enter_context(postgres.connection(target, schema)) for _ in range(maximum)]
            if len({conn.info.backend_pid for conn in connections}) != maximum:
                raise ValueError("Session pooler connections did not retain separate database sessions.")
            try:
                with pool.connection(timeout=.2):
                    raise ValueError("The connection pool exceeded its configured maximum.")
            except PoolTimeout:
                pass
        if pool.get_stats()["pool_size"] > 5:
            raise ValueError("The connection pool exceeded five connections.")
        result["checks"]["connection_pool"] = True
        result["pool_max"] = maximum
        postgres.close()
        print("Locks and pool bounds passed. Reconnecting and checking migration idempotency.", flush=True)
        with postgres.connection(target, schema) as conn:
            migrate(conn)
            restarted = conn.execute("SELECT version,digest,applied_at FROM platform_migrations ORDER BY version").fetchall()
            if restarted != ledger:
                raise ValueError("Restart changed the applied migration history.")
            if conn.execute("SELECT SUM(value) AS total FROM compatibility_probe").fetchone()["total"] != 3:
                raise ValueError("Committed records did not survive reconnection.")
            invalid = conn.execute("SELECT COUNT(*) AS n FROM pg_constraint WHERE connamespace=%s::regnamespace AND NOT convalidated", (schema,)).fetchone()["n"]
            if invalid:
                raise ValueError("The isolated schema has unvalidated constraints.")
            result["table_count"] = conn.execute("SELECT COUNT(*) AS n FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r'", (schema,)).fetchone()["n"] - 1
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        result["checks"].update(restart_idempotency=True, constraints=True, project_separation=True)
        result["migration_count"] = len(ledger)
        return result
    finally:
        if created:
            with postgres.connection(target) as conn:
                # The identifier is generated here, never accepted from input.
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            result["test_schema_removed"] = True
        postgres.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Ignored private file containing both Session pooler URLs; only this process uses it.")
    parser.add_argument("--output", type=Path, required=True, help="New local JSON verification manifest.")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new manifest filename to preserve previous results.")
    if args.env_file:
        load_credentials(args.env_file)
    logging.getLogger("psycopg.pool").disabled = True
    result = rehearsal()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit("Test-project rehearsal failed (" + type(error).__name__ + "). Connection details are withheld; inspect project access/configuration and retry with a new manifest.") from None
