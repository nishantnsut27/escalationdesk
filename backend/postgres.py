"""Bounded PostgreSQL access; storage targets are always explicit."""
import hashlib
import os
import re
import threading
import atexit
from contextlib import contextmanager
from pathlib import Path

import psycopg
import certifi
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool

SUPABASE_CA = Path(__file__).resolve().parent / "certs" / "supabase-root-2021.crt"


class Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


def connection_info(value):
    if not value:
        raise ValueError("Configure DESK_DATABASE_URL for PostgreSQL; SQLite fallback is disabled.")
    try:
        options = conninfo_to_dict(value)
        host = options.get("host", "")
        if not host or options.get("hostaddr") or "," in host:
            raise ValueError()
        host = host.lower().rstrip(".")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            raise ValueError()
        options["host"] = host
        root_certificate = certifi.where()
        if host.endswith(".pooler.supabase.com"):
            if options.get("port", "5432") != "5432":
                raise ValueError("Use the Supabase Session pooler on port 5432.")
            supabase_project(options)
            # The shared pooler's provider CA is published by Supabase's
            # dashboard, rather than included in Mozilla's public CA bundle.
            if not SUPABASE_CA.is_file():
                raise ValueError()
            root_certificate = str(SUPABASE_CA)
        elif host.endswith(".supabase.co"):
            raise ValueError("Use the Supabase Session pooler hostname on port 5432.")
        if options.get("sslmode", "verify-full") not in {"require", "verify-full"}:
            raise ValueError()
        # Provider URLs may request encryption alone; verify the server too.
        options.update(sslmode="verify-full", sslrootcert=root_certificate, connect_timeout="10", application_name="tracedesk",
                       keepalives="1", keepalives_idle="15", keepalives_interval="5", keepalives_count="3", tcp_user_timeout="20000")
        return make_conninfo(**options)
    except (psycopg.Error, ValueError):
        raise ValueError("Use a single PostgreSQL DNS hostname with verified TLS and percent-encode reserved password characters; Supabase requires its Session pooler on port 5432 and a project-qualified username.") from None


def supabase_project(options):
    role, separator, project = options.get("user", "").rpartition(".")
    if not role or not separator or not re.fullmatch(r"[a-z0-9]+", project):
        raise ValueError("The Supabase pooler username must include its project reference.")
    return project


def target_identity(value):
    options = conninfo_to_dict(connection_info(value))
    host = options["host"].lower().rstrip(".")
    if host.endswith(".pooler.supabase.com"):
        # Shared poolers route by the username's project suffix, including when
        # a project is reached through another region/hostname or database role.
        return ("supabase", supabase_project(options))
    return ("postgresql", host.replace("-pooler.", "."))


def test_target():
    from . import config
    target = os.environ.get("DESK_TEST_DATABASE_URL", "")
    if not target:
        raise ValueError("DESK_TEST_DATABASE_URL is required for isolated PostgreSQL integration tests.")
    working = os.environ.get("DESK_DATABASE_URL", "")
    if working and target_identity(target) == target_identity(working):
        raise ValueError("The test database must be on a separate PostgreSQL project/endpoint from the working database.")
    return connection_info(target)


_pools = {}
_mutex = threading.Lock()


def pool(target):
    info = connection_info(target)
    key = hashlib.sha256(info.encode()).hexdigest()
    with _mutex:
        if key not in _pools:
            try:
                maximum = int(os.environ.get("DESK_DATABASE_POOL_MAX", "5"))
            except ValueError:
                raise ValueError("DESK_DATABASE_POOL_MAX must be between 1 and 5.") from None
            if not 1 <= maximum <= 5:
                raise ValueError("DESK_DATABASE_POOL_MAX must be between 1 and 5.")
            # Startup and exhaustion fail within a bounded wait. No driver logger
            # receives connection credentials or SQL parameters.
            value = ConnectionPool(info, min_size=0, max_size=maximum, max_waiting=20,
                                   timeout=15, max_idle=60, max_lifetime=900,
                                   check=ConnectionPool.check_connection,
                                   kwargs={"row_factory": row_factory, "autocommit": True}, open=True)
            _pools[key] = value
        return _pools[key]


@contextmanager
def connection(target, schema="public"):
    if not re.fullmatch(r"public|(?:test|operator)_[a-z0-9_]{1,60}", schema):
        raise ValueError("Invalid isolated database schema.")
    with pool(target).connection() as conn:
        with conn.transaction():
            # Finish pipelining setup before callers fetch potentially large
            # result sets through a PostgreSQL proxy. Bulk executemany still batches.
            with conn.pipeline():
                conn.execute(sql.SQL("SET LOCAL search_path TO {} ").format(sql.Identifier(schema)))
                conn.execute("SET LOCAL statement_timeout = '15s'")
                conn.execute("SET LOCAL lock_timeout = '10s'")
            yield conn


def lock(conn, name="platform-writes"):
    # Preserve check-and-insert serialization across processes, not just tasks.
    # Independent test/operator schemas must not block each other's workflows.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':' || %s, 0))", (name,))


def close():
    with _mutex:
        for value in _pools.values():
            value.close()
        _pools.clear()


atexit.register(close)
