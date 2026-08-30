"""Transactional outbox.

Every posted transaction writes exactly one outbox_events row, in the same
database transaction as the postings and the idempotency record. That is what
makes the event trustworthy: it cannot exist for a transaction that rolled
back, and it cannot be missing for one that committed.
"""
import uuid

import pytest

from tests.conftest import db_rows


def idem_headers():
    return {"Idempotency-Key": str(uuid.uuid4())}


def events_for(transaction_id):
    return db_rows(
        "SELECT * FROM outbox_events WHERE transaction_id = %s",
        (transaction_id,),
    )


def _account(client, owner_id, account_type):
    return client.post(
        "/accounts", json={"owner_id": owner_id, "account_type": account_type}
    ).json()


@pytest.fixture
def accounts(client):
    suffix = uuid.uuid4().hex[:8]
    cash = _account(client, f"cash-outbox-{suffix}", "cash")
    alice = _account(client, f"alice-outbox-{suffix}", "customer")
    bob = _account(client, f"bob-outbox-{suffix}", "customer")
    return {
        "cash_account_id": cash["account_id"],
        "account_id": alice["account_id"],
        "to_account_id": bob["account_id"],
    }


def _deposit(client, accounts, amount=10000):
    return client.post("/transactions/deposit", json={
        "account_id": accounts["account_id"],
        "cash_account_id": accounts["cash_account_id"],
        "amount_minor": amount,
    }, headers=idem_headers()).json()


def test_deposit_writes_exactly_one_event(client, accounts):
    txn = _deposit(client, accounts)
    assert len(events_for(txn["transaction_id"])) == 1


def test_transfer_writes_exactly_one_event(client, accounts):
    _deposit(client, accounts)
    txn = client.post("/transactions/transfer", json={
        "from_account_id": accounts["account_id"],
        "to_account_id": accounts["to_account_id"],
        "amount_minor": 2500,
    }, headers=idem_headers()).json()
    assert len(events_for(txn["transaction_id"])) == 1


def test_reversal_writes_its_own_event(client, accounts):
    deposit = _deposit(client, accounts)
    reversal = client.post(
        f"/transactions/{deposit['transaction_id']}/reverse",
        json={"transaction_id": deposit["transaction_id"]},
        headers=idem_headers(),
    ).json()

    assert len(events_for(reversal["transaction_id"])) == 1
    # the original still has exactly its own event, not the reversal's
    assert len(events_for(deposit["transaction_id"])) == 1


def test_event_type_identifies_the_transaction_type(client, accounts):
    txn = _deposit(client, accounts)
    event = events_for(txn["transaction_id"])[0]
    assert event["event_type"] == "transaction.deposit"


def test_payload_carries_the_transaction_detail(client, accounts):
    txn = _deposit(client, accounts, amount=7500)
    # Business detail lives under `data`; the envelope wraps it. The envelope
    # itself is covered in tests/test_event_contract.py.
    data = events_for(txn["transaction_id"])[0]["payload"]["data"]

    assert data["transaction_id"] == txn["transaction_id"]
    assert data["type"] == "deposit"
    assert data["state"] == "posted"
    assert len(data["postings"]) == 2
    assert sum(p["amount"] for p in data["postings"]) == 15000


def test_new_event_is_unpublished(client, accounts):
    txn = _deposit(client, accounts)
    assert events_for(txn["transaction_id"])[0]["published_at"] is None


def test_idempotent_replay_does_not_write_a_second_event(client, accounts):
    headers = idem_headers()
    body = {
        "account_id": accounts["account_id"],
        "cash_account_id": accounts["cash_account_id"],
        "amount_minor": 4200,
    }
    first = client.post("/transactions/deposit", json=body, headers=headers).json()
    replay = client.post("/transactions/deposit", json=body, headers=headers).json()

    assert replay["transaction_id"] == first["transaction_id"]
    assert len(events_for(first["transaction_id"])) == 1


def test_failed_transaction_writes_no_event(client, accounts):
    """The atomicity guarantee: a rolled-back transfer leaves no event behind."""
    before = db_rows("SELECT count(*) AS n FROM outbox_events")[0]["n"]

    response = client.post("/transactions/transfer", json={
        "from_account_id": accounts["account_id"],
        "to_account_id": accounts["to_account_id"],
        "amount_minor": 999999999,
    }, headers=idem_headers())

    assert response.status_code == 400
    after = db_rows("SELECT count(*) AS n FROM outbox_events")[0]["n"]
    assert after == before
