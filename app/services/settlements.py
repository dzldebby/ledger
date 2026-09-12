from datetime import datetime, timedelta, timezone

import asyncpg
from opentelemetry import metrics, trace

from app.schemas.settlements import (
    SettlementBatchResponse,
    SettlementConfirmationResponse,
    SettlementPrepare,
    SettlementResponse,
)
from app.schemas.transactions import PostingResponse, TransactionResponse
from app.services.transactions import AccountNotFoundError, InsufficientFundsError, _record_outbox_event

tracer = trace.get_tracer("ledger-api")
settlement_counter = metrics.get_meter("ledger-api").create_counter("ledger.settlements")


class SettlementNotFoundError(Exception):
    pass


class SettlementStateError(Exception):
    pass


def _settlement(row) -> SettlementResponse:
    return SettlementResponse(
        settlement_id=str(row["settlement_id"]),
        account_id=str(row["account_id"]),
        external_bank_account_id=str(row["external_bank_account_id"]),
        amount_minor=row["amount_minor"],
        status=row["status"],
        external_reference=row["external_reference"],
        correlation_id=row["correlation_id"],
        effective_at=row["effective_at"],
        confirmed_at=row["confirmed_at"],
        transaction_id=str(row["transaction_id"]) if row["transaction_id"] else None,
    )


async def prepare_settlements(
    conn: asyncpg.Connection,
    data: SettlementPrepare,
    correlation_id: str,
) -> SettlementBatchResponse:
    with tracer.start_as_current_span("ledger.settlement.prepare"):
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"settlement:{data.batch_id}",
            )
            existing = await conn.fetch(
                "SELECT * FROM settlements WHERE batch_id = $1 ORDER BY account_id",
                data.batch_id,
            )
            if existing:
                return SettlementBatchResponse(
                    settlements=[_settlement(row) for row in existing],
                    count=len(existing),
                )

            external_type = await conn.fetchval(
                "SELECT account_type FROM accounts WHERE account_id = $1",
                data.external_bank_account_id,
            )
            if external_type != "external_bank":
                raise AccountNotFoundError()

            rows = await conn.fetch("""
                SELECT a.account_id, b.balance_minor
                FROM accounts a
                JOIN balances b ON b.account_id = a.account_id
                WHERE a.account_type = 'business'
                  AND a.status = 'active'
                  AND b.balance_minor > 0
                  AND NOT EXISTS (
                      SELECT 1 FROM holds h
                      WHERE h.account_id = a.account_id AND h.status = 'active'
                  )
                ORDER BY a.account_id
                FOR UPDATE OF b
            """)

            settlements = []
            for account in rows:
                reference = f"{data.batch_id}:{account['account_id']}"
                settlement = await conn.fetchrow("""
                    INSERT INTO settlements (
                        account_id, external_bank_account_id, amount_minor, status,
                        external_reference, batch_id, correlation_id
                    ) VALUES ($1, $2, $3, 'pending', $4, $5, $6)
                    ON CONFLICT (external_reference) WHERE external_reference IS NOT NULL
                    DO NOTHING
                    RETURNING *
                """, account["account_id"], data.external_bank_account_id,
                     account["balance_minor"], reference, data.batch_id, correlation_id)
                if settlement is None:
                    settlement = await conn.fetchrow(
                        "SELECT * FROM settlements WHERE external_reference = $1", reference
                    )
                else:
                    await conn.execute("""
                        INSERT INTO holds (
                            account_id, amount_minor, status, settlement_id, expires_at
                        ) VALUES ($1, $2, 'active', $3, $4)
                    """, account["account_id"], account["balance_minor"],
                         settlement["settlement_id"],
                         datetime.now(timezone.utc) + timedelta(days=1))
                    settlement_counter.add(1, {"outcome": "prepared"})
                settlements.append(_settlement(settlement))

        return SettlementBatchResponse(settlements=settlements, count=len(settlements))


