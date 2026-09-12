import asyncpg
from app.schemas.accounts import (
    AccountCreate,
    AccountHistoryResponse,
    AccountPosting,
    AccountResponse,
    AccountTransaction,
    BalanceResponse,
)


async def create_account(conn: asyncpg.Connection, data: AccountCreate) -> AccountResponse:
    row = await conn.fetchrow("""
        WITH new_account AS (
            INSERT INTO accounts (owner_id, account_type, currency)
            VALUES ($1, $2, $3)
            RETURNING account_id, owner_id, account_type, currency, status
        ),
        _ AS (
            INSERT INTO balances (account_id, balance_minor)
            SELECT account_id, 0 FROM new_account
        )
        SELECT account_id, owner_id, account_type, currency, status FROM new_account
    """, data.owner_id, data.account_type, data.currency.upper())

    return AccountResponse(
        account_id=str(row["account_id"]),
        owner_id=row["owner_id"],
        account_type=row["account_type"],
        currency=row["currency"],
        status=row["status"],
    )


async def list_accounts(conn: asyncpg.Connection) -> list[AccountResponse]:
    rows = await conn.fetch("""
        SELECT account_id, owner_id, account_type, currency, status FROM accounts
        ORDER BY created_at DESC
    """)

    return [
        AccountResponse(
            account_id=str(row["account_id"]),
            owner_id=row["owner_id"],
            account_type=row["account_type"],
            currency=row["currency"],
            status=row["status"],
        )
        for row in rows
    ]


async def get_balance(conn: asyncpg.Connection, account_id: str) -> BalanceResponse | None:
    row = await conn.fetchrow("""
        SELECT b.account_id, b.balance_minor,
               b.balance_minor - COALESCE(sum(h.amount_minor)
                   FILTER (WHERE h.status = 'active'), 0) AS available_balance_minor
        FROM balances b
        LEFT JOIN holds h ON h.account_id = b.account_id
        WHERE b.account_id = $1
        GROUP BY b.account_id, b.balance_minor
    """, account_id)

    if row is None:
        return None

    return BalanceResponse(
        account_id=str(row["account_id"]),
        balance_minor=row["balance_minor"],
        available_balance_minor=row["available_balance_minor"],
    )


async def get_history(
    conn: asyncpg.Connection, account_id: str, limit: int = 100
) -> AccountHistoryResponse | None:
    exists = await conn.fetchval(
        "SELECT 1 FROM accounts WHERE account_id = $1", account_id
    )
    if not exists:
        return None

    rows = await conn.fetch("""
        SELECT t.transaction_id, t.type, t.state, t.reversal_of_id,
               t.recorded_at, p.side, p.amount_minor
        FROM postings p
        JOIN transactions t ON t.transaction_id = p.transaction_id
        WHERE p.account_id = $1
        ORDER BY t.recorded_at DESC, t.transaction_id DESC
        LIMIT $2
    """, account_id, limit)
    transactions = [
        AccountTransaction(
            transaction_id=str(row["transaction_id"]),
            type=row["type"],
            state=row["state"],
            reversal_of_id=(str(row["reversal_of_id"]) if row["reversal_of_id"] else None),
            recorded_at=row["recorded_at"],
            posting=AccountPosting(side=row["side"], amount_minor=row["amount_minor"]),
        )
        for row in rows
    ]
    return AccountHistoryResponse(
        account_id=account_id,
        transactions=transactions,
        count=len(transactions),
    )
