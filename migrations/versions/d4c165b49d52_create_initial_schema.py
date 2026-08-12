"""create_initial_schema

Revision ID: d4c165b49d52
Revises:
Create Date: 2026-08-13

"""
from alembic import op

revision = 'd4c165b49d52'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE EXTENSION IF NOT EXISTS "pgcrypto";

        CREATE TABLE accounts (
            account_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id     TEXT        NOT NULL,
            account_type TEXT        NOT NULL,
            status       TEXT        NOT NULL DEFAULT 'active',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE balances (
            account_id    UUID   PRIMARY KEY REFERENCES accounts(account_id),
            balance_minor BIGINT NOT NULL DEFAULT 0
        );

        CREATE TABLE transactions (
            transaction_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            type           TEXT        NOT NULL,
            state          TEXT        NOT NULL DEFAULT 'posted',
            reversal_of_id UUID        REFERENCES transactions(transaction_id),
            recorded_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE postings (
            posting_id     UUID   PRIMARY KEY DEFAULT gen_random_uuid(),
            transaction_id UUID   NOT NULL REFERENCES transactions(transaction_id),
            account_id     UUID   NOT NULL REFERENCES accounts(account_id),
            side           TEXT   NOT NULL CHECK (side IN ('debit', 'credit')),
            amount_minor   BIGINT NOT NULL CHECK (amount_minor > 0)
        );

        CREATE TABLE idempotency_records (
            client_scope     TEXT        NOT NULL,
            idempotency_key  TEXT        NOT NULL,
            request_hash     TEXT        NOT NULL,
            transaction_id   UUID        REFERENCES transactions(transaction_id),
            state            TEXT        NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (client_scope, idempotency_key)
        );

        CREATE TABLE outbox_events (
            event_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            transaction_id UUID        NOT NULL REFERENCES transactions(transaction_id),
            event_type     TEXT        NOT NULL,
            payload        JSONB       NOT NULL,
            traceparent    TEXT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            published_at   TIMESTAMPTZ
        );

        CREATE TABLE holds (
            hold_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id   UUID        NOT NULL REFERENCES accounts(account_id),
            amount_minor BIGINT      NOT NULL CHECK (amount_minor > 0),
            status       TEXT        NOT NULL DEFAULT 'active',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE settlements (
            settlement_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id         UUID        NOT NULL REFERENCES accounts(account_id),
            amount_minor       BIGINT      NOT NULL,
            status             TEXT        NOT NULL DEFAULT 'pending',
            external_reference TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS settlements;
        DROP TABLE IF EXISTS holds;
        DROP TABLE IF EXISTS outbox_events;
        DROP TABLE IF EXISTS idempotency_records;
        DROP TABLE IF EXISTS postings;
        DROP TABLE IF EXISTS transactions;
        DROP TABLE IF EXISTS balances;
        DROP TABLE IF EXISTS accounts;
    """)
