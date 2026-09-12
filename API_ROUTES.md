# API routes

This document describes the HTTP APIs available in the local mentorship ledger
stack.

## Common conventions

Every FastAPI service exposes the following generated documentation routes:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/docs` | Interactive Swagger UI |
| `GET` | `/redoc` | ReDoc API documentation |
| `GET` | `/openapi.json` | Machine-readable OpenAPI specification |

The ledger accepts an optional `X-Correlation-ID` request header. If it is not
provided, the service generates one. The correlation ID is returned in the
response headers and propagated through risk checks, outbox events, Kafka,
compliance, logs, and traces.

Amounts are integers in minor currency units. For USD, `100` represents one
dollar.

Responses also include `X-Trace-ID` and `traceparent` for the active request
trace. See [Tracing](observability/TRACING.md) for following asynchronous steps
in Grafana Tempo.

## Ledger API

**Base URL:** `http://localhost:8000`

All ledger routes except `/health`, `/docs`, `/redoc`, and `/openapi.json`
require:

```http
X-API-Key: local-development-key
```

Deposit, transfer, and reversal commands also require a stable idempotency
header:

```http
Idempotency-Key: <unique-client-generated-value>
```

### General

| Method | Route | Authentication | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | None | Check ledger API health |
| `GET` | `/ui` | None | Open the local ledger test UI |

### Accounts

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/accounts` | Create an account and its zero balance |
| `GET` | `/accounts` | List all accounts |
| `GET` | `/accounts/{account_id}/balance` | Get total and available balances |
| `GET` | `/accounts/{account_id}/transactions?limit=100` | Get immutable account history, newest first |

Create-account body:

```json
{
  "owner_id": "alice",
  "account_type": "personal",
  "currency": "USD"
}
```

Valid account types are `cash`, `personal`, `customer`, `business`, and
`external_bank`. Currency defaults to `USD` and must be a three-letter code.

Balance response:

```json
{
  "account_id": "account-uuid",
  "balance_minor": 10000,
  "available_balance_minor": 7500
}
```

`available_balance_minor` subtracts active settlement holds from the posted
balance.

### Transactions

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/transactions/deposit` | Deposit funds into an account |
| `POST` | `/transactions/transfer` | Transfer available funds between accounts |
| `POST` | `/transactions/{transaction_id}/reverse` | Post an opposite transaction that reverses the original |

Deposit body:

```json
{
  "account_id": "customer-account-uuid",
  "cash_account_id": "cash-account-uuid",
  "amount_minor": 10000
}
```

Transfer body:

```json
{
  "from_account_id": "source-account-uuid",
  "to_account_id": "destination-account-uuid",
  "amount_minor": 5000
}
```

The reversal endpoint has no request body. Its `transaction_id` path parameter
identifies the transaction being reversed.

Successful transaction response:

```json
{
  "transaction_id": "transaction-uuid",
  "type": "transfer",
  "state": "posted",
  "postings": [
    {
      "account_id": "source-account-uuid",
      "side": "debit",
      "amount_minor": 5000
    },
    {
      "account_id": "destination-account-uuid",
      "side": "credit",
      "amount_minor": 5000
    }
  ],
  "reversal_of_id": null
}
```

### Events and outbox

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/events?cursor={cursor}&limit=100` | Read committed events through a pull-based feed |
| `GET` | `/admin/outbox/stats` | Inspect total, unpublished, and per-type event counts |

The event-feed `cursor` is opaque and should be stored exactly as returned in
`next_cursor`. The default page size is 100 and the maximum is 1,000.

### Settlements

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/settlements/prepare` | Create pending business settlements and active holds |
| `POST` | `/settlements/{settlement_id}/confirm` | Post the settlement transaction and settle its hold |
| `POST` | `/settlements/{settlement_id}/fail` | Mark the settlement failed and release its hold |
| `GET` | `/settlements?limit=100` | List settlements, newest first |

Prepare body:

```json
{
  "batch_id": "2026-09-08",
  "external_bank_account_id": "external-bank-account-uuid"
}
```

The `batch_id` makes preparation idempotent for each business account.

Confirmation body:

```json
{
  "external_reference": "bank-payout-reference"
}
```

Failure body:

```json
{
  "reason": "external_bank_rejected"
}
```

## Risk API

**Base URL:** `http://localhost:8001`

