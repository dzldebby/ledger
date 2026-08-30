# Integration contracts — proposal

**Status: draft for discussion.** Everything here is a starting point, not a
decision. Push back on any of it — it is much cheaper to change now than after
three implementations exist.

Once we agree, this becomes the living contract and changes go through PR.

## Context

The ledger core is built and deployed: accounts, deposits, transfers,
reversals, double-entry postings, pessimistic row-locking, idempotency, and API
key auth.

**Base URL: `https://ledger-api-8i8i.onrender.com`**

- `/docs` — browsable API
- `/openapi.json` — machine-readable spec, also committed here as
  `contracts/openapi.json` so you can generate a client without the service
  being up

Every endpoint except `/health` needs an `X-API-Key` header. Ask me for a key.

**Two things about this host, so you don't file them as bugs.** It is a free
instance: it spins down after 15 minutes idle, so the *first* request after a
quiet period can take 30–60 seconds. Treat a slow first call as a cold start,
not a timeout. And the database is deleted 30 days after creation, which is
past the end of this project — but it means nothing stored here is durable, and
any data you rely on for a demo should be re-createable from the API.

Two boundaries connect our components:

```
Ledger  ──── calls, waits ────►  Risk         (synchronous HTTP)
Ledger  ──── emits ───────────►  Compliance   (asynchronous events)
```

Neither Risk nor Compliance talks to the other, so these are the only two
interfaces we need to agree.

---

## Boundary 1 — Ledger → Risk

The ledger calls Risk **before** posting a transaction and waits for a
decision. This is on the critical path of every write, so latency and failure
behaviour matter.

### Request

```http
POST /risk/evaluate
Content-Type: application/json

{
  "transaction_id": "550e8400-e29b-41d4-a716-446655440000",
  "type": "transfer",
  "amount_minor": 5000,
  "from_account_id": "alice-uuid",
  "to_account_id": "bob-uuid",
  "occurred_at": "2026-08-28T10:15:00Z"
}
```

`amount_minor` is in minor units (cents). `from_account_id` is null for
deposits.

**Proposed: start with this minimal set.** If Risk needs more context (account
owner, balance, history) let us add named fields rather than Risk calling back
into the ledger — a callback would make the dependency circular.

### Response

```http
200 OK

{
  "decision": "approve",
  "reason": null,
  "rule_ids": [],
  "evaluated_at": "2026-08-28T10:15:00Z"
}
```

`decision` is one of `approve` | `decline` | `review`.

For a decline:

```json
{"decision": "decline", "reason": "velocity_limit_exceeded", "rule_ids": ["VEL-003"]}
```

**Proposed: a decline is `200`, not `403`.** A decline is a *successful
evaluation that returned no*. HTTP error codes should mean the evaluation
itself failed, so the ledger can distinguish "Risk says no" from "Risk is
broken" — those need opposite handling.

`4xx`/`5xx` therefore mean Risk could not evaluate, and are treated as a
failure (see below).

### Retries

The ledger may retry an evaluation if the first attempt times out.

**Proposed: Risk keys on `transaction_id` and returns the same decision for a
repeat call**, rather than evaluating twice. Same idempotency problem the
ledger already solves; worth solving on both sides.

### Which transactions are evaluated

**Proposed: transfers and deposits, but not reversals.** A reversal undoes an
already-approved transaction, so re-evaluating it could block a correction.
Open to argument.

---

## Boundary 2 — Ledger → Compliance

The ledger writes an event into an `outbox_events` table **in the same
database transaction** as the postings and balance updates. A publisher then
reads that table and puts events on the broker.

This means an event cannot exist for a transaction that rolled back, and
cannot be missing for one that committed.

### Event envelope

