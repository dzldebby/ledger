import hashlib
import os
import secrets
import time
import asyncpg
from fastapi import Header, HTTPException, Depends
from app.database import get_conn

RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "1000"))
RATE_LIMIT_WINDOW_SECONDS = 60

# In-memory only. Correct for a single running instance; a multi-instance
# deployment behind a load balancer needs shared state (e.g. Redis), since
# each instance would otherwise track its own independent counters.
_rate_limit_state: dict[str, tuple[float, int]] = {}


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


async def _authenticate(conn: asyncpg.Connection, api_key: str) -> str | None:
    row = await conn.fetchrow("""
        SELECT client_id FROM api_clients WHERE api_key_hash = $1
    """, hash_api_key(api_key))
    return row["client_id"] if row else None


def _check_rate_limit(client_id: str) -> None:
    now = time.time()
    window_start, count = _rate_limit_state.get(client_id, (now, 0))

    if now - window_start >= RATE_LIMIT_WINDOW_SECONDS:
        window_start, count = now, 0

    count += 1
    _rate_limit_state[client_id] = (window_start, count)

    if count > RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


async def get_authenticated_client(
    x_api_key: str = Header(..., alias="X-API-Key"),
    conn: asyncpg.Connection = Depends(get_conn),
) -> str:
    client_id = await _authenticate(conn, x_api_key)
    if client_id is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    _check_rate_limit(client_id)
    return client_id
