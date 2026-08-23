import hashlib
import json

import asyncpg
from app.schemas.transactions import DepositCreate, TransferCreate, ReversalCreate, TransactionResponse, PostingResponse


class SameAccountError(Exception):
    pass


class AccountNotFoundError(Exception):
    pass


class InsufficientFundsError(Exception):
    pass


class IdempotencyKeyReuseError(Exception):
    pass


class TransactionNotFoundError(Exception):
    pass


class AlreadyReversedError(Exception):
    pass


class CannotReverseReversalError(Exception):
    pass


def _compute_request_hash(operation: str, data) -> str:
    payload = f"{operation}:{data.model_dump_json()}"
    return hashlib.sha256(payload.encode()).hexdigest()


async def _load_transaction_response(conn: asyncpg.Connection, transaction_id) -> TransactionResponse:
    transaction_row = await conn.fetchrow("""
        SELECT transaction_id, type, state, reversal_of_id FROM transactions WHERE transaction_id = $1
    """, transaction_id)

    posting_rows = await conn.fetch("""
        SELECT account_id, side, amount_minor FROM postings
        WHERE transaction_id = $1 ORDER BY posting_id
    """, transaction_id)

    return TransactionResponse(
        transaction_id=str(transaction_row["transaction_id"]),
        type=transaction_row["type"],
        state=transaction_row["state"],
        reversal_of_id=str(transaction_row["reversal_of_id"]) if transaction_row["reversal_of_id"] else None,
        postings=[
            PostingResponse(account_id=str(p["account_id"]), side=p["side"], amount_minor=p["amount_minor"])
            for p in posting_rows
        ],
    )


async def _run_idempotent(conn: asyncpg.Connection, client_scope: str, idempotency_key: str, operation: str, data, execute_fn) -> TransactionResponse:
    request_hash = _compute_request_hash(operation, data)

    async with conn.transaction():
        existing = await conn.fetchrow("""
            SELECT transaction_id, request_hash FROM idempotency_records
            WHERE client_scope = $1 AND idempotency_key = $2
            FOR UPDATE
        """, client_scope, idempotency_key)

        if existing:
            if existing["request_hash"] != request_hash:
                raise IdempotencyKeyReuseError()
            return await _load_transaction_response(conn, existing["transaction_id"])

        try:
            await conn.execute("""
                INSERT INTO idempotency_records (client_scope, idempotency_key, request_hash, state)
                VALUES ($1, $2, $3, 'processing')
            """, client_scope, idempotency_key, request_hash)
        except asyncpg.exceptions.UniqueViolationError:
            raise IdempotencyKeyReuseError()

        response = await execute_fn(conn)
        await _record_outbox_event(conn, response)

        await conn.execute("""
            UPDATE idempotency_records SET state = 'complete', transaction_id = $1
            WHERE client_scope = $2 AND idempotency_key = $3
        """, response.transaction_id, client_scope, idempotency_key)

    return response


async def _record_outbox_event(conn: asyncpg.Connection, response: TransactionResponse) -> None:
    """Writes the transactional-outbox row for a transaction just posted.

    Called from inside _run_idempotent's transaction, so the event commits
    atomically with the postings, the balance updates and the idempotency
    record. An event therefore cannot exist for a transaction that rolled
    back, and cannot be missing for one that committed - which is what lets a
    downstream consumer trust the outbox as the record of what happened.

    A replayed idempotency key returns the cached response without reaching
    here, so a retry does not produce a duplicate event.
    """
    await conn.execute("""
        INSERT INTO outbox_events (transaction_id, event_type, payload)
        VALUES ($1::uuid, $2, $3::jsonb)
    """, response.transaction_id, f"transaction.{response.type}",
         json.dumps(response.model_dump()))


