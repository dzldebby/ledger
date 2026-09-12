import os
import hashlib
import asyncpg
from dotenv import load_dotenv

load_dotenv()

pool: asyncpg.Pool | None = None


async def create_pool() -> None:
    global pool
    pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"))
    client_id = os.getenv("BOOTSTRAP_API_CLIENT_ID")
    api_key = os.getenv("BOOTSTRAP_API_KEY")
    if client_id and api_key:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO api_clients (client_id, api_key_hash)
                VALUES ($1, $2)
                ON CONFLICT (client_id) DO UPDATE
                SET api_key_hash = EXCLUDED.api_key_hash
            """, client_id, hashlib.sha256(api_key.encode()).hexdigest())


async def close_pool() -> None:
    if pool:
        await pool.close()


async def get_conn() -> asyncpg.Connection:
    async with pool.acquire() as conn:
        yield conn