async def confirm_settlement(
    conn: asyncpg.Connection,
    settlement_id: str,
    external_reference: str,
    correlation_id: str,
) -> SettlementConfirmationResponse:
    with tracer.start_as_current_span("ledger.settlement.confirm"):
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM settlements WHERE settlement_id = $1 FOR UPDATE",
                settlement_id,
            )
            if row is None:
                raise SettlementNotFoundError()
            if row["status"] == "confirmed":
                transaction = await _load_settlement_transaction(conn, row["transaction_id"])
                return SettlementConfirmationResponse(
                    settlement=_settlement(row), transaction=transaction
                )
            if row["status"] != "pending":
                raise SettlementStateError()

            account_ids = sorted([
                str(row["account_id"]), str(row["external_bank_account_id"])
            ])
            balances = await conn.fetch("""
                SELECT account_id, balance_minor FROM balances
                WHERE account_id = ANY($1::uuid[])
                ORDER BY account_id FOR UPDATE
            """, account_ids)
            current = {str(balance["account_id"]): balance["balance_minor"] for balance in balances}
            if current[str(row["account_id"])] < row["amount_minor"]:
                raise InsufficientFundsError()

            transaction_row = await conn.fetchrow("""
                INSERT INTO transactions (type, state)
                VALUES ('settlement', 'posted')
                RETURNING transaction_id, type, state
            """)
            postings = [
                {"account_id": str(row["account_id"]), "side": "debit"},
                {"account_id": str(row["external_bank_account_id"]), "side": "credit"},
            ]
            for posting in postings:
                await conn.execute("""
                    INSERT INTO postings (transaction_id, account_id, side, amount_minor)
                    VALUES ($1, $2, $3, $4)
                """, transaction_row["transaction_id"], posting["account_id"],
                     posting["side"], row["amount_minor"])

            await conn.execute(
                "UPDATE balances SET balance_minor = balance_minor - $1 WHERE account_id = $2",
                row["amount_minor"], row["account_id"],
            )
            await conn.execute(
                "UPDATE balances SET balance_minor = balance_minor + $1 WHERE account_id = $2",
                row["amount_minor"], row["external_bank_account_id"],
            )
            transaction = TransactionResponse(
                transaction_id=str(transaction_row["transaction_id"]),
                type="settlement",
                state="posted",
                postings=[
                    PostingResponse(
                        account_id=posting["account_id"],
                        side=posting["side"],
                        amount_minor=row["amount_minor"],
                    )
                    for posting in postings
                ],
            )
            await _record_outbox_event(conn, transaction, correlation_id=correlation_id)
            updated = await conn.fetchrow("""
                UPDATE settlements
                SET status = 'confirmed', external_reference = $2,
                    transaction_id = $3, confirmed_at = clock_timestamp()
                WHERE settlement_id = $1
                RETURNING *
            """, settlement_id, external_reference, transaction_row["transaction_id"])
            await conn.execute(
                "UPDATE holds SET status = 'settled' WHERE settlement_id = $1",
                settlement_id,
            )
            settlement_counter.add(1, {"outcome": "confirmed"})
            return SettlementConfirmationResponse(
                settlement=_settlement(updated), transaction=transaction
            )


async def _load_settlement_transaction(conn, transaction_id) -> TransactionResponse:
    row = await conn.fetchrow(
        "SELECT transaction_id, type, state FROM transactions WHERE transaction_id = $1",
        transaction_id,
    )
    postings = await conn.fetch("""
        SELECT account_id, side, amount_minor FROM postings
        WHERE transaction_id = $1 ORDER BY posting_id
    """, transaction_id)
    return TransactionResponse(
        transaction_id=str(row["transaction_id"]),
        type=row["type"],
        state=row["state"],
        postings=[
            PostingResponse(
                account_id=str(posting["account_id"]),
                side=posting["side"],
                amount_minor=posting["amount_minor"],
            )
            for posting in postings
        ],
    )


async def fail_settlement(conn, settlement_id: str, reason: str) -> SettlementResponse:
    async with conn.transaction():
        row = await conn.fetchrow("""
            UPDATE settlements
            SET status = 'failed', external_reference = $2
            WHERE settlement_id = $1 AND status = 'pending'
            RETURNING *
        """, settlement_id, f"failed:{settlement_id}:{reason}")
        if row is None:
            exists = await conn.fetchval(
                "SELECT 1 FROM settlements WHERE settlement_id = $1", settlement_id
            )
            if not exists:
                raise SettlementNotFoundError()
            raise SettlementStateError()
        await conn.execute(
            "UPDATE holds SET status = 'released' WHERE settlement_id = $1",
            settlement_id,
        )
        settlement_counter.add(1, {"outcome": "failed"})
        return _settlement(row)


async def list_settlements(conn, limit: int = 100) -> list[SettlementResponse]:
    rows = await conn.fetch(
        "SELECT * FROM settlements ORDER BY created_at DESC LIMIT $1", limit
    )
    return [_settlement(row) for row in rows]
