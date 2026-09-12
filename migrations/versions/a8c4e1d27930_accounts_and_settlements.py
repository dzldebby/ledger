"""accounts_and_settlements

Revision ID: a8c4e1d27930
Revises: 6b2f9d84e11a
"""
from alembic import op

revision = "a8c4e1d27930"
down_revision = "6b2f9d84e11a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE accounts ADD COLUMN currency TEXT NOT NULL DEFAULT 'USD';
        ALTER TABLE accounts ADD CONSTRAINT accounts_currency_format
            CHECK (currency ~ '^[A-Z]{3}$');
        ALTER TABLE accounts ADD CONSTRAINT accounts_type_valid
            CHECK (account_type IN ('cash', 'personal', 'customer', 'business', 'external_bank'));

        ALTER TABLE settlements
            ADD COLUMN external_bank_account_id UUID REFERENCES accounts(account_id),
            ADD COLUMN transaction_id UUID REFERENCES transactions(transaction_id),
            ADD COLUMN batch_id TEXT,
            ADD COLUMN correlation_id TEXT,
            ADD COLUMN effective_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            ADD COLUMN confirmed_at TIMESTAMPTZ;

        ALTER TABLE holds
            ADD COLUMN settlement_id UUID REFERENCES settlements(settlement_id),
            ADD COLUMN expires_at TIMESTAMPTZ;

        DO $$
        DECLARE
            legacy_bank_id UUID;
        BEGIN
            IF EXISTS (
                SELECT 1 FROM settlements
                WHERE external_bank_account_id IS NULL
            ) THEN
                INSERT INTO accounts (owner_id, account_type, currency)
                VALUES ('legacy-settlement-bank', 'external_bank', 'USD')
                RETURNING account_id INTO legacy_bank_id;
                INSERT INTO balances (account_id) VALUES (legacy_bank_id);

                UPDATE settlements
                SET external_bank_account_id = legacy_bank_id,
                    batch_id = COALESCE(batch_id, 'legacy-' || settlement_id::text),
                    correlation_id = COALESCE(
                        correlation_id, 'legacy-' || settlement_id::text
                    ),
                    external_reference = COALESCE(
                        external_reference, 'legacy-' || settlement_id::text
                    );
            END IF;
        END;
        $$;

        ALTER TABLE settlements
            ALTER COLUMN external_bank_account_id SET NOT NULL,
            ALTER COLUMN batch_id SET NOT NULL,
            ALTER COLUMN correlation_id SET NOT NULL,
            ALTER COLUMN external_reference SET NOT NULL;
        ALTER TABLE settlements ADD CONSTRAINT settlements_amount_positive
            CHECK (amount_minor > 0);
        ALTER TABLE settlements ADD CONSTRAINT settlements_status_valid
            CHECK (status IN ('pending', 'confirmed', 'failed'));
        ALTER TABLE holds ADD CONSTRAINT holds_status_valid
            CHECK (status IN ('active', 'settled', 'released'));

        CREATE UNIQUE INDEX ux_holds_active_settlement
            ON holds (settlement_id) WHERE settlement_id IS NOT NULL;
        CREATE INDEX ix_holds_account_active
            ON holds (account_id) WHERE status = 'active';
        CREATE INDEX ix_settlements_status_created
            ON settlements (status, created_at);
        CREATE UNIQUE INDEX ux_settlements_batch_account
            ON settlements (batch_id, account_id);
        CREATE UNIQUE INDEX ux_settlements_external_reference
            ON settlements (external_reference) WHERE external_reference IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS ux_settlements_external_reference;
        DROP INDEX IF EXISTS ux_settlements_batch_account;
        DROP INDEX IF EXISTS ix_settlements_status_created;
        DROP INDEX IF EXISTS ix_holds_account_active;
        DROP INDEX IF EXISTS ux_holds_active_settlement;
        ALTER TABLE holds DROP CONSTRAINT IF EXISTS holds_status_valid;
        ALTER TABLE settlements DROP CONSTRAINT IF EXISTS settlements_status_valid;
        ALTER TABLE settlements DROP CONSTRAINT IF EXISTS settlements_amount_positive;
        ALTER TABLE holds DROP COLUMN IF EXISTS expires_at;
        ALTER TABLE holds DROP COLUMN IF EXISTS settlement_id;
        ALTER TABLE settlements DROP COLUMN IF EXISTS confirmed_at;
        ALTER TABLE settlements DROP COLUMN IF EXISTS effective_at;
        ALTER TABLE settlements DROP COLUMN IF EXISTS correlation_id;
        ALTER TABLE settlements DROP COLUMN IF EXISTS transaction_id;
        ALTER TABLE settlements DROP COLUMN IF EXISTS batch_id;
        ALTER TABLE settlements DROP COLUMN IF EXISTS external_bank_account_id;
        ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_type_valid;
        ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_currency_format;
        ALTER TABLE accounts DROP COLUMN IF EXISTS currency;
    """)
