import os
from dotenv import load_dotenv

load_dotenv()

DEV_DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://ledger:ledger@localhost:5432/ledger")
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql://ledger:ledger@localhost:5432/ledger_test")

# Must happen before `app.main` is imported: app/database.py reads DATABASE_URL
# from the environment when the app's lifespan starts. Overriding it here
# points every test run at an isolated database instead of the dev one.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import psycopg2
import psycopg2.errors
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.main import app
from app.auth import generate_api_key, hash_api_key


def _ensure_test_database_exists():
    admin_conn = psycopg2.connect(DEV_DATABASE_URL)
    admin_conn.autocommit = True
    try:
        with admin_conn.cursor() as cur:
            cur.execute("CREATE DATABASE ledger_test")
    except psycopg2.errors.DuplicateDatabase:
        pass
    finally:
        admin_conn.close()


def _run_migrations():
    alembic_ini = os.path.join(os.path.dirname(__file__), "..", "alembic.ini")
    cfg = Config(alembic_ini)
    command.upgrade(cfg, "head")


_ensure_test_database_exists()
_run_migrations()


def provision_api_client(client_id: str) -> str:
    """Inserts (or refreshes) an api_clients row and returns the plaintext key."""
    api_key = generate_api_key()
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO api_clients (client_id, api_key_hash) VALUES (%s, %s)
                ON CONFLICT (client_id) DO UPDATE SET api_key_hash = EXCLUDED.api_key_hash
            """, (client_id, hash_api_key(api_key)))
    conn.close()
    return api_key


def db_rows(sql, params=None):
    """Runs a read query against the test database and returns dict rows.

    Lets a test assert on state the API does not expose, such as
    outbox_events.
    """
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            columns = [d[0] for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


@pytest.fixture(scope="session")
def client():
    api_key = provision_api_client("test-client")
    with TestClient(app) as c:
        c.headers["X-API-Key"] = api_key
        yield c
