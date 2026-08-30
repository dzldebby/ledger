"""Builds the outbox event envelope.

The envelope is the part of an event a consumer can handle *without knowing
what kind of event it is*: deduplicate on `event_id`, route on `event_type`,
pick a parser from `schema_version`, stitch a trace from `traceparent`.
Business detail is nested under `data`, which is what makes it safe to add new
envelope fields later - they can never collide with a business field name.

The contract is contracts/events/README.md, and the fixtures beside it are
asserted against in tests/test_event_contract.py. A change here that would
break a consumer therefore fails *our* build rather than surfacing later in
someone else's service.
"""
import uuid
from datetime import datetime, timezone

from app.schemas.transactions import PostingResponse, TransactionResponse

SCHEMA_VERSION = 1


def format_timestamp(moment: datetime) -> str:
    """RFC 3339, UTC, numeric offset, second precision.

    timespec is pinned deliberately. isoformat() omits fractional seconds only
    when microseconds happen to be zero, so leaving it unpinned emits
    "...53.123456+00:00" almost always and "...53+00:00" about once in a
    million events - breaking a strict consumer parser intermittently and
    unreproducibly. Fixed precision also keeps every timestamp the same length,
    so string comparison and sorting are safe.
    """
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _posting(posting: PostingResponse) -> dict:
    # The event contract calls this `amount`; the HTTP API calls the same
    # number `amount_minor`. Both are minor units (cents) and both are always
    # positive - direction is carried by `side`, never by a negative amount.
    return {
        "account_id": posting.account_id,
        "side": posting.side,
        "amount": posting.amount_minor,
    }


def build_event(
    response: TransactionResponse,
    *,
    event_id: uuid.UUID | str | None = None,
    occurred_at: datetime | None = None,
    traceparent: str | None = None,
) -> dict:
    """Wraps a posted transaction in the v1 event envelope.

    event_id and occurred_at are injectable so the contract test can reproduce
    a fixture byte for byte; in production both are generated here.

    traceparent is always present as a key and is null today - the ledger does
    not yet propagate trace context. Threading the inbound header through to
    here is the only change needed to populate it, and is not a contract
    change because consumers are already required to handle null.
    """
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": f"transaction.{response.type}",
        "schema_version": SCHEMA_VERSION,
        "occurred_at": format_timestamp(occurred_at or datetime.now(timezone.utc)),
        "traceparent": traceparent,
        "data": {
            "transaction_id": response.transaction_id,
            "type": response.type,
            "state": response.state,
            "reversal_of_id": response.reversal_of_id,
            "postings": [_posting(p) for p in response.postings],
        },
    }
