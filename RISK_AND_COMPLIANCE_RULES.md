# Risk and Compliance Rules

This document describes the rules currently implemented by the local ledger
services. Amounts are expressed in **minor units**: for a currency with two
decimal places, `500000` means 5,000.00.

## At a glance

| Concern | Risk | Compliance |
| --- | --- | --- |
| When it runs | Synchronously, before the ledger transaction commits | Asynchronously, after the transaction commits and its outbox event reaches Kafka |
| Transactions evaluated | Deposits and transfers | Committed deposits, transfers, reversals, and settlements |
| Result | `approved`, `review`, or `declined` | A compliance alert, or no alert |
| Can stop a transaction? | Yes | No |
| HTTP service | `http://localhost:8001` | `http://localhost:8002` |

Risk protects the ledger before money moves. Compliance monitors activity that
has already happened. A risk review is therefore not a compliance alert.

## Risk triggers

Risk rules are evaluated in the following order. The first matching rule wins.

| Priority | Trigger | Decision | Reason | Ledger response |
| --- | --- | --- | --- | --- |
| 1 | Any owner ID starts with `blocked-`, case-insensitively | `declined` | `blocked_owner` | `403 Forbidden` |
| 2 | Amount is at least `1000000` minor units | `declined` | `amount_limit_exceeded` | `403 Forbidden` |
| 3 | The client scope already has at least five risk decisions in the preceding minute | `review` | `velocity_limit_reached` | `409 Conflict` |
| 4 | Amount is at least `500000` minor units | `review` | `large_transaction` | `409 Conflict` |
| 5 | No rule above matches | `approved` | No reason | Transaction continues |

With the default velocity limit, the first five requests in a one-minute window
can pass this rule; the sixth request is sent to review. The count includes all
risk decisions for the same `client_scope`, regardless of their decision state.

### Amount examples

| Amount | Default result, assuming no earlier rule matches |
| --- | --- |
| `499999` | Approved |
| `500000` | Risk review |
| `999999` | Risk review |
| `1000000` | Declined |

### Risk scope and behavior

- Deposits and transfers call the risk service before committing.
- Reversals do not call the risk service.
- Settlement confirmation does not call the risk service.
- If the configured risk service is unavailable, the ledger fails closed and
  returns `503 Service Unavailable`.
- A decision is idempotent for the combination of `client_scope` and
  `idempotency_key`. Reusing that pair for a different request produces a
  conflict.
- Risk decisions have a configured expiry timestamp, but the current service
  does not automatically re-evaluate an expired review.
- There is currently no HTTP API or UI workflow for an operator to approve a
  reviewed decision. The review state must not be confused with an approval.

## Compliance triggers

Compliance consumes committed transaction events from the Kafka
`ledger.events` topic. It records activity and evaluates these rules:

| Rule | Trigger | Severity | Applies to |
| --- | --- | --- | --- |
| `single_minor_unit_transaction` | The event's total debit postings equal exactly `1` minor unit | Low | Any committed transaction event |
| `large_transaction` | The event's total debit postings are at least `500000` minor units | High | Any committed transaction event |
| `repeated_transfers` | At least three outgoing transfers from the same debit account occur within five minutes | Medium | Transfer events only |

For repeated transfers, the current event is included in the count. The third
qualifying transfer creates the first alert, and later qualifying transfers can
each create another alert. Duplicate delivery of the same Kafka event does not
create duplicate activity or alerts.

Compliance is observational: it creates an alert after commit and never blocks,
reverses, or pauses a ledger transaction.

To trigger `single_minor_unit_transaction` from the ledger UI, enter `1` in
the amount field and submit a deposit or transfer with a fresh idempotency key.
For USD this is one cent, not one dollar. After Kafka processing, the alert
appears at `/compliance-alerts`. Previously processed events are not re-evaluated.

## Important threshold interaction

The default risk review threshold and compliance large-transaction threshold
are both `500000` minor units. Consequently, a normal deposit or transfer at or
above that amount is placed into risk review before it commits, so no compliance
event exists yet and no large-transaction compliance alert is generated.

A `large_transaction` compliance alert can still be produced by:

- a settlement, because settlement confirmation bypasses risk;
- a reversal of a previously committed large transaction;
- raising or disabling the risk review threshold while retaining the compliance
  threshold; or
- publishing a valid committed transaction event to Kafka during controlled
  integration testing.

## Example scenarios

| Scenario | Risk outcome | Compliance outcome |
| --- | --- | --- |
| Transfer of `10000` minor units | Approved | No alert unless it satisfies repeated-transfer velocity |
| Third small transfer from one account within five minutes | Approved, unless risk client velocity is reached | Medium `repeated_transfers` alert |
| Transfer of `500000` minor units | Review; transaction is not committed | No alert |
| Transfer of `1000000` minor units | Declined; transaction is not committed | No alert |
| Transfer involving owner `blocked-alice` | Declined; transaction is not committed | No alert |
| Settlement with debit total `500000` | Risk is bypassed | High `large_transaction` alert |

## Viewing results locally

Create deposits and transfers through the ledger API using the usual API key
and a fresh idempotency key. Risk review and decline responses are returned by
the ledger request itself.

List generated compliance alerts:

```powershell
Invoke-RestMethod http://localhost:8002/compliance-alerts?limit=100
```

List events that failed compliance processing after all retries:

```powershell
Invoke-RestMethod http://localhost:8002/dead-letter-events?limit=100
```

A dead-letter event indicates an infrastructure or processing failure. It is
not a business compliance alert.

## Configuration

| Environment variable | Default | Effect |
| --- | --- | --- |
| `RISK_REVIEW_AMOUNT_MINOR` | `500000` | Minimum amount sent to review |
| `RISK_DECLINE_AMOUNT_MINOR` | `1000000` | Minimum amount declined |
| `RISK_VELOCITY_LIMIT_PER_MINUTE` | `5` | Prior decisions allowed per client scope before review |
| `RISK_DECISION_TTL_SECONDS` | `300` | Stored decision expiry interval |
| `COMPLIANCE_LARGE_TRANSACTION_MINOR` | `500000` | Minimum debit total for a high-severity alert |
| `COMPLIANCE_REPEATED_TRANSFER_COUNT` | `3` | Transfer count needed for a medium-severity alert |
| `COMPLIANCE_REPEATED_TRANSFER_WINDOW_MINUTES` | `5` | Repeated-transfer time window |

The services use these defaults when an environment variable is not supplied.
Docker Compose explicitly supplies the three risk threshold values and runs the
supporting local PostgreSQL and Kafka infrastructure.
