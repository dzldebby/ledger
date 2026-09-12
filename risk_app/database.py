import os

import asyncpg

pool: asyncpg.Pool | None = None


async def create_pool() -> None:
    global pool
    pool = await asyncpg.create_pool(os.environ["RISK_DATABASE_URL"])
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE EXTENSION IF NOT EXISTS "pgcrypto";
            CREATE TABLE IF NOT EXISTS risk_decisions (
                decision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                client_scope TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                transaction_type TEXT NOT NULL,
                amount_minor BIGINT NOT NULL,
                owner_ids JSONB NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('approved', 'declined', 'review')),
                reasons JSONB NOT NULL,
                correlation_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                expires_at TIMESTAMPTZ NOT NULL,
                UNIQUE (client_scope, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_risk_decisions_client_created
                ON risk_decisions (client_scope, created_at DESC);
        """)


async def close_pool() -> None:
    if pool:
        await pool.close()


async def get_conn():
    async with pool.acquire() as conn:
        yield conn
