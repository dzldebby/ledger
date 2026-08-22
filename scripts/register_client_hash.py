"""Registers an API client from a pre-computed API key hash.

Unlike create_client.py, this never sees the plaintext key, so it is safe to
run somewhere stdout is captured and retained (e.g. as a one-off container in
a Lightsail deployment, where the DB is only reachable from inside AWS).

Generate the key locally, keep it, and pass only its hash here:

    python -c "from app.auth import generate_api_key, hash_api_key; k=generate_api_key(); print(k); print(hash_api_key(k))"

    CLIENT_ID=<id> API_KEY_HASH=<hash> python scripts/register_client_hash.py

Re-running with the same client_id is a no-op, so a restarted container will
not fail or create duplicates.
"""
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def main():
    client_id = os.getenv("CLIENT_ID")
    api_key_hash = os.getenv("API_KEY_HASH")

    if not client_id or not api_key_hash:
        print("CLIENT_ID and API_KEY_HASH must both be set")
        sys.exit(1)

    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO api_clients (client_id, api_key_hash) VALUES (%s, %s)"
                " ON CONFLICT (client_id) DO NOTHING",
                (client_id, api_key_hash),
            )
    conn.close()

    print(f"Client '{client_id}' registered.", flush=True)


if __name__ == "__main__":
    main()
