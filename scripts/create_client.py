"""Provisions a new API client. Run manually; there is no HTTP endpoint for this.

Usage:
    python scripts/create_client.py <client_id>
"""
import os
import sys

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.auth import generate_api_key, hash_api_key

load_dotenv()


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/create_client.py <client_id>")
        sys.exit(1)

    client_id = sys.argv[1]
    api_key = generate_api_key()

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO api_clients (client_id, api_key_hash) VALUES (%s, %s)",
                (client_id, hash_api_key(api_key)),
            )
    conn.close()

    print(f"Client '{client_id}' created.")
    print(f"API key (save this now, it will not be shown again): {api_key}")


if __name__ == "__main__":
    main()
