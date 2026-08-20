import asyncpg
from app.schemas.accounts import AccountCreate, AccountResponse, BalanceResponse


async def create_account(conn: asyncpg.Connection, data: AccountCreate) -> AccountResponse:
    row = await conn.fetchrow("""
        WITH new_account AS (
            INSERT INTO accounts (owner_id, account_type)
            VALUES ($1, $2)
            RETURNING account_id, owner_id, account_type, status
        ),
        _ AS (
            INSERT INTO balances (account_id, balance_minor)
            SELECT account_id, 0 FROM new_account
        )
        SELECT account_id, owner_id, account_type, status FROM new_account
    """, data.owner_id, data.account_type)

    return AccountResponse(
        account_id=str(row["account_id"]),
        owner_id=row["owner_id"],
        account_type=row["account_type"],
        status=row["status"],
    )


async def list_accounts(conn: asyncpg.Connection) -> list[AccountResponse]:
    rows = await conn.fetch("""
        SELECT account_id, owner_id, account_type, status FROM accounts
        ORDER BY created_at DESC
    """)

    return [
        AccountResponse(
            account_id=str(row["account_id"]),
            owner_id=row["owner_id"],
            account_type=row["account_type"],
            status=row["status"],
        )
        for row in rows
    ]


async def get_balance(conn: asyncpg.Connection, account_id: str) -> BalanceResponse | None:
    row = await conn.fetchrow("""
        SELECT account_id, balance_minor FROM balances
        WHERE account_id = $1
    """, account_id)

    if row is None:
        return None

    return BalanceResponse(
        account_id=str(row["account_id"]),
        balance_minor=row["balance_minor"],
    )