async def _execute_deposit(conn: asyncpg.Connection, data: DepositCreate) -> TransactionResponse:
    if data.account_id == data.cash_account_id:
        raise SameAccountError()

    # Lock both balance rows in consistent order to avoid deadlocks
    account_ids = sorted([data.account_id, data.cash_account_id])
    await conn.fetch("""
        SELECT account_id FROM balances
        WHERE account_id = ANY($1::uuid[])
        ORDER BY account_id
        FOR UPDATE
    """, account_ids)

    transaction_row = await conn.fetchrow("""
        INSERT INTO transactions (type, state)
        VALUES ('deposit', 'posted')
        RETURNING transaction_id, type, state
    """)
    transaction_id = transaction_row["transaction_id"]

    postings = [
        {"account_id": data.cash_account_id, "side": "debit"},
        {"account_id": data.account_id, "side": "credit"},
    ]

    for posting in postings:
        await conn.execute("""
            INSERT INTO postings (transaction_id, account_id, side, amount_minor)
            VALUES ($1, $2, $3, $4)
        """, transaction_id, posting["account_id"], posting["side"], data.amount_minor)

    # Debit increases cash asset, credit increases customer liability balance
    await conn.execute("""
        UPDATE balances SET balance_minor = balance_minor + $1
        WHERE account_id = $2
    """, data.amount_minor, data.cash_account_id)

    await conn.execute("""
        UPDATE balances SET balance_minor = balance_minor + $1
        WHERE account_id = $2
    """, data.amount_minor, data.account_id)

    return TransactionResponse(
        transaction_id=str(transaction_id),
        type=transaction_row["type"],
        state=transaction_row["state"],
        postings=[
            PostingResponse(account_id=p["account_id"], side=p["side"], amount_minor=data.amount_minor)
            for p in postings
        ],
    )


async def create_deposit(conn: asyncpg.Connection, data: DepositCreate, client_id: str, idempotency_key: str) -> TransactionResponse:
    return await _run_idempotent(conn, client_id, idempotency_key, "deposit", data, lambda c: _execute_deposit(c, data))


async def _execute_transfer(conn: asyncpg.Connection, data: TransferCreate) -> TransactionResponse:
    if data.from_account_id == data.to_account_id:
        raise SameAccountError()

    # Lock both balance rows in consistent order to avoid deadlocks
    account_ids = sorted([data.from_account_id, data.to_account_id])
    rows = await conn.fetch("""
        SELECT account_id, balance_minor FROM balances
        WHERE account_id = ANY($1::uuid[])
        ORDER BY account_id
        FOR UPDATE
    """, account_ids)

    if len(rows) != 2:
        raise AccountNotFoundError()

    balances = {str(row["account_id"]): row["balance_minor"] for row in rows}
    if balances[data.from_account_id] < data.amount_minor:
        raise InsufficientFundsError()

    transaction_row = await conn.fetchrow("""
        INSERT INTO transactions (type, state)
        VALUES ('transfer', 'posted')
        RETURNING transaction_id, type, state
    """)
    transaction_id = transaction_row["transaction_id"]

    postings = [
        {"account_id": data.from_account_id, "side": "debit"},
        {"account_id": data.to_account_id, "side": "credit"},
    ]

    for posting in postings:
        await conn.execute("""
            INSERT INTO postings (transaction_id, account_id, side, amount_minor)
            VALUES ($1, $2, $3, $4)
        """, transaction_id, posting["account_id"], posting["side"], data.amount_minor)

    # Debit decreases the sender's liability balance, credit increases the recipient's
    await conn.execute("""
        UPDATE balances SET balance_minor = balance_minor - $1
        WHERE account_id = $2
    """, data.amount_minor, data.from_account_id)

    await conn.execute("""
        UPDATE balances SET balance_minor = balance_minor + $1
        WHERE account_id = $2
    """, data.amount_minor, data.to_account_id)

    return TransactionResponse(
        transaction_id=str(transaction_id),
        type=transaction_row["type"],
        state=transaction_row["state"],
        postings=[
            PostingResponse(account_id=p["account_id"], side=p["side"], amount_minor=data.amount_minor)
            for p in postings
        ],
    )


