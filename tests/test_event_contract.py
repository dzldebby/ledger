"""Contract test for the outbox event envelope.

The fixtures in contracts/events/ are what other teams build their consumers
against. These tests assert the ledger emits exactly those shapes, so a change
that would break a consumer fails *this* build rather than surfacing weeks
later in someone else's service.

Full equality, not a shape check: build_event takes event_id and occurred_at
so a fixture can be reproduced byte for byte. A shape check would let a
renamed field through as long as the type matched, which is precisely the
break we are trying to catch.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.schemas.transactions import PostingResponse, TransactionResponse
from app.services.events import build_event, format_timestamp
from tests.conftest import db_rows

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "contracts" / "events"


def load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text())


def posting(account_id, side, amount):
    # Fixtures speak the contract's `amount`; the internal model speaks
    # `amount_minor`. Translating here is the point of the test - it proves
    # build_event does the rename.
    return PostingResponse(account_id=account_id, side=side, amount_minor=amount)


DEPOSIT = (
    "transaction.deposit.v1.json",
    TransactionResponse(
        transaction_id="550e8400-e29b-41d4-a716-446655440000",
        type="deposit",
        state="posted",
        reversal_of_id=None,
        postings=[
            posting("bank-cash-uuid", "debit", 100000),
            posting("alice-uuid", "credit", 100000),
        ],
    ),
    "0f6a1c3e-9b2d-4a71-8f3c-1d2e5a7b9c04",
    datetime(2026, 8, 28, 10, 15, 0, tzinfo=timezone.utc),
    "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
)

TRANSFER = (
    "transaction.transfer.v1.json",
    TransactionResponse(
        transaction_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        type="transfer",
        state="posted",
        reversal_of_id=None,
        postings=[
            posting("alice-uuid", "debit", 5000),
            posting("bob-uuid", "credit", 5000),
        ],
    ),
    "1a7b2d4f-0c3e-5b82-9a4d-2e3f6b8c0d15",
    datetime(2026, 8, 28, 10, 16, 30, tzinfo=timezone.utc),
    None,
)

REVERSAL = (
    "transaction.reversal.v1.json",
    TransactionResponse(
        transaction_id="7cb8c921-0ebe-22e2-91c5-11d15fe541d9",
        type="reversal",
        state="posted",
        # The reversal carries its own transaction_id; reversal_of_id points
        # at the transfer above. The two fixtures are a matched pair.
        reversal_of_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        postings=[
            posting("alice-uuid", "credit", 5000),
            posting("bob-uuid", "debit", 5000),
        ],
    ),
    "2b8c3e50-1d4f-6c93-0b5e-3f4a7c9d1e26",
    datetime(2026, 8, 28, 11, 2, 11, tzinfo=timezone.utc),
    None,
)

CASES = [DEPOSIT, TRANSFER, REVERSAL]
CASE_IDS = [case[0] for case in CASES]


@pytest.mark.parametrize("name,response,event_id,occurred_at,traceparent", CASES, ids=CASE_IDS)
def test_emitted_event_matches_the_published_fixture(name, response, event_id, occurred_at, traceparent):
    event = build_event(response, event_id=event_id, occurred_at=occurred_at, traceparent=traceparent)
    assert event == load_fixture(name)


def test_the_matched_pair_actually_matches():
    """The README tells consumers to feed these two in reverse order to
    exercise the out-of-order case, which only works if they really are a
    pair."""
    transfer = load_fixture("transaction.transfer.v1.json")
    reversal = load_fixture("transaction.reversal.v1.json")

    assert reversal["data"]["reversal_of_id"] == transfer["data"]["transaction_id"]
    assert reversal["data"]["transaction_id"] != transfer["data"]["transaction_id"]
    assert reversal["occurred_at"] > transfer["occurred_at"]

    # postings are mirrored: same accounts and amounts, opposite sides
    sides = {p["account_id"]: p["side"] for p in transfer["data"]["postings"]}
    for p in reversal["data"]["postings"]:
        assert p["side"] != sides[p["account_id"]]


@pytest.mark.parametrize("fixture", CASE_IDS)
def test_fixture_postings_balance(fixture):
    postings = load_fixture(fixture)["data"]["postings"]
    debits = sum(p["amount"] for p in postings if p["side"] == "debit")
    credits = sum(p["amount"] for p in postings if p["side"] == "credit")

    assert len(postings) >= 2
    assert debits == credits
    assert all(p["amount"] > 0 for p in postings)


class TestTimestampFormat:
    """The contract promises three things: always UTC as +00:00, always second
    precision, always the same length."""

    def test_uses_numeric_offset_not_z(self):
        assert format_timestamp(datetime(2020, 12, 9, 16, 9, 53, tzinfo=timezone.utc)) == "2020-12-09T16:09:53+00:00"

    def test_drops_fractional_seconds(self):
        moment = datetime(2020, 12, 9, 16, 9, 53, 123456, tzinfo=timezone.utc)
        assert format_timestamp(moment) == "2020-12-09T16:09:53+00:00"

    def test_zero_microseconds_is_the_same_length(self):
        """The bug this format exists to prevent: an unpinned isoformat emits a
        shorter string only when microseconds land on zero, so a strict
        consumer parser breaks about one event in a million."""
        with_micros = format_timestamp(datetime(2020, 12, 9, 16, 9, 53, 999999, tzinfo=timezone.utc))
        without = format_timestamp(datetime(2020, 12, 9, 16, 9, 53, 0, tzinfo=timezone.utc))
        assert len(with_micros) == len(without) == 25

    def test_converts_a_non_utc_input_to_utc(self):
        moment = datetime(2020, 12, 9, 16, 9, 53, tzinfo=timezone(timedelta(hours=8)))
        assert format_timestamp(moment) == "2020-12-09T08:09:53+00:00"


class TestEnvelopeInvariants:
    def test_traceparent_key_is_present_even_when_null(self):
        """Consumers are told the key is always there and the value is
        frequently null - so it must not be omitted."""
        event = build_event(TRANSFER[1])
        assert "traceparent" in event
        assert event["traceparent"] is None

    def test_event_id_is_unique_per_call(self):
        response = TRANSFER[1]
        assert build_event(response)["event_id"] != build_event(response)["event_id"]

    def test_event_type_is_namespaced(self):
        assert build_event(DEPOSIT[1])["event_type"] == "transaction.deposit"

    def test_business_fields_stay_under_data(self):
        """Nothing from the transaction body may leak into the envelope, or a
        future envelope field could collide with it."""
        event = build_event(TRANSFER[1])
        assert set(event) == {"event_id", "event_type", "schema_version", "occurred_at", "traceparent", "data"}


def test_a_real_deposit_emits_the_contracted_envelope(client):
    """End to end: the envelope the app actually writes to outbox_events, not
    just the one build_event returns in isolation."""
    def account(owner, kind):
        return client.post("/accounts", json={"owner_id": owner, "account_type": kind}).json()

    suffix = uuid.uuid4().hex[:8]
    cash = account(f"cash-contract-{suffix}", "cash")
    alice = account(f"alice-contract-{suffix}", "customer")

    txn = client.post("/transactions/deposit", json={
        "account_id": alice["account_id"],
        "cash_account_id": cash["account_id"],
        "amount_minor": 7500,
    }, headers={"Idempotency-Key": str(uuid.uuid4())}).json()

    row = db_rows(
        "SELECT * FROM outbox_events WHERE transaction_id = %s",
        (txn["transaction_id"],),
    )[0]
    payload = row["payload"]

    assert set(payload) == set(load_fixture("transaction.deposit.v1.json"))
    assert payload["event_type"] == "transaction.deposit"
    assert payload["schema_version"] == 1
    assert payload["traceparent"] is None
    assert payload["data"]["transaction_id"] == txn["transaction_id"]
    assert payload["data"]["state"] == "posted"
    assert sum(p["amount"] for p in payload["data"]["postings"]) == 15000

    # The columns are projections of the payload and must agree with it,
    # otherwise a publisher reading columns and a consumer reading the payload
    # would disagree about the same event.
    assert str(row["event_id"]) == payload["event_id"]
    assert row["event_type"] == payload["event_type"]
    assert row["traceparent"] == payload["traceparent"]

    # occurred_at must parse with a strict RFC 3339 reader
    assert datetime.fromisoformat(payload["occurred_at"]).tzinfo is not None
