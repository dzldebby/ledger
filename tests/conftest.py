import os
import psycopg2
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from app.main import app
from app.auth import generate_api_key, hash_api_key

load_dotenv()


def provision_api_client(client_id: str) -> str:
    """Inserts (or refreshes) an api_clients row and returns the plaintext key."""
    api_key = generate_api_key()
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO api_clients (client_id, api_key_hash) VALUES (%s, %s)
                ON CONFLICT (client_id) DO UPDATE SET api_key_hash = EXCLUDED.api_key_hash
            """, (client_id, hash_api_key(api_key)))
    conn.close()
    return api_key


@pytest.fixture(scope="session")
def client():
    api_key = provision_api_client("test-client")
    with TestClient(app) as c:
        c.headers["X-API-Key"] = api_key
        yield c