async def create_transfer(conn: asyncpg.Connection, data: TransferCreate, client_id: str, idempotency_key: str) -> TransactionResponse:
    return await _run_idempotent(conn, client_id, idempotency_key, "transfer", data, lambda c: _execute_transfer(c, data))


def _original_delta(transaction_type: str, side: str, amount: int) -> int:
    # How the original transaction's posting affected that account's balance.
    if transaction_type == "deposit":
        return amount
    if transaction_type == "transfer":
        return -amount if side == "debit" else amount
    raise CannotReverseReversalError()


async def _execute_reversal(conn: asyncpg.Connection, original_transaction_id: str) -> TransactionResponse:
    original = await conn.fetchrow("""
        SELECT transaction_id, type, state FROM transactions WHERE transaction_id = $1
    """, original_transaction_id)

    if original is None:
        raise TransactionNotFoundError()

    if original["type"] == "reversal":
        raise CannotReverseReversalError()

    already_reversed = await conn.fetchval("""
        SELECT 1 FROM transactions WHERE reversal_of_id = $1
    """, original_transaction_id)

    if already_reversed:
        raise AlreadyReversedError()

    original_postings = await conn.fetch("""
        SELECT account_id, side, amount_minor FROM postings
        WHERE transaction_id = $1 ORDER BY posting_id
    """, original_transaction_id)

    account_ids = sorted({str(p["account_id"]) for p in original_postings})
    balance_rows = await conn.fetch("""
        SELECT account_id, balance_minor FROM balances
        WHERE account_id = ANY($1::uuid[])
        ORDER BY account_id
        FOR UPDATE
    """, account_ids)
    current_balances = {str(row["account_id"]): row["balance_minor"] for row in balance_rows}

    # Reversal undoes the original effect: new_side flips, and the balance
    # delta is the negation of whatever the original posting applied.
    reversal_postings = []
    for posting in original_postings:
        account_id = str(posting["account_id"])
        reversal_delta = -_original_delta(original["type"], posting["side"], posting["amount_minor"])
        if current_balances[account_id] + reversal_delta < 0:
            raise InsufficientFundsError()
        new_side = "credit" if posting["side"] == "debit" else "debit"
        reversal_postings.append({
            "account_id": account_id,
            "side": new_side,
            "amount_minor": posting["amount_minor"],
            "balance_delta": reversal_delta,
        })

    transaction_row = await conn.fetchrow("""
        INSERT INTO transactions (type, state, reversal_of_id)
        VALUES ('reversal', 'posted', $1)
        RETURNING transaction_id, type, state
    """, original_transaction_id)
    transaction_id = transaction_row["transaction_id"]

    for posting in reversal_postings:
        await conn.execute("""
            INSERT INTO postings (transaction_id, account_id, side, amount_minor)
            VALUES ($1, $2, $3, $4)
        """, transaction_id, posting["account_id"], posting["side"], posting["amount_minor"])

        await conn.execute("""
            UPDATE balances SET balance_minor = balance_minor + $1
            WHERE account_id = $2
        """, posting["balance_delta"], posting["account_id"])

    return TransactionResponse(
        transaction_id=str(transaction_id),
        type=transaction_row["type"],
        state=transaction_row["state"],
        reversal_of_id=str(original_transaction_id),
        postings=[
            PostingResponse(account_id=p["account_id"], side=p["side"], amount_minor=p["amount_minor"])
            for p in reversal_postings
        ],
    )


async def create_reversal(conn: asyncpg.Connection, data: ReversalCreate, client_id: str, idempotency_key: str) -> TransactionResponse:
    return await _run_idempotent(conn, client_id, idempotency_key, "reversal", data, lambda c: _execute_reversal(c, data.transaction_id))
