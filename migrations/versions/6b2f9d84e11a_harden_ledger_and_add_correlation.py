"""harden_ledger_and_add_correlation

Revision ID: 6b2f9d84e11a
Revises: 08a270703c92
"""
from alembic import op

revision = "6b2f9d84e11a"
down_revision = "08a270703c92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE outbox_events ADD COLUMN correlation_id TEXT;
        UPDATE outbox_events
        SET correlation_id = 'legacy-' || event_id::text
        WHERE correlation_id IS NULL;
        UPDATE outbox_events
        SET payload = jsonb_set(payload, '{correlation_id}', to_jsonb(correlation_id), true)
        WHERE NOT payload ? 'correlation_id';
        ALTER TABLE outbox_events ALTER COLUMN correlation_id SET NOT NULL;

        ALTER TABLE balances ADD CONSTRAINT balances_nonnegative
            CHECK (balance_minor >= 0);
        ALTER TABLE transactions ADD CONSTRAINT transactions_type_valid
            CHECK (type IN ('deposit', 'transfer', 'reversal', 'settlement'));
        ALTER TABLE transactions ADD CONSTRAINT transactions_state_valid
            CHECK (state = 'posted');

        CREATE UNIQUE INDEX ux_transactions_reversal_once
            ON transactions (reversal_of_id)
            WHERE reversal_of_id IS NOT NULL;
        CREATE INDEX ix_postings_account_transaction
            ON postings (account_id, transaction_id);
        CREATE INDEX ix_transactions_recorded_at
            ON transactions (recorded_at DESC, transaction_id DESC);
        CREATE INDEX ix_outbox_unpublished
            ON outbox_events (created_at, event_id)
            WHERE published_at IS NULL;

        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM transactions t
                LEFT JOIN postings p ON p.transaction_id = t.transaction_id
                GROUP BY t.transaction_id
                HAVING count(p.posting_id) < 2
                   OR COALESCE(sum(p.amount_minor) FILTER (WHERE p.side = 'debit'), 0)
                      <> COALESCE(sum(p.amount_minor) FILTER (WHERE p.side = 'credit'), 0)
            ) THEN
                RAISE EXCEPTION 'cannot enable balance constraint: existing transactions are unbalanced';
            END IF;
        END;
        $$;

        CREATE OR REPLACE FUNCTION enforce_balanced_transaction()
        RETURNS TRIGGER AS $$
        DECLARE
            target_transaction_id UUID;
            posting_count BIGINT;
            debit_total NUMERIC;
            credit_total NUMERIC;
        BEGIN
            target_transaction_id := COALESCE(NEW.transaction_id, OLD.transaction_id);
            SELECT
                count(*),
                COALESCE(sum(amount_minor) FILTER (WHERE side = 'debit'), 0),
                COALESCE(sum(amount_minor) FILTER (WHERE side = 'credit'), 0)
            INTO posting_count, debit_total, credit_total
            FROM postings
            WHERE transaction_id = target_transaction_id;

            IF posting_count < 2 OR debit_total <> credit_total THEN
                RAISE EXCEPTION 'transaction % is not balanced', target_transaction_id
                    USING ERRCODE = '23514';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;

        CREATE CONSTRAINT TRIGGER transactions_must_balance
            AFTER INSERT OR UPDATE ON transactions
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_balanced_transaction();

        CREATE CONSTRAINT TRIGGER postings_must_balance
            AFTER INSERT OR UPDATE OR DELETE ON postings
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_balanced_transaction();
    """)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER IF EXISTS postings_must_balance ON postings;
        DROP TRIGGER IF EXISTS transactions_must_balance ON transactions;
        DROP FUNCTION IF EXISTS enforce_balanced_transaction();
        DROP INDEX IF EXISTS ix_outbox_unpublished;
        DROP INDEX IF EXISTS ix_transactions_recorded_at;
        DROP INDEX IF EXISTS ix_postings_account_transaction;
        DROP INDEX IF EXISTS ux_transactions_reversal_once;
        ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_state_valid;
        ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_type_valid;
        ALTER TABLE balances DROP CONSTRAINT IF EXISTS balances_nonnegative;
        ALTER TABLE outbox_events DROP COLUMN IF EXISTS correlation_id;
    """)
