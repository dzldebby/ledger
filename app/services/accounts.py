import asyncpg
from app.schemas.accounts import AccountCreate, AccountResponse


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
