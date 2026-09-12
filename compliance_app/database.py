import os

import asyncpg

pool: asyncpg.Pool | None = None


async def create_pool() -> None:
    global pool
    pool = await asyncpg.create_pool(os.environ["COMPLIANCE_DATABASE_URL"])
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE EXTENSION IF NOT EXISTS "pgcrypto";
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id UUID PRIMARY KEY,
                event_type TEXT NOT NULL,
                transaction_id UUID NOT NULL,
                correlation_id TEXT NOT NULL,
                payload JSONB NOT NULL,
                processed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
            );
            CREATE TABLE IF NOT EXISTS compliance_activity (
                activity_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                event_id UUID NOT NULL REFERENCES processed_events(event_id),
                account_id UUID NOT NULL,
                event_type TEXT NOT NULL,
                amount_minor BIGINT NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_compliance_activity_account_time
                ON compliance_activity (account_id, occurred_at DESC);
            CREATE TABLE IF NOT EXISTS compliance_alerts (
                alert_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                event_id UUID NOT NULL REFERENCES processed_events(event_id),
                transaction_id UUID NOT NULL,
                rule_code TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                details JSONB NOT NULL,
                correlation_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                UNIQUE (event_id, rule_code)
            );
            CREATE TABLE IF NOT EXISTS event_failures (
                delivery_key TEXT PRIMARY KEY,
                event_id TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL,
                payload TEXT NOT NULL,
                dead_lettered_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
            );
        """)


async def close_pool() -> None:
    if pool:
        await pool.close()


async def get_conn():
    async with pool.acquire() as conn:
        yield conn
