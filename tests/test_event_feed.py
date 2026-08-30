"""The pull-based event feed, and the two bugs its cursor design exists to
prevent.

Both bugs lose events silently - no exception, no error response, just a
transaction that compliance never sees. So they are tested by construction
rather than left to chance: the tests force the exact conditions that trigger
them.
"""
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.event_feed import decode_cursor, encode_cursor
from tests.conftest import db_execute, db_rows

ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def idem():
    return {"Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture
def accounts(client):
    suffix = uuid.uuid4().hex[:8]

    def account(owner, kind):
        return client.post("/accounts", json={"owner_id": f"{owner}-{suffix}", "account_type": kind}).json()

    return {
        "cash_account_id": account("cash", "cash")["account_id"],
        "account_id": account("alice", "customer")["account_id"],
        "to_account_id": account("bob", "customer")["account_id"],
    }


def deposit(client, accounts, amount=10000):
    return client.post("/transactions/deposit", json={
        "account_id": accounts["account_id"],
        "cash_account_id": accounts["cash_account_id"],
        "amount_minor": amount,
    }, headers=idem()).json()


def backdate(transaction_ids, moment):
    """Forces outbox rows to a chosen created_at.

    Used to place events at a unique point far in the past, so a test can
    page over exactly its own events without the rest of the suite's data
    interleaving.
    """
    db_execute(
        "UPDATE outbox_events SET created_at = %s WHERE transaction_id = ANY(%s::uuid[])",
        (moment, list(transaction_ids)),
    )


def unique_past_moment():
    """A distinct timestamp per run, so repeated runs cannot collide."""
    return datetime(1990, 1, 1, tzinfo=timezone.utc) + timedelta(
        seconds=random.randint(0, 10_000_000)
    )


def cursor_just_before(moment):
    return encode_cursor(moment - timedelta(microseconds=1), ZERO_UUID)


def read_pages(client, cursor, limit, pages):
    """Walks the feed, returning the event_ids seen in order."""
    seen = []
    for _ in range(pages):
        response = client.get("/events", params={"cursor": cursor, "limit": limit})
        assert response.status_code == 200
        body = response.json()
        seen.extend(event["event_id"] for event in body["events"])
        cursor = body["next_cursor"]
        if not body["events"]:
            break
    return seen, cursor


class TestSameSecondTie:
    """BUG 1. Events sharing a created_at are the case that breaks a
    timestamp-only cursor: `>` skips them, `>=` loops forever."""

    def test_all_same_second_events_are_returned_exactly_once(self, client, accounts):
        moment = unique_past_moment()
        transactions = [deposit(client, accounts, amount=1000 + i) for i in range(3)]
        ids = [t["transaction_id"] for t in transactions]
        backdate(ids, moment)

        expected = {
            str(row["event_id"])
            for row in db_rows(
                "SELECT event_id FROM outbox_events WHERE transaction_id = ANY(%s::uuid[])",
                (ids,),
            )
        }
        assert len(expected) == 3

        # One at a time is the worst case: every page boundary lands inside
        # the tie.
        seen, _ = read_pages(client, cursor_just_before(moment), limit=1, pages=3)

        assert len(seen) == 3, "an event was skipped or repeated across the tie"
        assert set(seen) == expected
        assert len(set(seen)) == len(seen), "the same event came back twice"

    def test_they_really_do_share_a_timestamp(self, client, accounts):
        """Guards the test itself - if backdating stopped working, the test
        above would pass without exercising the tie at all."""
        moment = unique_past_moment()
        ids = [deposit(client, accounts, amount=2000 + i)["transaction_id"] for i in range(3)]
        backdate(ids, moment)

        timestamps = {
            row["created_at"]
            for row in db_rows(
                "SELECT created_at FROM outbox_events WHERE transaction_id = ANY(%s::uuid[])",
                (ids,),
            )
        }
        assert len(timestamps) == 1


class TestSafetyWindow:
    """BUG 2. created_at is the transaction's *start* time, so a slow
    transaction can become visible behind a cursor that has already moved
    past it. Withholding very recent rows closes the gap."""

    def test_a_brand_new_event_is_withheld(self, client, accounts):
        transaction = deposit(client, accounts, amount=3210)
        event_id = str(db_rows(
            "SELECT event_id FROM outbox_events WHERE transaction_id = %s",
            (transaction["transaction_id"],),
        )[0]["event_id"])

        # Read the whole feed from the start; the event was created moments
        # ago and so is still inside the window.
        seen = []
        cursor = None
        for _ in range(50):
            body = client.get("/events", params={"cursor": cursor, "limit": 1000}).json()
            if not body["events"]:
                break
            seen.extend(e["event_id"] for e in body["events"])
            cursor = body["next_cursor"]

        assert event_id not in seen

    def test_it_appears_once_it_is_older_than_the_window(self, client, accounts):
        moment = unique_past_moment()
        transaction = deposit(client, accounts, amount=4321)
        backdate([transaction["transaction_id"]], moment)

        body = client.get("/events", params={"cursor": cursor_just_before(moment), "limit": 1}).json()

        assert body["count"] == 1
        assert body["events"][0]["data"]["transaction_id"] == transaction["transaction_id"]


class TestCursor:
    def test_round_trip(self):
        moment = datetime(2026, 8, 30, 10, 58, 18, tzinfo=timezone.utc)
        event_id = str(uuid.uuid4())

        assert decode_cursor(encode_cursor(moment, event_id)) == (moment, event_id)

    def test_is_opaque(self):
        """Base64 so consumers do not start hand-building cursors and depend
        on the internal format."""
        cursor = encode_cursor(datetime.now(timezone.utc), str(uuid.uuid4()))
        assert "|" not in cursor and ":" not in cursor

    @pytest.mark.parametrize("bad", ["not-base64!!", "", "YWJjZGVm", "!!!!"])
    def test_a_bad_cursor_is_rejected(self, client, bad):
        response = client.get("/events", params={"cursor": bad})
        # An empty cursor is absent, not invalid, so it starts from the top.
        assert response.status_code == (200 if bad == "" else 400)


class TestFeedBehaviour:
    def test_events_carry_the_full_envelope(self, client, accounts):
        moment = unique_past_moment()
        transaction = deposit(client, accounts, amount=5555)
        backdate([transaction["transaction_id"]], moment)

        event = client.get("/events", params={"cursor": cursor_just_before(moment), "limit": 1}).json()["events"][0]

        assert set(event) == {"event_id", "event_type", "schema_version",
                              "occurred_at", "traceparent", "data"}

    def test_caught_up_returns_an_empty_page_and_keeps_the_cursor(self, client, accounts):
        moment = unique_past_moment()
        transaction = deposit(client, accounts, amount=6666)
        backdate([transaction["transaction_id"]], moment)

        first = client.get("/events", params={"cursor": cursor_just_before(moment), "limit": 1}).json()
        # Everything after it is inside the safety window, so the next page is
        # empty rather than an error, and the cursor is safe to reuse.
        second = client.get("/events", params={"cursor": first["next_cursor"], "limit": 1000}).json()

        assert second["count"] == 0
        assert second["next_cursor"] == first["next_cursor"]

    def test_requires_authentication(self, client):
        key = client.headers.pop("X-API-Key")
        try:
            assert client.get("/events").status_code == 422
        finally:
            client.headers["X-API-Key"] = key