```json
{
  "event_id": "evt-789",
  "event_type": "transaction.transfer",
  "schema_version": 1,
  "occurred_at": "2026-08-28T10:15:00Z",
  "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
  "data": {
    "transaction_id": "550e8400-e29b-41d4-a716-446655440000",
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

`event_type` is one of `transaction.deposit` | `transaction.transfer` |
`transaction.reversal`.

### Guarantees

- **At-least-once delivery. Duplicates will happen — dedup on `event_id`.**
  We cannot offer exactly-once, and anything built assuming it will be subtly
  wrong.
- **Ordering is not guaranteed.** A reversal may arrive before the transaction
  it reverses. Use `reversal_of_id` and `occurred_at`, not arrival order.
- **Only committed transactions produce events.** If you receive one, the money
  moved.
- **If Compliance is down, nothing breaks.** Events accumulate unpublished in
  the outbox and drain on recovery. This is why the flow is asynchronous.

### Versioning

`schema_version` starts at 1. Additive fields do **not** bump it, so consumers
must ignore unknown fields. Removals and renames bump the version, and both
versions are published during a transition.

---

## Decisions I cannot make alone

These are business or team calls. I have proposed an answer so we have
something to argue with, but they need agreement.

### 1. What does the ledger do on `review`?

The HLD lists three outcomes but only defines two behaviours.

| Option | Effect |
| --- | --- |
| Treat as decline | Simple, but rejects transactions a human might approve |
| Post and flag | Money moves before review — probably wrong |
| **Place a hold (proposed)** | Funds reserved, not posted, resolved later |

The ledger schema already has an unused `holds` table, which suggests holds
were the original intent. If we choose this, we need to define who resolves a
hold and how.

### 2. Fail open or fail closed when Risk is unavailable?

| Option | Effect |
| --- | --- |
| **Fail closed (proposed)** | Reject the transaction. Risk being down stops writes |
| Fail open | Post anyway and flag. Unchecked money moves |

**Proposed: fail closed, with a 2 second timeout.** For a ledger, refusing to
move money we could not check seems safer than moving it. But this trades
availability for safety and is genuinely a business decision — worth asking our
mentor.

### 3. Does Compliance need data the ledger does not currently send?

Right now events carry account **IDs only**, no owner details. If Compliance
needs owner or balance data it would have to call back into the ledger, which
turns a fire-and-forget flow into two-way coupling. Worth deciding deliberately
rather than discovering later.

### 4. Broker choice and ownership

**Decided: Kafka, with the ledger owning the publisher.** Events go to a single
topic, `ledger.events`.

- **Value** is the complete envelope as JSON — self-contained, so an archived
  or replayed copy is still a whole event.
- **Headers** carry `event_id`, `event_type` and `schema_version`, so a router
  can dispatch or deduplicate without deserializing the body.
- **Key** is `reversal_of_id or transaction_id`. Kafka only orders within a
  partition and the key picks the partition, so a reversal deliberately lands
  on the same partition as the transaction it reverses. Treat that as best
  effort, not a guarantee — the ordering caveat above still applies.

Run it locally with `docker compose up kafka kafka-ui`; the UI at
`localhost:8080` shows topics and individual messages. For a demo where all
three services run on different machines we need one shared cluster, since a
broker in your Docker is a separate, empty broker — see DEPLOYMENT.md.

**Still open:** whether the shared cluster is Confluent Cloud (free credits
cover our remaining window) or one of us exposing a broker on the LAN. That
only needs deciding before the first joint demo, not before you start building.

---

## Working in parallel before integration

Nobody should be blocked waiting for anyone else.

- **Fixtures.** Example payloads for both boundaries live in `contracts/`.
  Compliance can build and test a consumer against the event fixture with no
  ledger running.
- **Stubs.** The ledger calls Risk through an interface with a stub
  implementation that always approves, so ledger work continues before Risk
  exists. Risk can do the same in reverse.
- **Contract tests.** Each side asserts against the committed fixture, so a
  breaking change fails the *producer's* build rather than surfacing weeks
  later in someone else's service.

## Where contracts live

This directory. Changes go through PR so they are visible and reviewable — the
failure mode we are avoiding is someone changing a shape and nobody noticing
until integration.
