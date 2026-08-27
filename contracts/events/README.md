# Ledger transaction events

**Owner:** ledger team · **Status:** proposed, v1 · **Transport:** message broker (async)

Events describing money that has moved. Emitted by the ledger, consumed by
compliance (and any future consumer).

> **Current implementation differs.** The ledger today writes the bare
> transaction body with no envelope. This document describes the target
> contract; the envelope below is the change being proposed before any consumer
> is built against the current accidental shape.

---

## Envelope

Every event has the same envelope. Business detail lives under `data`.

| Field | Type | Always present | Meaning |
| --- | --- | --- | --- |
| `event_id` | uuid | yes | Unique id **of this event**. The deduplication key. |
| `event_type` | string | yes | `transaction.deposit` \| `transaction.transfer` \| `transaction.reversal` |
| `occurred_at` | RFC 3339 | yes | When the transaction was **committed**, not when published. See below. |
| `traceparent` | string \| null | yes (may be null) | W3C Trace Context of the originating request. See below. |
| `data` | object | yes | Transaction detail. Shape depends on `event_type`. |

Business fields are nested under `data` deliberately: it means new envelope
fields can be added later without any chance of colliding with a business field
name.

### Timestamp format

All timestamps are **RFC 3339, UTC, with a numeric offset and second
precision**:

```
2026-08-28T10:15:00+00:00
```

Three guarantees, so a consumer can parse with a fixed expectation:

- **Always UTC**, always written as `+00:00`. Never `Z`, never a local offset.
  Both spellings are legal RFC 3339 and mean the same instant, but emitting one
  consistently means the fixtures match production byte for byte.
- **Always second precision.** No fractional seconds, ever.
- **Always the same length**, so string comparison and sorting are safe.

The fixed precision is deliberate. Python's `datetime.isoformat()` omits
fractional seconds only when microseconds happen to be zero, so a naive
implementation emits `...53.123456+00:00` most of the time and
`...53+00:00` about once in a million events — breaking any consumer with a
strict parser, intermittently and unreproducibly. Producers must pin the
precision:

```python
occurred_at.astimezone(timezone.utc).isoformat(timespec="seconds")
```

Two events committed in the same second will carry identical `occurred_at`
values. That is acceptable because ordering is not guaranteed anyway and
deduplication is on `event_id`, but it does mean `occurred_at` cannot be used
to order events within a second.

### About `traceparent`

