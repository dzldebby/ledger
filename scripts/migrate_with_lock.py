"""Runs Alembic migrations under a Postgres advisory lock.

Safe to run concurrently from multiple replicas on container startup: only
one holds the lock and actually migrates, the others block until it releases,
then find the database already at head and return immediately.
"""
import os
import sys

import psycopg2
from alembic import command
from alembic.config import Config

ADVISORY_LOCK_KEY = 727271


def main():
    database_url = os.environ["DATABASE_URL"]
    print("Connecting to database...", flush=True)
    conn = psycopg2.connect(database_url, connect_timeout=10)
    print("Connected.", flush=True)
    conn.autocommit = True
    cur = conn.cursor()

    print(f"Acquiring migration advisory lock ({ADVISORY_LOCK_KEY})...", flush=True)
    cur.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
    print("Lock acquired. Running migrations...", flush=True)

    try:
        alembic_ini = os.path.join(os.path.dirname(__file__), "..", "alembic.ini")
        cfg = Config(alembic_ini)
        command.upgrade(cfg, "head")
        print("Migrations complete.", flush=True)
    finally:
        cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        cur.close()
        conn.close()
        print("Lock released.", flush=True)


if __name__ == "__main__":
    main()