| Method | Route | Authentication | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | None | Check risk-service health |
| `POST` | `/risk-decisions` | None | Create or retrieve an idempotent risk decision |

Risk-decision body:

```json
{
  "client_scope": "local-services",
  "idempotency_key": "risk-001",
  "transaction_type": "transfer",
  "amount_minor": 10000,
  "owner_ids": ["alice", "bob"],
  "correlation_id": "request-001"
}
```

Possible decision states are `approved`, `review`, and `declined`.

See [Risk and Compliance Rules](RISK_AND_COMPLIANCE_RULES.md) for the exact
trigger order, configured defaults, and interaction between both services.

The current implementation does not expose routes for listing reviewed
decisions or manually approving and declining them. Local manual approval can
be performed directly in the risk PostgreSQL database for testing.

## Compliance API

**Base URL:** `http://localhost:8002`

| Method | Route | Authentication | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | None | Check compliance-service health |
| `GET` | `/compliance-alerts?limit=100` | None | List generated compliance alerts |
| `GET` | `/dead-letter-events?limit=100` | None | List events that exhausted consumer retries |

The compliance service receives transaction events asynchronously from the
Kafka `ledger.events` topic. It has no HTTP endpoint for directly submitting
events.

Current compliance rules are:

- `single_minor_unit_transaction`: a committed event with a debit total of
  exactly `1` minor unit, generating a low-severity alert.
- `repeated_transfers`: the third and subsequent committed outgoing transfer
  from the same account within five minutes.
- `large_transaction`: a committed event with a debit total of at least
  500,000 minor units. Normal deposits and transfers at this threshold are
  stopped by the default risk-review rule before reaching compliance, but a
  settlement can still trigger this rule.

## Settlement orchestrator API

**Base URL:** `http://localhost:8004`

| Method | Route | Authentication | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | None | Check settlement-service health |
| `POST` | `/settlements/run` | None | Run the current settlement batch |

The run endpoint finds or creates the synthetic external-bank ledger account,
prepares settlements, requests payouts, and confirms or fails the corresponding
ledger settlements.

## Synthetic external bank API

**Base URL:** `http://localhost:8003`

| Method | Route | Authentication | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | None | Check external-bank simulator health |
| `POST` | `/payouts` | None | Create an idempotent synthetic payout |

Payout body:

```json
{
  "settlement_id": "settlement-uuid",
  "account_id": "business-account-uuid",
  "amount_minor": 75000,
  "correlation_id": "settlement-run-001"
}
```

## Temporal settlement API

**Base URL:** `http://localhost:8005`

This is an additive learning path. The original settlement orchestrator on
port 8004 remains available.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check the Temporal starter API and connection |
| `POST` | `/temporal/settlements/run` | Start today's durable settlement batch |
| `GET` | `/temporal/workflows/{workflow_id}` | Read a workflow's current status |

The request body can choose a batch ID and optional learning-only failure
simulation settings:

```json
{
  "batch_id": "learning-run-001",
  "payout_failures_before_success": 0,
  "simulate_bank_rejection": false,
  "payout_delay_seconds": 0,
  "workflow_delay_seconds": 0
}
```

The start response includes a link to the workflow in Temporal UI. Reusing a
batch ID returns `started: false` and does not create a duplicate workflow.
The ledger UI at `http://localhost:8000/ui/` exposes these settings as named
test scenarios, so no terminal commands are required for the demonstrations.

## Non-HTTP interfaces

| Component | Local address | Purpose |
| --- | --- | --- |
| Kafka | `localhost:29092` | Transaction-event transport |
| Kafka UI | `http://localhost:8080` | Inspect topics, messages, and consumer groups |
| Grafana | `http://localhost:3000` | View dashboards, alerts, metrics, traces, and logs |
| Ledger PostgreSQL | `localhost:5432` | Ledger state and transactional outbox |
| Risk PostgreSQL | `localhost:5433` | Risk decisions |
| Compliance PostgreSQL | `localhost:5434` | Processed events, alerts, and failures |
| Outbox publisher | No HTTP port | Publish committed outbox rows to Kafka |

## OpenAPI contract files

The generated service contracts are stored in:

- `contracts/openapi.json`
- `contracts/risk-openapi.json`
- `contracts/compliance-openapi.json`
- `contracts/settlement-openapi.json`
- `contracts/external-bank-openapi.json`

The ready-to-import local Postman collection and environment are stored in the
`postman` directory.