The [W3C Trace Context](https://www.w3.org/TR/trace-context/) of the request
that caused the transaction:

```
00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
│  └ trace-id (constant across every hop)  └ parent span  └ flags
└ version
```

Carrying it on the event is what lets a single customer request be followed
across the synchronous hops *and* across the async boundary into compliance, as
one trace.

**The key is always present; the value is frequently `null`.** It is null when
the caller sent no `traceparent` header, when the transaction was started by an
internal job rather than an HTTP request, or when tracing is not enabled.

**Today it is always null** — the ledger does not yet propagate trace context.
Consumers must handle null from the start; treat a populated value as a bonus,
never a guarantee.

Presence has nothing to do with the transaction type. The fixtures vary
deliberately — `transaction.deposit` shows a populated value, the other two show
`null` — purely so consumers exercise both paths.

### `data`

| Field | Type | Always present | Meaning |
| --- | --- | --- | --- |
| `transaction_id` | uuid | yes | The ledger transaction. |
| `type` | string | yes | `deposit` \| `transfer` \| `reversal` |
| `state` | string | yes | Always `posted` in v1. Events are only emitted for committed transactions. |
| `reversal_of_id` | uuid \| null | yes | Set only on `transaction.reversal`; the transaction being reversed. |
| `postings` | array | yes | The double-entry postings. Always at least two. |

### `postings[]`

| Field | Type | Meaning |
| --- | --- | --- |
| `account_id` | uuid | Account affected. |
| `side` | string | `debit` or `credit`. |
| `amount_minor` | integer | Amount in **minor units** (cents). Always positive. |

Two invariants a consumer can rely on:

- There are always **at least two postings**.
- **Total debits equal total credits.** Direction is carried by `side`, never by
  a negative amount — `amount_minor` is always positive.

---

## Examples

### `transaction.deposit`

```json
{
  "event_id": "0f6a1c3e-9b2d-4a71-8f3c-1d2e5a7b9c04",
  "event_type": "transaction.deposit",
  "schema_version": 1,
  "occurred_at": "2026-08-28T10:15:00+00:00",
  "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
  "data": {
    "transaction_id": "550e8400-e29b-41d4-a716-446655440000",
    "type": "deposit",
    "state": "posted",
    "reversal_of_id": null,
    "postings": [
      {"account_id": "bank-system-acct", "side": "credit",  "amount_minor": 100000},
      {"account_id": "alice-acct",     "side": "debit", "amount_minor": 100000}
    ]
  }
}
```

A deposit debits the bank's cash account (an asset increasing) and credits the
customer account (a liability increasing). Both sides are always present.

### `transaction.transfer`

```json
{
  "event_id": "1a7b2d4f-0c3e-5b82-9a4d-2e3f6b8c0d15",
  "event_type": "transaction.transfer",
  "schema_version": 1,
  "occurred_at": "2026-08-28T10:16:30+00:00",
  "traceparent": null,
  "data": {
    "transaction_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
    "type": "transfer",
    "state": "posted",
    "reversal_of_id": null,
    "postings": [
      {"account_id": "alice-uuid", "side": "debit",  "amount_minor": 5000},
      {"account_id": "bob-uuid",   "side": "credit", "amount_minor": 5000}
    ]
  }
}
```

### `transaction.reversal`

```json
{
  "event_id": "2b8c3e50-1d4f-6c93-0b5e-3f4a7c9d1e26",
  "event_type": "transaction.reversal",
  "schema_version": 1,
  "occurred_at": "2026-08-28T11:02:11+00:00",
  "traceparent": null,
  "data": {
    "transaction_id": "7cb8c921-0ebe-22e2-91c5-11d15fe541d9",
    "type": "reversal",
    "state": "posted",
    "reversal_of_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
    "postings": [
      {"account_id": "alice-uuid", "side": "credit", "amount_minor": 5000},
      {"account_id": "bob-uuid",   "side": "debit",  "amount_minor": 5000}
    ]
  }
}
```

A reversal is a **new transaction with opposite postings**, not an edit or
deletion of the original. The original transaction stays exactly as it was, and
its event is never retracted or amended. `reversal_of_id` links them.

---

## Delivery semantics

Read this section carefully — most integration bugs come from assuming
something here that is not true.

### At-least-once, never exactly-once

The same event **will** be delivered more than once. Publishing and marking-as-
published are not atomic, so a crash between them causes redelivery.

**Deduplicate on `event_id`.** It is stable across redeliveries of the same
event — the same row is republished, keeping its id.

Handlers should be idempotent regardless. "I already processed `event_id` X" is
the cheapest correct implementation.

### Ordering is not guaranteed

Events may arrive in any order. In particular **a reversal can arrive before
the transaction it reverses.**

Use `reversal_of_id` and `occurred_at` to reconstruct the relationship. Do not
rely on arrival order, and do not treat "reversal for an unknown transaction"
as an error — the original may simply be behind it.

### Only committed transactions produce events

The event is written in the **same database transaction** as the postings,
balance updates and idempotency record. Therefore:

- If you receive an event, **the money moved.** It is not a proposal or an
  attempt.
- No event exists for a transaction that rolled back.
- No committed transaction is missing its event.

A retried request that hits an existing idempotency key returns the original
transaction **without** emitting a second event.

### If a consumer is down

Nothing breaks upstream. Events accumulate unpublished in the ledger's outbox
table and drain when the consumer recovers. The ledger does not wait for, retry
against, or care about consumers.

---

## Versioning

`schema_version` is `1`.

**Additive changes do not bump the version.** New optional fields may appear in
the envelope or in `data` at any time. **Consumers must ignore unknown fields**
rather than failing validation — a strict schema check that rejects extra
fields will break on the first additive change.

**Breaking changes bump the version**: removing a field, renaming one, changing
a type, or changing the meaning of an existing field. Both versions will be
published during a transition period so consumers can migrate without a
flag day.

---

## Deliberately not included

| Not sent | Why |
| --- | --- |
| Account balances | Point-in-time and stale the moment it is read. A consumer acting on a balance in an event would be acting on a lie. Query the ledger for current balance. |
| Account owner / customer details | Keeps customer data out of the message bus. Ask if you need it — it is a contract change, not a workaround. |
| The API client that made the request | Not relevant to downstream consumers; avoids leaking caller identity across services. |
| Amounts as negative numbers | Direction is carried by `side`. A negative `amount_minor` never appears. |

If a consumer needs something in this table, raise it — the answer may well be
yes, but adding it should be a deliberate contract change rather than a
callback into the ledger, which would make the dependency circular.

---

## Fixtures

`transaction.deposit.v1.json`, `transaction.transfer.v1.json`,
`transaction.reversal.v1.json` in this directory are the examples above as
files.

Consumers can build and test against these with no ledger running. The ledger
asserts its emitted events match them, so a breaking change fails the
**producer's** build rather than surfacing later in someone else's service.
