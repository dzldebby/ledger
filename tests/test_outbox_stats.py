"""The outbox stats endpoint.

Read-only aggregate counts, so the outbox can be observed in a deployment
where the database is private and unreachable from a laptop.
"""
import uuid

import pytest


def idem_headers():
    return {"Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture
def accounts(client):
    suffix = uuid.uuid4().hex[:8]
    cash = client.post("/accounts", json={
        "owner_id": f"cash-stats-{suffix}", "account_type": "cash"}).json()
    customer = client.post("/accounts", json={
        "owner_id": f"cust-stats-{suffix}", "account_type": "customer"}).json()
    return {"cash_account_id": cash["account_id"],
            "account_id": customer["account_id"]}


def test_stats_requires_an_api_key(client):
    # the client fixture is session-scoped, so the key must be captured before
    # removing it and restored afterwards or every later test loses auth
    key = client.headers["X-API-Key"]
    try:
        del client.headers["X-API-Key"]
        assert client.get("/admin/outbox/stats").status_code == 422
    finally:
        client.headers["X-API-Key"] = key


def test_stats_returns_the_expected_shape(client):
    body = client.get("/admin/outbox/stats").json()
    assert set(body) == {"total_events", "unpublished_events",
                         "events_by_type", "newest_event_at"}
    assert isinstance(body["total_events"], int)
    assert isinstance(body["events_by_type"], dict)


def test_a_deposit_increments_the_totals(client, accounts):
    before = client.get("/admin/outbox/stats").json()

    client.post("/transactions/deposit", json={
        "account_id": accounts["account_id"],
        "cash_account_id": accounts["cash_account_id"],
        "amount_minor": 5000,
    }, headers=idem_headers())

    after = client.get("/admin/outbox/stats").json()
    assert after["total_events"] == before["total_events"] + 1
    assert after["unpublished_events"] == before["unpublished_events"] + 1


def test_counts_are_broken_down_by_event_type(client, accounts):
    client.post("/transactions/deposit", json={
        "account_id": accounts["account_id"],
        "cash_account_id": accounts["cash_account_id"],
        "amount_minor": 5000,
    }, headers=idem_headers())

    body = client.get("/admin/outbox/stats").json()
    assert body["events_by_type"]["transaction.deposit"] >= 1


def test_newest_event_at_advances_after_a_transaction(client, accounts):
    client.post("/transactions/deposit", json={
        "account_id": accounts["account_id"],
        "cash_account_id": accounts["cash_account_id"],
        "amount_minor": 100,
    }, headers=idem_headers())
    first = client.get("/admin/outbox/stats").json()["newest_event_at"]
    assert first is not None

    client.post("/transactions/deposit", json={
        "account_id": accounts["account_id"],
        "cash_account_id": accounts["cash_account_id"],
        "amount_minor": 100,
    }, headers=idem_headers())
    second = client.get("/admin/outbox/stats").json()["newest_event_at"]
    assert second >= first
